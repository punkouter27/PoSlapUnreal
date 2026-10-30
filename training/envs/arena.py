"""Self-play env on MuJoCo Warp (L4 Duel): N worlds of the arena, two athletes each, one brain driving both.

Agents are (world, athlete) pairs flattened to 2N rows: row 2w is red, 2w+1 blue. Every episode is one turn: a coin toss
picks the striker (role +1, BRAIN_IO's striker inputs, the defender's head as target); the other defends (role -1: the
striker's head and right hand). The turn ends 5 s after the striker first touches the defender's head (the recovery window),
at 30 s, or on any knockdown / foot-box exit. KO = the defender knocked down after that touch.
Same observation, action rule, timing and randomisation as envs/slap.py; the model is the arena Unreal runs.
Memory: each world's turns follow on like a match's (a new match 1 in 6), so the opponent's last strike (BRAIN_IO 126-127)
is real. Feints: the striker earns a bonus when the defender's head had already moved (flinched, committed) at the touch.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import mujoco
import mujoco_warp as mjw
import torch
import warp as wp

from constants import GAZE_GAIN, GAZE_MAX_STEP, GAZE_ON, LAGS, MIN_NECK, MIN_STAMINA, OBS, REACT, WATCH_MAX_STEP
from envs.slap import DECIM, WAIT, hill, quat_mat
from levels import LEVELS

HERE = Path(__file__).resolve().parents[1]
PREFIX = ("red_", "blue_")
KICK_STEPS = 2      # the slap's impact impulse lasts 10 ms (match.py)
KO_WINDOW = 250     # control steps after the first head touch: the match's 5 s recovery window (match.py)


class ArenaEnv:
    def __init__(self, character="grandpa", level="L4", num_envs=4096, seed=0, randomize=True):
        # character: one name (both corners) or (red, blue) for a mixed pairing (<red>_vs_<blue>_arena.xml); each corner
        # then keeps its own stand pose, joint ranges and speeds, start height and slap mass
        self.level, self.cfg, self.W, self.randomize = level, LEVELS[level], num_envs, randomize
        self.N = 2 * num_envs          # agents
        dev = self.device = "cuda"
        torch.manual_seed(seed)
        wp.set_device("cuda:0")
        self.stream = torch.cuda.Stream()
        torch.cuda.set_stream(self.stream)
        wp.set_stream(wp.stream_from_torch(self.stream))

        self.chars = (character, character) if isinstance(character, str) else tuple(character)
        fps = [json.loads((HERE / "models" / f"{c}_fingerprint.json").read_text()) for c in self.chars]
        m = self.m = mujoco.MjModel.from_xml_path(str(HERE / "models" / f"{self.chars[0]}_vs_{self.chars[1]}_arena.xml"))
        order = fps[0]["joint_order"]
        assert fps[1]["joint_order"] == order, "both corners need the same joint order"
        self.A = len(order)
        t = lambda xs: torch.tensor(xs, device=dev)
        jid = [[m.joint(p + n).id for n in order] for p in PREFIX]
        self.jq = t([[m.jnt_qposadr[j] for j in js] for js in jid])            # (2, A)
        self.jv = t([[m.jnt_dofadr[j] for j in js] for js in jid])
        self.act_id = t([[m.actuator(p + n).id for n in order] for p in PREFIX])
        self.neck_act = t([[m.actuator(p + n).id for n in order if n.startswith("neck")] for p in PREFIX])   # (2, 3)
        self.rq = t([m.jnt_qposadr[m.joint(p + "root").id] for p in PREFIX])  # (2,)
        self.rv = t([m.jnt_dofadr[m.joint(p + "root").id] for p in PREFIX])
        self.pelvis = t([m.body(p + "pelvis").id for p in PREFIX])
        self.chest = t([m.body(p + "chest").id for p in PREFIX])
        self.head_g = t([m.geom(p + "head").id for p in PREFIX])
        self.hand_g = t([m.geom(p + "hand_r").id for p in PREFIX])
        self.head_b = t([m.geom_bodyid[m.geom(p + "head").id] for p in PREFIX])
        # the slap's impact (match.py): the striker's card mass behind the palm, as an impulse on the head after the touch
        self.strike_mass = t([json.loads((HERE / "characters" / f"{c}.json").read_text())["strike_mass"] for c in self.chars])   # (2,)
        self.feet = t([[m.geom(p + "foot_l").id, m.geom(p + "foot_r").id] for p in PREFIX])
        self.stand = t([[fp["stand_pose_rad"].get(n, 0.0) for n in order] for fp in fps])          # (2 corners, A)
        rng = t([fp["ctrl_range_rad"] for fp in fps])                                             # (2, A, 2)
        self.lo, self.hi = rng[..., 0], rng[..., 1]
        per_joint = lambda v: v if isinstance(v, list) else [v] * len(order)   # a scalar in older fingerprints
        vmax = t([per_joint(fp["max_joint_speed_rad_s"]) for fp in fps])   # (2, A): Hill's top speed per joint
        self.max_step = vmax * 0.02   # per joint
        self.h_act, self.h_dof, self.h_vmax = self.act_id.flatten(), self.jv.flatten(), vmax.flatten()
        # per agent row (row 2w + k is corner k): the action rule's bounds
        rows = lambda x: x.repeat(num_envs, 1)
        self.stand_n, self.lo_n, self.hi_n, self.ms_n = rows(self.stand), rows(self.lo), rows(self.hi), rows(self.max_step)
        self.T = 1500

        d0 = mujoco.MjData(m)
        for k in range(2):
            d0.qpos[self.jq[k].cpu().numpy()] = self.stand[k].cpu().numpy()
        mujoco.mj_forward(m, d0)
        self.q0 = torch.tensor(d0.qpos, device=dev, dtype=torch.float32)
        self.h0 = self.q0[self.rq + 2]   # (2,) each corner's start pelvis height
        self.mw = mjw.put_model(m)
        self.dw = mjw.put_data(m, d0, nworld=num_envs, naconmax=num_envs * 48, njmax=512)

        def per_world(name):
            a = getattr(self.mw, name)
            x = wp.to_torch(a)
            x = x.repeat(num_envs, *[1] * (x.dim() - 1)).contiguous()
            setattr(self.mw, name, wp.from_torch(x, dtype=a.dtype))
            return x
        self.friction, self.mass, self.inertia = per_world("geom_friction"), per_world("body_mass"), per_world("body_inertia")
        self.gain, self.bias, self.frange = per_world("actuator_gainprm"), per_world("actuator_biasprm"), per_world("actuator_forcerange")
        self.mass0, self.inertia0 = self.mass[0].clone(), self.inertia[0].clone()
        self.gain0, self.bias0, self.frange0 = self.gain[0].clone(), self.bias[0].clone(), self.frange[0].clone()
        self.floor = m.geom("floor").id

        T = lambda a: wp.to_torch(a)
        self.qpos, self.qvel, self.ctrl = T(self.dw.qpos), T(self.dw.qvel), T(self.dw.ctrl)
        self.xpos, self.com = T(self.dw.xpos), T(self.dw.subtree_com)
        self.xmat = T(self.dw.xmat)   # the body frames (the gaze servo projects into the chest frame)
        self.geom_xpos, self.geom_xmat = T(self.dw.geom_xpos), T(self.dw.geom_xmat)
        self.con_geom, self.con_world, self.nacon = T(self.dw.contact.geom), T(self.dw.contact.worldid), T(self.dw.nacon)
        self.con_frame = T(self.dw.contact.frame)
        self.xfrc, self.xipos = T(self.dw.xfrc_applied), T(self.dw.xipos)
        self.mact = T(self.dw.act)   # the muscles' filtered targets (build_body MUSCLE_TC)
        self.act0 = torch.zeros(m.nu, device=dev)
        for k in range(2):
            self.act0[self.act_id[k]] = self.stand[k]
        # geom -> athlete (0 red, 1 blue, -1 world); knockdown = a non-foot geom of an athlete on floor or podium
        owner = [-1] * m.ngeom
        for g in range(m.ngeom):
            root = m.body_rootid[m.geom_bodyid[g]]
            owner[g] = 0 if root == self.pelvis[0].item() else 1 if root == self.pelvis[1].item() else -1
        self.owner = t(owner)
        self.bad = (self.owner >= 0)
        self.bad[self.feet.flatten()] = False
        self.surface = torch.zeros(m.ngeom, dtype=torch.bool, device=dev)
        self.surface[self.floor] = True
        self.surface[m.geom("podium").id] = True
        # the defender may brace with his hands on the podium (user, 2026-10-22): palms 7 cm in from his edge, 17 cm apart
        self.podium = m.geom("podium").id
        self.hand_ids = t([[m.geom(p + "hand_l").id, m.geom(p + "hand_r").id] for p in PREFIX])   # (2 athletes, 2 hands)
        self.is_hand = torch.zeros(m.ngeom, dtype=torch.bool, device=dev)
        self.is_hand[self.hand_ids.flatten()] = True
        pg = m.geom("podium")
        top, half = float(pg.pos[2] + pg.size[2]), float(pg.size[0])
        self.brace_pt = t([[[sx * (half + 0.07), y, top + 0.02] for y in (0.17, -0.17)] for sx in (-1, 1)])   # red at -X, blue at +X
        self.podium_half = half
        self.belly_g = t([m.geom(p + "belly").id for p in PREFIX])
        self.belly_r = t([float(m.geom(p + "belly").size[0]) for p in PREFIX])

        W, N, A = num_envs, self.N, self.A
        z = lambda *s: torch.zeros(*s, device=dev)
        self.striker = torch.zeros(W, dtype=torch.long, device=dev)
        self.goal, self.prev_action, self.prev2_action = self.stand_n.clone(), z(N, A), z(N, A)
        self.t = torch.zeros(W, dtype=torch.long, device=dev)
        self.delay = torch.zeros(W, dtype=torch.long, device=dev)
        self.ret, self.left_box = z(N), torch.zeros(N, dtype=torch.bool, device=dev)
        self.foot0, self.head0 = z(W, 2, 2, 2), z(W, 2, 3)
        self.hit_v = float(self.cfg.get("hit_speed", 8.0))
        self.episodes = []
        self.struck, self.hit_ok = torch.zeros(W, dtype=torch.bool, device=dev), torch.zeros(W, dtype=torch.bool, device=dev)
        self.t_hit, self.hit_speed, self.bonus = torch.zeros(W, dtype=torch.long, device=dev), z(W), z(W)
        self.hand_prev, self.hand_prev_ctrl, self.phi_prev = z(W, 3), z(W, 3), z(W)
        self.com0, self.com_err = z(W, 2), z(W)
        self.kick, self.kick_left = z(W, 6), torch.zeros(W, dtype=torch.long, device=dev)
        self.wid = torch.arange(W, device=dev)
        self.force_striker = None   # exams: None = coin toss
        self.stamina, self.neck = torch.ones(W, 2, device=dev), torch.ones(W, 2, device=dev)
        self.hist, self.fresh, self.mem = z(N, 16, 6), torch.zeros(W, dtype=torch.bool, device=dev), z(W, 2, 2)
        self.hand_seen = z(W, REACT + 1, 2, 3)   # both right hands, oldest first: the defender sees [:, 0] (0.12 s late)

        # The eye-contact servo (match.py gaze_desire/gaze_walk/gaze_apply/strike_hold, ported to the batch):
        # each athlete keeps a WORLD-space gaze bias that walks toward the opponent's eye line; the driven
        # athlete's neck yaw goal is set from that bias. The brain keeps every other joint (see the QA note in
        # constants.py: an imposed pose hold tripled the self-knockdowns).
        self.i_yaw = order.index("neck_turn")          # the neck's yaw motor (the nod stays the brain's)
        self.lo_yaw, self.hi_yaw = self.lo[:, self.i_yaw], self.hi[:, self.i_yaw]   # (2,) the range per corner
        self.wbias = z(W, 2, 3)                        # the world bias, per athlete
        self.gaze_off = z(W, 2)                        # |the servo's yaw - the brain's own neck goal| (rad), applied
        self.gaze_bias = z(W, 2)                       # the same, always computed: what the servo would do
        self.gaze_step = z(W)                          # the walk's step (rad per decision): watch faster after the touch
        # How many worlds train WITHOUT the servo (SLAP_SERVO_MIX, 0 = the match's world: the servo is always on).
        # A brain that only ever sees the servo-owned neck learns to depend on it: with the servo off it falls
        # apart (the 2026-10-29 retrain: 60/120 strikes with the servo on, 18/109 and 45 % self-falls with it
        # off). Mixing the two worlds in training is what makes a brain servo-robust.
        self.servo_mix = float(os.environ.get("SLAP_SERVO_MIX", "0.0"))
        self.servo_on = torch.ones(W, dtype=torch.bool, device=dev)

        mjw.step(self.mw, self.dw); mjw.kinematics(self.mw, self.dw); wp.synchronize()
        with wp.ScopedCapture() as c:
            mjw.step(self.mw, self.dw)
        self.g_step = c.graph
        with wp.ScopedCapture() as c:
            mjw.kinematics(self.mw, self.dw)
        self.g_kin = c.graph
        self.reset(torch.ones(W, dtype=torch.bool, device=dev))

    # ------------------------------------------------------------------ helpers
    def _u(self, n, lo, hi):
        return lo + torch.rand(n, device=self.device) * (hi - lo)

    def _pair(self, x):   # (W, 2, ...) -> (2W, ...) agent rows
        return x.reshape(self.N, *x.shape[2:])

    def _role(self):      # (W, 2): +1 striker, -1 defender
        k = torch.arange(2, device=self.device)
        return torch.where(k[None, :] == self.striker[:, None], 1.0, -1.0)

    def _sd(self, x):     # per-athlete (W, 2, ...) -> (striker's, defender's)
        w = torch.arange(self.W, device=self.device)
        return x[w, self.striker], x[w, 1 - self.striker]

    # ------------------------------------------------------------------ reset
    def reset(self, mask):
        n = int(mask.sum())
        if n == 0:
            return
        mjw.reset_data(self.mw, self.dw, reset=wp.from_torch(mask))
        q = self.q0.repeat(n, 1)
        for k in range(2):
            q[:, self.jq[k]] += self._u((n, self.A), -0.05, 0.05)
        self.qpos[mask], self.qvel[mask] = q, 0.0
        self.mact[mask] = self.act0   # muscles settled at the stand targets (reset zeroes act)
        self.kick_left[mask] = 0
        if self.randomize:
            self.friction[mask, self.floor, 0] = self._u(n, 0.6, 1.2)
            for fk in self.feet.flatten():
                self.friction[mask, int(fk), 0] = self._u(n, 0.6, 1.2)
            s = self._u((n, self.mass.shape[1]), 0.95, 1.05)
            self.mass[mask], self.inertia[mask] = self.mass0 * s, self.inertia0 * s[..., None]
            k = self._u((n, self.gain.shape[1]), 0.9, 1.1)
            tired, hurt = torch.rand(n, 2, device=self.device) < 0.7, torch.rand(n, 2, device=self.device) < 0.7
            self.stamina[mask] = torch.where(tired, self._u((n, 2), MIN_STAMINA, 1.0), 1.0)
            self.neck[mask] = torch.where(hurt, self._u((n, 2), MIN_NECK, 1.0), 1.0)
            fr = self.frange0 * k[..., None]
            for a in range(2):
                fr[:, self.neck_act[a]] *= self.neck[mask][:, a, None, None]
            self.gain[mask], self.bias[mask], self.frange[mask] = self.gain0 * k[..., None], self.bias0 * k[..., None], fr
            self.delay[mask] = torch.randint(0, 5, (n,), device=self.device)
        self.mem[mask & (torch.rand(self.W, device=self.device) < 1 / 6)] = 0.0   # a new match
        self.fresh |= mask
        self.striker[mask] = torch.randint(0, 2, (n,), device=self.device) if self.force_striker is None else self.force_striker
        am = mask.repeat_interleave(2)
        self.goal[am], self.prev_action[am], self.prev2_action[am], self.ret[am], self.left_box[am] = self.stand_n[am], 0.0, 0.0, 0.0, False
        self.t[mask] = 0
        wp.capture_launch(self.g_kin)
        self.foot0[mask] = self.geom_xpos[mask][:, self.feet, :2]
        self.head0[mask] = self.geom_xpos[mask][:, self.head_g]
        self.struck[mask], self.hit_ok[mask], self.hit_speed[mask], self.bonus[mask] = False, False, 0.0, 0.0
        hs, ht = self._sd(self.geom_xpos[:, self.hand_g]), self._sd(self.geom_xpos[:, self.head_g])
        self.hand_prev[mask], self.hand_prev_ctrl[mask] = hs[0][mask], hs[0][mask]
        self.phi_prev[mask] = self._palm_phi(hs[0], ht[1])[mask]
        self.com_err[mask] = 0.0
        # the gaze bias starts on the eye line (match.py: the bias is reseeded at every turn start)
        eye = self._gaze_desire()
        self.wbias[mask] = eye[mask]
        self.gaze_off[mask] = 0.0
        # the servo is on in every world by default (the match's world); SLAP_SERVO_MIX trains a share without it
        self.servo_on[mask] = torch.rand(n, device=self.device) >= self.servo_mix

    # ------------------------------------------------------------------ the eye-contact servo
    def _gaze_desire(self):
        """The unit world direction from each athlete's eyes to the opponent's eyes (the eye line)."""
        head = self.geom_xpos[:, self.head_g]                       # (W, 2, 3)
        u = head[:, [1, 0], :] - head                               # the opponent's eyes - mine
        return u / u.norm(dim=-1, keepdim=True).clamp(min=1e-6)

    def _gaze(self, tgt2):
        """The eye-contact servo, ported from match.py (gaze_desire / gaze_walk / gaze_apply / strike_hold).

        Before the touch the DEFENDER squares up to the striker; after it the STRIKER watches the defender
        (WATCH_MAX_STEP: fast enough to re-acquire a snapping head).  Gaze only: the brain keeps every other
        joint - the imposed post-slap pose hold tripled the self-knockdowns (see constants.py).

        The bias is WORLD space: the chest whips through a slap's follow-through and a chest-relative bias would
        chase a target spinning hundreds of degrees per second, while the world bias keeps the head glued to the
        opponent and lets the neck quietly counter-rotate the torso.  It walks by at most `step` radians on the
        great circle: never a teleport.

        `tgt2` is the (W, 2, A) view of the motor goals; the neck yaw goal of the driven athlete is overwritten
        in place, exactly as match.py does it after the decision.  The observation is untouched: the servo moves
        motors, not what the brain sees.
        """
        W = self.W
        eye = self._gaze_desire()                                  # (W, 2, 3)
        # the walk: the world bias toward the eye line, at most `step` rad on the great circle
        bw = self.wbias
        c = (bw * eye).sum(-1).clamp(-1.0, 1.0)
        theta = torch.acos(c)
        p = eye - c[..., None] * bw
        pn = p.norm(dim=-1)
        p = torch.where((pn < 1e-9)[..., None], eye, p / pn.clamp(min=1e-9)[..., None])
        cs, sn = torch.cos(self.gaze_step)[:, None, None], torch.sin(self.gaze_step)[:, None, None]
        v = bw * cs + p * sn
        v = v / v.norm(dim=-1, keepdim=True).clamp(min=1e-6)
        self.wbias = torch.where((theta <= self.gaze_step[:, None])[..., None], eye, v)
        # the goal: the world bias projected into the chest frame (the neck's hinges hang off the chest)
        Rc = self.xmat[:, self.chest].reshape(W, 2, 3, 3)          # body -> world
        u = (Rc.transpose(-1, -2) @ self.wbias[..., None]).squeeze(-1)
        yaw = torch.atan2(u[..., 1], u[..., 0])
        lo, hi = self.lo_yaw, self.hi_yaw
        want = torch.clamp(yaw, lo, hi)
        blend = torch.clamp((1.0 - GAZE_GAIN) * tgt2[..., self.i_yaw] + GAZE_GAIN * want, lo, hi)
        # only the driven athlete's row is written: the defender until the touch, the striker after it
        driven = torch.where(self.struck, self.striker, 1 - self.striker)       # (W,)
        corner = torch.arange(2, device=self.device)
        drive = (corner[None, :] == driven[:, None]) & self.servo_on[:, None]    # (W, 2)
        own = tgt2[..., self.i_yaw]
        off = torch.where(drive, (blend - own).abs(), torch.zeros_like(own))
        self.gaze_bias = off          # what the servo WOULD do (always computed: the check reads it)
        if not GAZE_ON:
            return                   # SLAP_EYE_CONTACT=0: the brain keeps the neck
        self.gaze_off = off
        tgt2[..., self.i_yaw] = torch.where(drive, blend, own)

    # ------------------------------------------------------------------ step
    def step(self, action):
        a = action.clamp(-1.0, 1.0)
        if self.randomize:
            effort = (a.view(self.W, 2, -1) ** 2).mean(dim=-1)
            delta_stam = torch.where(effort > 0.20, -0.00016 * effort, 0.0003 * (1.0 - effort))
            self.stamina = (self.stamina + delta_stam).clamp(MIN_STAMINA, 1.0)
        st, lo, hi = self.stand_n, self.lo_n, self.hi_n
        tgt = torch.clamp(st + torch.where(a > 0, a * (hi - st), a * (st - lo)), lo, hi)
        ms = self.ms_n * self.stamina.reshape(-1, 1)          # fatigue: a tired athlete moves slower
        tgt = torch.max(torch.min(tgt, self.goal + ms), self.goal - ms)
        old, self.goal = self.goal, tgt
        tw, ow = tgt.view(self.W, 2, self.A), old.view(self.W, 2, self.A)
        # the walk's step: the defender squares up slowly, the planted striker re-acquires fast (match.py)
        self.gaze_step = torch.where(self.struck, torch.full_like(self.gaze_step, WATCH_MAX_STEP),
                                     torch.full_like(self.gaze_step, GAZE_MAX_STEP))
        self._gaze(tw)          # the eye-contact servo writes the neck yaw goal (match.py: after the decision)
        for s in range(DECIM):
            on = (s >= self.delay)[:, None, None]
            u = torch.where(on, tw, ow)
            for k in range(2):
                self.ctrl[:, self.act_id[k]] = u[:, k]
            self.xfrc[:, self.head_b] = 0.0
            self.xfrc[self.wid, self.head_b[1 - self.striker]] = self.kick * (self.kick_left > 0)[:, None]
            self.kick_left = (self.kick_left - 1).clamp(min=0)
            fr = self.frange[:, self.h_act].clone()   # muscles: Hill's force-velocity cap for this step only
            self.frange[:, self.h_act] = hill(fr, self.qvel[:, self.h_dof], self.h_vmax)
            wp.capture_launch(self.g_step)
            self.frange[:, self.h_act] = fr
            self._strike_check()
        wp.capture_launch(self.g_kin)

        fell = self.fell_now = self._fallen()                  # (W, 2)
        rew, out = self._reward(a, fell)                       # (2W,), (W, 2)
        self.prev2_action = self.prev_action
        self.prev_action = a
        self.t += 1
        self.ret += rew
        role = self._role()
        s_fell, d_fell = self._sd(fell)
        ko = self.struck & d_fell
        stop = fell.any(-1) | out.any(-1)
        done = stop | (self.t >= self.T) | (self.struck & (self.t >= self.t_hit + KO_WINDOW))
        timeout = done & ~stop
        self.ko_now, self.self_fall_now = done & ko, done & s_fell
        if done.any():
            w = (done & self.struck).nonzero().squeeze(-1)    # the striker's strike, remembered by his next opponent
            self.mem[w, self.striker[w]] = torch.stack([self.t_hit[w].float() / self.T, self.hit_speed[w] / 10.0], -1)
            dd = done.repeat_interleave(2)
            cols = (self.ret[dd], self.t.repeat_interleave(2)[dd], self._pair(fell)[dd], self._pair(out)[dd],
                    self.hit_ok.repeat_interleave(2)[dd], self.hit_speed.repeat_interleave(2)[dd], self._pair(role)[dd],
                    ko.repeat_interleave(2)[dd], self.struck.repeat_interleave(2)[dd], (torch.arange(self.N, device=self.device) % 2)[dd])
            for r, l, f, b, h, v, ro, k, st, sd in zip(*(c.tolist() for c in cols)):
                self.episodes.append(dict(ret=r, len=l, fell=f, left_box=b, hit=h, speed=v, role=int(ro), ko=k, struck=st,
                                          absorbed=(not f) if ro < 0 else False, com_err=0.0, side=sd))
            self.reset(done)
        return self.observe(), rew, done.repeat_interleave(2), timeout.repeat_interleave(2)

    def _palm_phi(self, hand, tgt):
        to = tgt - hand
        d = to.norm(dim=-1)
        palm_dir = -self._sd(self.geom_xmat[:, self.hand_g])[0][:, :, 2]
        face = ((to / d[:, None].clamp(min=1e-6)) * palm_dir).sum(-1).clamp(min=0)
        return 2.0 * torch.exp(-d ** 2 / 0.08) * face

    def _strike_check(self):
        """After each physics step: the striker's first touch on the defender's head decides the strike."""
        hand = self._sd(self.geom_xpos[:, self.hand_g])[0]
        vel = (hand - self.hand_prev) / 0.005
        speed = vel.norm(dim=-1)
        self.hand_prev = hand.clone()
        n = self.nacon[0]
        idx = torch.arange(self.con_geom.shape[0], device=self.device)
        g0, g1 = self.con_geom[:, 0].long(), self.con_geom[:, 1].long()
        w = self.con_world.long().clamp(0, self.W - 1)                # slots past nacon hold stale ids
        dhead = self.head_g[1 - self.striker[w]]                        # the defender's head in that contact's world
        on = (idx < n) & ((g0 == dhead) | (g1 == dhead))
        other = torch.where(g0 == dhead, g1, g0)
        on &= self.owner[other] == self.striker[w]                     # touched by the striker's body
        if not bool(on.any()):
            return
        w = w[on]
        hit = torch.zeros(self.W, dtype=torch.bool, device=self.device)
        hit[w] = True
        early = self.t < WAIT                                # the 4 s stand (envs/slap.py WAIT): an early touch is a foul
        self.bonus[hit & early & ~self.struck] -= 0.5
        self.bonus[hit & self.struck & (self.t > self.t_hit + 15)] -= 1.0   # one slap per turn: a second touch costs
        new = hit & ~self.struck & ~early
        if bool(new.any()):
            v = speed[new]
            clean = v >= self.hit_v                          # any hit counts (user 2026-10-25): the palm is technique, not validity
            self.hit_ok[new], self.hit_speed[new] = clean, v
            self.bonus[new] += torch.where(clean, 50.0 + 8.0 * v, 3.0 * v + v * v)
            dh = self._sd(self.geom_xpos[:, self.head_g])[1][new] - self._sd(self.head0)[1][new]   # feint: he had flinched
            self.bonus[new] += 100.0 * (dh[:, :2].norm(dim=-1) - 0.03).clamp(0, 0.15)
            f = vel[new] * (self.strike_mass[self.striker[new]] / (KICK_STEPS * 0.005))[:, None]
            r = hand[new] - self.xipos[new.nonzero().squeeze(-1), self.head_b[1 - self.striker[new]]]
            self.kick[new], self.kick_left[new] = torch.cat([f, torch.cross(r, f, dim=-1)], -1), KICK_STEPS
            self.struck |= new
            self.t_hit[new] = self.t[new]
            self.com0[new] = self._sd(self.com[:, self.pelvis, :2])[1][new]

    def _fallen(self):
        n = self.nacon[0]
        idx = torch.arange(self.con_geom.shape[0], device=self.device)
        g0, g1 = self.con_geom[:, 0].long(), self.con_geom[:, 1].long()
        w = self.con_world.long().clamp(0, self.W - 1)
        defender = 1 - self.striker[w]                                   # per contact: who defends in its world
        down = (idx < n) & self.surface[g0] & self.bad[g1] & ~((g0 == self.podium) & self.is_hand[g1] & (self.owner[g1] == defender))
        down2 = (idx < n) & self.surface[g1] & self.bad[g0] & ~((g1 == self.podium) & self.is_hand[g0] & (self.owner[g0] == defender))
        fell = torch.zeros(self.W, 2, dtype=torch.bool, device=self.device)
        fell[w[down], self.owner[g1[down]]] = True
        fell[w[down2], self.owner[g0[down2]]] = True
        return fell | (self.qpos[:, self.rq + 2] < 0.45)

    def _reward(self, a, fell):
        W = self.W
        q = self.qpos
        quat = torch.stack([q[:, self.rq[k] + 3:self.rq[k] + 7] for k in range(2)], 1)    # (W, 2, 4)
        R = quat_mat(quat.reshape(-1, 4)).view(W, 2, 3, 3)
        upright = R[..., 2, 2]
        z = q[:, self.rq + 2]
        height = torch.exp(-((z - self.h0) ** 2) / 0.01)
        jp = torch.stack([q[:, self.jq[k]] for k in range(2)], 1)
        posture = torch.exp(-((jp - self.stand) ** 2).sum(-1) / 1.0)
        a2, p2, p2_prev = a.view(W, 2, -1), self.prev_action.view(W, 2, -1), self.prev2_action.view(W, 2, -1)
        smooth = ((a2 - p2) ** 2).sum(-1)
        jerk = ((a2 - 2.0 * p2 + p2_prev) ** 2).sum(-1)
        r = 1.0 + 0.5 * upright.clamp(min=0) + 0.5 * height + 0.3 * posture - 0.01 * smooth - 0.002 * jerk
        bx, by = self.cfg["foot_box"]
        d = (self.geom_xpos[:, self.feet, :2] - self.foot0).abs()                       # (W, 2, 2 feet, 2)
        out = (d[..., 0] > bx).any(-1) | (d[..., 1] > by).any(-1)
        self.left_box |= self._pair(out)
        facing = R[..., 0, 0] * torch.tensor([1.0, -1.0], device=self.device)          # red faces +X, blue -X
        head = self.geom_xpos[:, self.head_g]
        hdev = (head[..., :2] - self.head0[..., :2]).norm(dim=-1)
        anchor = torch.exp(-(d ** 2).sum((-1, -2)) / 0.005)
        r = r + 0.3 * facing + 0.5 * anchor + 0.3 * torch.exp(-hdev ** 2 / 0.01)

        # defender (absorb): after the touch, the COM back over its pre-touch spot; before it, no flinching (head > 5 cm)
        role = self._role()
        dcom = self._sd(self.com[:, self.pelvis, :2])[1]
        self.com_err = (dcom - self.com0).norm(dim=-1)
        after = self.struck
        flinch = (hdev - 0.05).clamp(min=0) * (~after)[:, None]
        r_def = r + torch.where(after, torch.exp(-self.com_err ** 2 / 0.0025), torch.zeros_like(self.com_err))[:, None] - 20.0 * flinch
        hands = self.geom_xpos[:, self.hand_ids]                         # (W, 2 athletes, 2 hands, 3): the brace
        r_def = r_def + 0.4 * torch.exp(-((hands - self.brace_pt) ** 2).sum(-1) / 0.01).sum(-1)

        # striker: as envs/slap.py strike (approach + palm-facing shaping before the touch, hit bonus at it), + KO bonus
        hand, tgt = self._sd(self.geom_xpos[:, self.hand_g])[0], self._sd(head)[1]
        dist = (tgt - hand).norm(dim=-1)
        d_prev = (tgt - self.hand_prev_ctrl).norm(dim=-1)
        phi = self._palm_phi(hand, tgt)
        su = self._sd(upright)[0]
        ss = self._sd(smooth)[0]
        go = (self.t >= WAIT).float()   # the 4 s stand first
        before = go * (-0.05 + 30.0 * (d_prev - dist) + 5.0 * (phi - self.phi_prev)) + 0.04 * su.clamp(min=0) - 0.01 * ss
        self.phi_prev = phi
        self.hand_prev_ctrl = hand.clone()
        ko = self.struck & self._sd(fell)[1] & self.hit_ok
        # stick the landing (user 2026-10-25: "focus on not falling down after slapping"): after the touch the
        # striker earns for REMAINING UPRIGHT through the recovery window - flat zeros gave no signal to ride
        # out the recoil HE triggers (0.5/step ~ the value of the hit itself over a calm 5 s recovery)
        land = 0.5 * su.clamp(min=0)
        # ...and off the podium (2026-10-29: all of grandpa_L4's 12 % exam self-falls were his belly leaning onto it
        # 0.5-2 s after the touch, still standing): from 8 cm of clearance (11.9 at stand) to touching costs up to 1/step
        gap = self._sd(self.geom_xpos[:, self.belly_g, 0])[0].abs() - self.podium_half - self.belly_r[self.striker]
        land = land - ((0.08 - gap) / 0.08).clamp(0.0, 1.0)
        r_str = torch.where(self.struck, land, before) + self.bonus + 40.0 * ko.float()
        r_str = r_str - 20.0 * ((self.t + 1 >= self.T) & ~self.struck).float()
        self.bonus.zero_()

        rr = torch.where(role > 0, r_str[:, None].expand(-1, 2), r_def)
        # a striker's self-fall USED to cost -30 against a +146 clean hit: tumbling after the smack was
        # profitable, and the champions duly learned to flail (5 self-downs + 1 box-crossing per 10 matches).
        # -150 cancels a clean hit outright; -60 makes the box expensive too (defender terms unchanged)
        penalty = torch.where(role > 0, torch.where(fell, -150.0, -60.0), -5.0)
        rr = torch.where(fell | out, penalty, rr)
        return self._pair(rr), out

    # ------------------------------------------------------------------ observe (BRAIN_IO)
    def observe(self):
        W, q, v = self.W, self.qpos, self.qvel
        role = self._role()
        clock = (1.0 - self.t.float() / self.T)[:, None].expand(-1, 2)
        head, hand = self.geom_xpos[:, self.head_g], self.geom_xpos[:, self.hand_g]
        fresh = self.fresh.repeat_interleave(2)
        self.hand_seen = torch.where(self.fresh[:, None, None, None], hand[:, None].expand(-1, REACT + 1, -1, -1),
                                     torch.cat([self.hand_seen[:, 1:], hand[:, None]], 1))
        self.fresh[:] = False
        rows = []
        for k in range(2):
            rq, rv = self.rq[k], self.rv[k]
            R = quat_mat(q[:, rq + 3:rq + 7])
            yaw = torch.atan2(R[:, 1, 0], R[:, 0, 0])
            c, s = torch.cos(yaw), torch.sin(yaw)
            hd = lambda x: torch.stack([c * x[:, 0] + s * x[:, 1], -s * x[:, 0] + c * x[:, 1], x[:, 2]], -1)
            pel = self.xpos[:, self.pelvis[k]]
            opp_hand = torch.where((role[:, k] < 0)[:, None], self.hand_seen[:, 0, 1 - k] - pel, torch.zeros_like(pel))
            cur = torch.cat([hd(head[:, 1 - k] - pel), hd(opp_hand)], -1)
            rows.append(torch.cat([
                q[:, self.jq[k]] - self.stand[k],
                v[:, self.jv[k]] * 0.1,
                pel[:, 2:3],
                -R[:, 2, :],
                hd(v[:, rv:rv + 3]),
                v[:, rv + 3:rv + 6] * 0.25,
                self.prev_action.view(W, 2, -1)[:, k],
                role[:, k:k + 1], clock[:, k:k + 1],
                cur,                               # the opponent's head centre - own pelvis; defender: the striker's
                                                   # right hand (striker: 0, as L2/L3 trained)
                self.mem[:, 1 - k], self.stamina[:, k:k + 1], self.neck[:, k:k + 1],
                hd(hand[:, k] - pel), hd(-self.geom_xmat[:, self.hand_g[k]][:, :, 2]),   # own palm: centre, facing (130-135)
            ], -1))
        obs = torch.stack(rows, 1).reshape(self.N, -1)   # (2W, 108 + 10): memory history goes in below
        cur = obs[:, 102:108]
        self.hist = torch.where(fresh[:, None, None], cur[:, None].expand(-1, 16, -1), torch.cat([self.hist[:, 1:], cur[:, None]], 1))
        obs = torch.cat([obs[:, :108], *[self.hist[:, -1 - lag] for lag in LAGS], obs[:, 108:]], -1)
        if self.randomize:
            noise = torch.zeros(OBS, device=self.device)
            noise[:30], noise[30:60], noise[61:64] = 0.005, 0.05, 0.01
            obs = obs + torch.randn_like(obs) * noise
        return obs


class VsFrozen:
    """L4 against a fixed opponent: the learner drives its corner of a mixed arena, the opponent's brain (a run's .pt,
    its mean action) the other. Grandpa vs Kim never sparred together (each L4 brain only met its double; 2026-10-24
    Grandpa's fell at 1-2 s every turn on Kim's lower podium), so the two take turns learning against each other.
    Looks like an ArenaEnv with one agent per world (the learner's rows); everything else is the arena's."""
    _own = ("env", "side", "N", "opp", "_full")

    def __init__(self, learner, opponent, opp_ckpt, level="L4", num_envs=4096, seed=0, randomize=True, side=None):
        # side: the learner's corner (0 red, 1 blue); None = red when <learner>_vs_<opponent>_arena.xml exists
        from ppo import PPO, PPOConfig
        if side is None:
            side = 0 if (HERE / "models" / f"{learner}_vs_{opponent}_arena.xml").exists() else 1
        pair = (learner, opponent) if side == 0 else (opponent, learner)
        self.env = ArenaEnv(pair, level, num_envs, seed, randomize)
        self.side, self.N = pair.index(learner), num_envs
        self.opp = PPO(OBS, self.env.A, 1, "cuda", PPOConfig())
        self.opp.load(str(opp_ckpt))
        self._full = self.env.observe()

    def __getattr__(self, k):
        return getattr(self.env, k)

    def __setattr__(self, k, v):
        if k in VsFrozen._own:
            object.__setattr__(self, k, v)
        else:
            setattr(self.env, k, v)

    @property
    def episodes(self):
        return [e for e in self.env.episodes if e["side"] == self.side]

    def observe(self):
        self._full = self.env.observe()
        return self._full[self.side::2]

    @torch.no_grad()
    def step(self, action):
        full = torch.empty(self.env.N, self.env.A, device=self.env.device)
        full[self.side::2] = action
        o = self._full[1 - self.side::2]
        full[1 - self.side::2] = self.opp.model.actor(self.opp.obs_rms.normalize(o, self.opp.cfg.obs_clip))
        obs, rew, done, timeout = self.env.step(full)
        self._full = obs
        k = self.side
        return obs[k::2], rew[k::2], done[k::2], timeout[k::2]

class Field:
    """One brain per player (user 2026-10-30: "3 players, 3 brains"): the learner's seats are its own arena (self-play,
    both corners) and every opponent from BOTH corners (VsFrozen, side forced), so one brain learns every pairing it can
    be put in. Each seat keeps its own arena, CUDA stream and captured Warp graphs; a step runs them one after the other
    on their streams and joins the agents into one batch for PPO. Attribute writes (the curricula) reach every seat."""
    _own = ("seats", "sizes", "names", "stream", "N", "A")

    def __init__(self, learner, opponents, level="L4", num_envs=4096, seed=0, randomize=True):
        # opponents: [(character, checkpoint .pt)]; num_envs worlds in all, split evenly over the seats
        seats, names = [], []
        k = 1 + 2 * len(opponents)
        w = max(64, num_envs // k)
        seats.append(ArenaEnv(learner, level, max(32, w // 2), seed, randomize)); names.append(f"{learner}_self")   # 2 agents a world
        for i, (opp, ckpt) in enumerate(opponents):
            for side in (0, 1):
                seats.append(VsFrozen(learner, opp, ckpt, level, w, seed + 1 + 2 * i + side, randomize, side=side))
                names.append(f"{learner}_{'red' if side == 0 else 'blue'}_vs_{opp}")
        object.__setattr__(self, "seats", seats)
        object.__setattr__(self, "names", names)
        object.__setattr__(self, "sizes", [s.N for s in seats])
        object.__setattr__(self, "N", sum(s.N for s in seats))
        object.__setattr__(self, "A", seats[0].A)
        object.__setattr__(self, "stream", torch.cuda.Stream())
        self._use(self.stream)

    @staticmethod
    def _use(stream):
        torch.cuda.set_stream(stream)
        wp.set_stream(wp.stream_from_torch(stream))

    def __getattr__(self, k):   # read: the first seat's (T, qpos for telemetry, the curricula's current values)
        return getattr(self.seats[0], k)

    def __setattr__(self, k, v):
        if k in Field._own:
            object.__setattr__(self, k, v)
        else:
            for s in self.seats:
                setattr(s, k, v)

    @property
    def episodes(self):
        return [e for s in self.seats for e in s.episodes]

    @episodes.setter
    def episodes(self, v):
        for s in self.seats:
            s.episodes = []

    def _each(self, fn):
        out = []
        for s in self.seats:
            s.stream.wait_stream(self.stream)   # the trainer's actions are ready before the seat reads them
            self._use(s.stream)
            out.append(fn(s))
        self._use(self.stream)
        for s in self.seats:
            self.stream.wait_stream(s.stream)   # and the seat's results before the trainer reads them
        return out

    def observe(self):
        return torch.cat(self._each(lambda s: s.observe()))

    def reset(self, mask):
        self._each(lambda s: s.reset(torch.ones(s.W if hasattr(s, "W") else s.env.W, dtype=torch.bool, device="cuda")))

    @torch.no_grad()
    def step(self, action):
        parts = iter(torch.split(action, self.sizes))
        outs = self._each(lambda s: s.step(next(parts)))
        return tuple(torch.cat(x) for x in zip(*outs))
