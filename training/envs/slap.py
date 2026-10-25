"""Slap training env on MuJoCo Warp: N worlds of one athlete, on the GPU (levels L0-L3).

Observation and action are DOCS/BRAIN_IO.md exactly; Unreal's USlapController builds the same 130 numbers.
One brain, every role (Blueprint 7): each world plays one role per episode, drawn from the level's `roles` mix, so a
level keeps the earlier ones' skills (2026-10-22: L2 trained on strikes alone had forgotten how to guard).
    0  guard   (L0, L1): stand in the foot box under random pushes, 30 s
    +1 strike  (L2):     hit on the `target` mocap sphere where the opponent's head is (any part of the hand counts,
                         the palm is technique not validity — user 2026-10-25); ends 1.5 s after
    -1 absorb  (L3):     one scripted slap to the face (an impulse on the head, the opponent's hand seen coming);
                         ends 5 s after it: still up, centre of mass back within 5 cm
Randomised every episode (Blueprint 6): floor friction, +-5 % mass per part, +-10 % strength, 0-20 ms action
delay, a little sensor noise; guard worlds get pushes of 150-300 N (x push_scale) on chest or head.
Fatigue (card): 70 % of episodes start tired (stamina 0.7-1 scales joint speed) and/or hurt (neck 0.5-1 scales the neck
motors' strength); both are inputs. Memory: the 0.1-0.3 s history of inputs 102-107, and the opponent's last strike: the
scripted slap keeps its timing from one episode to the next half the time (a habit worth remembering).
A fall is Unreal's knockdown rule: any non-foot geom touching the floor or podium (MuJoCo contacts, not a height guess).
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

import mujoco
import mujoco_warp as mjw
import torch
import warp as wp

from brain_cpu import LAGS, MIN_NECK, MIN_STAMINA, OBS
from levels import LEVELS

HERE = Path(__file__).resolve().parents[1]
DECIM = 4           # physics steps per brain step (200 Hz / 50 Hz)
LEAN = float(os.environ.get("SLAP_LEAN", "1.0"))   # how far a braced defender leans in (see reset), stepped up by hand
SETTLE = 250        # brain steps (5 s) a strike episode runs on after the touch: the match's recovery window; he stays up, slaps once
WAIT = 200          # brain steps (4 s): the striker stands balanced before the wind-up (user, 2026-10-22); earlier touches don't count


def quat_mat(q):    # (N, 4) wxyz -> (N, 3, 3), body -> world
    w, x, y, z = q.unbind(-1)
    return torch.stack([1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y),
                        2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x),
                        2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)], -1).view(-1, 3, 3)


class SlapEnv:
    def __init__(self, character="grandpa", level="L0", num_envs=4096, seed=0, randomize=True):
        if level not in ("L0", "L1", "L2", "L3"):
            raise NotImplementedError(f"{level}: its mechanics are added when the ladder reaches it (levels.py)")
        self.level, self.cfg, self.N, self.randomize = level, LEVELS[level], num_envs, randomize
        dev = self.device = "cuda"
        torch.manual_seed(seed)
        wp.set_device("cuda:0")
        # torch and warp take turns on one stream; not the legacy default one, which CUDA graphs can't capture on
        self.stream = torch.cuda.Stream()
        torch.cuda.set_stream(self.stream)
        wp.set_stream(wp.stream_from_torch(self.stream))

        fp = json.loads((HERE / "models" / f"{character}_fingerprint.json").read_text())
        m = self.m = mujoco.MjModel.from_xml_path(str(HERE / "models" / f"{character}_solo.xml"))
        order = fp["joint_order"]
        self.A = len(order)
        jid = [m.joint(n).id for n in order]
        self.jq = torch.tensor([m.jnt_qposadr[j] for j in jid], device=dev)
        self.jv = torch.tensor([m.jnt_dofadr[j] for j in jid], device=dev)
        self.act_id = torch.tensor([m.actuator(n).id for n in order], device=dev)
        self.neck_act = torch.tensor([m.actuator(n).id for n in order if n.startswith("neck")], device=dev)
        self.stand = torch.tensor([fp["stand_pose_rad"].get(n, 0.0) for n in order], device=dev)
        rng = torch.tensor(fp["ctrl_range_rad"], device=dev)   # listed in joint order
        self.lo, self.hi = rng[:, 0], rng[:, 1]
        self.max_step = torch.tensor(fp["max_joint_speed_rad_s"], device=dev) * 0.02   # per joint (scalar in older fingerprints)
        self.T = 1500   # 30 s turn clock, every role

        d0 = mujoco.MjData(m)
        d0.qpos[self.jq.cpu().numpy()] = self.stand.cpu().numpy()
        mujoco.mj_forward(m, d0)
        self.q0 = torch.tensor(d0.qpos, device=dev, dtype=torch.float32)
        self.h0 = float(d0.qpos[2])
        self.mw = mjw.put_model(m)
        # room as envs/arena.py: at the full lean the striker leans onto target and podium at once; a world past
        # njmax=256 crashed the GPU ("unspecified launch failure", twice in 12 min, 2026-10-23)
        self.dw = mjw.put_data(m, d0, nworld=num_envs, naconmax=num_envs * 48, njmax=512)

        # model fields randomised per world: one row each
        def per_world(name):
            a = getattr(self.mw, name)
            t = wp.to_torch(a)
            t = t.repeat(num_envs, *[1] * (t.dim() - 1)).contiguous()
            setattr(self.mw, name, wp.from_torch(t, dtype=a.dtype))
            return t
        self.friction, self.mass, self.inertia = per_world("geom_friction"), per_world("body_mass"), per_world("body_inertia")
        self.gain, self.bias, self.frange = per_world("actuator_gainprm"), per_world("actuator_biasprm"), per_world("actuator_forcerange")
        self.gpos = per_world("geom_pos")   # the podium's height (a shorter opponent, see reset)
        self.mass0, self.inertia0 = self.mass[0].clone(), self.inertia[0].clone()
        self.gain0, self.bias0, self.frange0 = self.gain[0].clone(), self.bias[0].clone(), self.frange[0].clone()
        self.floor = m.geom("floor").id

        T = lambda a: wp.to_torch(a)
        self.qpos, self.qvel, self.ctrl = T(self.dw.qpos), T(self.dw.qvel), T(self.dw.ctrl)
        self.xpos, self.xfrc, self.com = T(self.dw.xpos), T(self.dw.xfrc_applied), T(self.dw.subtree_com)
        self.geom_xpos, self.geom_xmat = T(self.dw.geom_xpos), T(self.dw.geom_xmat)
        self.con_geom, self.con_world, self.nacon = T(self.dw.contact.geom), T(self.dw.contact.worldid), T(self.dw.nacon)
        self.con_frame, self.mocap = T(self.dw.contact.frame), T(self.dw.mocap_pos)
        feet = [m.geom("foot_l").id, m.geom("foot_r").id]
        self.feet = torch.tensor(feet, device=dev)
        body_geoms = [g for g in range(m.ngeom) if m.body_rootid[m.geom_bodyid[g]] == m.body("pelvis").id]
        self.bad = torch.zeros(m.ngeom, dtype=torch.bool, device=dev)
        self.bad[[g for g in body_geoms if g not in feet]] = True
        self.surface = torch.zeros(m.ngeom, dtype=torch.bool, device=dev)
        self.surface[self.floor] = True
        self.surface[m.geom("podium").id] = True   # Unreal's knockdown rule: floor or podium
        # strike curriculum (reach < 1): touching the podium costs 2 per step instead of a knockdown; the full rule at the
        # real head and in every exam (2026-10-22: strict from the start, the striker froze, hand never within 0.5 m; with
        # only the arms exempt his belly, 13 cm from the podium face at rest, met it whenever he leaned)
        self.podium = m.geom("podium").id
        # the defender's brace: palms on the podium top, 7 cm in from its near edge, 17 cm either side of centre
        self.hands = torch.tensor([m.geom("hand_l").id, m.geom("hand_r").id], device=dev)
        self.is_hand = torch.zeros(m.ngeom, dtype=torch.bool, device=dev)
        self.is_hand[self.hands] = True
        pg = m.geom("podium")
        near, top = float(pg.pos[0] - pg.size[0]), float(pg.pos[2] + pg.size[2])
        self.brace_pt = torch.tensor([[near + 0.07, 0.17, top + 0.02], [near + 0.07, -0.17, top + 0.02]], device=dev)
        self.pod_z0, self.shorter = float(pg.pos[2]), torch.zeros(num_envs, device=dev)
        # strike imitation (2026-10-22): the reference slap from slap_demo.py (wind-up, strike, follow-through), its joint
        # angles per brain step from the stand pose; the striker earns up to `imit` per step for matching it
        demo = HERE / "models" / f"{character}_slap_demo.json"
        self.ref_q, self.imit = None, 0.0
        if demo.exists():
            dj = json.loads(demo.read_text())
            assert dj["joint_order"] == order, "slap demo joint order != fingerprint"
            ref = torch.tensor(dj["ref_q"], device=dev, dtype=torch.float32)
            self.ref_q = torch.cat([ref[:1].repeat(WAIT, 1), ref])   # 4 s standing still, then the slap
            self.imit_mask = torch.tensor([float(n in dj["joints"]) for n in order], device=dev)
        self.pod_touch = torch.zeros(num_envs, dtype=torch.bool, device=dev)
        self.pelvis, self.chest, self.head = m.body("pelvis").id, m.body("chest").id, m.body("head").id
        # strike: the mocap sphere stands in for the opponent's head; other roles park it out of reach
        self.tgeom, self.hand = int(m.body_geomadr[m.body("target").id]), m.geom("hand_r").id
        # the practice opponent (character card "sparring"): his face from our start pelvis, how much shorter he may be,
        # how far he leans in when braced (x SLAP_LEAN)
        self.spar = json.loads((HERE / "characters" / f"{character}.json").read_text())["sparring"]
        self.tbase = self.q0[:3] + torch.tensor(self.spar["head"], device=dev)          # the arena opponent's head
        self.teasy = self.q0[:3] + torch.tensor([0.30, -0.40, 0.45], device=dev)     # front-right at chest height: clear of the
                                                                                    # podium (x 0.305-0.505, y +-0.3) and of his own head (0.43 forward
                                                                                    # sat over the podium; 0.25 forward / 0.62 up was touched by his head)
        self.tpark = self.q0[:3] + torch.tensor([5.0, 0.0, 0.7], device=dev)
        # absorb: the opponent's right hand (their right = our +Y), at rest, wound up; it lands on our left cheek
        self.hrest = self.q0[:3] + torch.tensor([0.80, 0.20, -0.05], device=dev)
        self.hwind = self.q0[:3] + torch.tensor([0.70, 0.60, 0.55], device=dev)

        N, A = num_envs, self.A
        z = lambda *s: torch.zeros(*s, device=dev)
        zb = lambda: torch.zeros(N, dtype=torch.bool, device=dev)
        zl = lambda: torch.zeros(N, dtype=torch.long, device=dev)
        self.roles = {int(r): float(p) for r, p in self.cfg["roles"].items()}
        self.force_role = None   # exams: every world plays this role
        self.role = zl()
        self.goal, self.prev_action, self.prev2_action, self.t = self.stand.repeat(N, 1), z(N, A), z(N, A), zl()
        self.delay = zl()
        self.push_wait, self.push_left, self.push_f = z(N), z(N), z(N, 3)
        self.push_body = torch.full((N,), self.chest, dtype=torch.long, device=dev)
        self.ret, self.foot0, self.head0, self.left_box = z(N), z(N, 2, 2), z(N, 3), zb()
        self.push_scale = 1.0   # guard pushes at this share of 150-300 N (the trainer's curriculum; exams use the level's)
        self.hit_v = float(self.cfg.get("hit_speed", 8.0))   # strike: m/s a clean hit needs (the trainer's curriculum)
        self.reach = 1.0   # strike: target between easy (0) and the real opponent's head (1) (the trainer's curriculum)
        self.slap_j = float(self.cfg.get("slap_impulse", 0.0))   # absorb: mean slap impulse, N s (the trainer's curriculum)
        self.exam = False   # absorb: exams hit with exactly slap_j
        self.episodes = []  # finished episodes, dicts (see step); the trainer drains it
        self.struck, self.hit_ok, self.done_hit = zb(), zb(), zb()
        self.settle = zl()   # strike: control steps left after the strike
        self.hit_speed, self.bonus, self.hand_prev, self.hand_prev_ctrl, self.phi_prev = z(N), z(N), z(N, 3), z(N, 3), z(N)
        self.near, self.whiff = torch.full((N,), 9.0, device=dev), zb()   # strike: closest approach of a fast swing; a miss
        self.recoil, self.tgt_hit, self.back = z(N, 3), z(N, 3), z(N, 1)   # strike: the struck target's recoil, where it was hit, how fast it comes back
        self.t_hit, self.slap_f, self.slap_r, self.com0, self.com_err = zl(), z(N, 3), z(N, 3), z(N, 2), z(N)
        self.stamina, self.neck = torch.ones(N, device=dev), torch.ones(N, device=dev)
        self.hist, self.fresh, self.mem = z(N, 16, 6), zb(), z(N, 2)   # memory (BRAIN_IO 108-127)

        # CUDA graphs: compile once, then replay
        mjw.step(self.mw, self.dw); mjw.kinematics(self.mw, self.dw); wp.synchronize()
        with wp.ScopedCapture() as c:
            mjw.step(self.mw, self.dw)
        self.g_step = c.graph
        with wp.ScopedCapture() as c:
            mjw.kinematics(self.mw, self.dw)
        self.g_kin = c.graph
        self.reset(torch.ones(N, dtype=torch.bool, device=dev))
        # desynchronise the episode clocks once: a run (or a resume) starting every world at t = 0 put all strikes in the same
        # iteration and the resumed striker unlearned the slap (2026-10-22: 0 touches 12 min after resuming a 61 % brain).
        # Strikers stay inside the 4 s stand (< WAIT), guards spread over the 30 s clock; absorbers keep t = 0 (their slap timing).
        zero = torch.zeros(N, dtype=torch.long, device=dev)
        self.t += torch.where(self.role == 1, torch.randint(0, WAIT - 50, (N,), device=dev),
                              torch.where(self.role == 0, torch.randint(0, self.T - 200, (N,), device=dev), zero))

    # ------------------------------------------------------------------ reset
    def _u(self, n, lo, hi):
        return lo + torch.rand(n, device=self.device) * (hi - lo)

    def reset(self, mask):
        n = int(mask.sum())
        if n == 0:
            return
        # memory of the opponent's last strike: an absorb episode's slap (time, ~palm speed); a new "match" 1 in 6
        was = mask & (self.role == -1)
        j = self.slap_f.norm(dim=-1) * 0.01
        self.mem[was] = torch.stack([self.t_hit.float() / self.T, 0.8 * j / 15.0], -1)[was]
        self.mem[mask & (torch.rand(self.N, device=self.device) < 1 / 6)] = 0.0
        prev_hit = self.t_hit.clone()
        self.fresh |= mask
        mjw.reset_data(self.mw, self.dw, reset=wp.from_torch(mask))
        q = self.q0.repeat(n, 1)
        q[:, self.jq] += self._u((n, self.A), -0.05, 0.05)          # never the same start twice
        self.qpos[mask], self.qvel[mask] = q, 0.0
        if self.randomize:
            self.friction[mask, self.floor, 0] = self._u(n, 0.6, 1.2)
            self.friction[mask, self.feet[0], 0] = self._u(n, 0.6, 1.2)
            self.friction[mask, self.feet[1], 0] = self._u(n, 0.6, 1.2)
            s = self._u((n, self.mass.shape[1]), 0.95, 1.05)
            self.mass[mask], self.inertia[mask] = self.mass0 * s, self.inertia0 * s[..., None]
            k = self._u((n, self.gain.shape[1]), 0.9, 1.1)
            tired, hurt = torch.rand(n, device=self.device) < 0.7, torch.rand(n, device=self.device) < 0.7
            self.stamina[mask] = torch.where(tired, self._u(n, MIN_STAMINA, 1.0), 1.0)
            self.neck[mask] = torch.where(hurt, self._u(n, MIN_NECK, 1.0), 1.0)
            fr = self.frange0 * k[..., None]
            fr[:, self.neck_act] *= self.neck[mask][:, None, None]
            self.gain[mask], self.bias[mask], self.frange[mask] = self.gain0 * k[..., None], self.bias0 * k[..., None], fr
            self.delay[mask] = torch.randint(0, 5, (n,), device=self.device)
            # the opponent up to 10 cm shorter (Kim is 7 cm shorter than Grandpa): the podium (0.59 x the shorter
            # athlete's height) and his head (~0.93 x) sit lower; Grandpa's L2 brain overreached onto Kim's podium
            self.shorter[mask] = self._u(n, 0.0, self.spar["shorter_max"])
            self.gpos[mask, self.podium, 2] = self.pod_z0 - 0.59 * self.shorter[mask]
            self.geom_xpos[mask, self.podium, 2] = self.gpos[mask, self.podium, 2]   # a world geom: kinematics skips it
        if self.force_role is not None:
            self.role[mask] = self.force_role
        else:
            keys = torch.tensor(list(self.roles), device=self.device)
            self.role[mask] = keys[torch.multinomial(torch.tensor(list(self.roles.values()), device=self.device), n, replacement=True)]
        self.goal[mask], self.prev_action[mask], self.prev2_action[mask], self.t[mask], self.ret[mask] = self.stand, 0.0, 0.0, 0, 0.0
        self.push_wait[mask], self.push_left[mask] = self._u(n, 1.0, 3.0), 0.0
        self.left_box[mask] = False
        wp.capture_launch(self.g_kin)
        self.foot0[mask] = self.geom_xpos[mask][:, self.feet, :2]
        self.head0[mask] = self.xpos[mask][:, self.head]
        strike = mask & (self.role == 1)
        base = self.teasy + self.reach * (self.tbase - self.teasy)   # strike curriculum: 0 easy -> 1 the real head
        self.mocap[mask, 0] = self.tpark
        self.mocap[strike, 0] = base + self._u((int(strike.sum()), 3), -0.06, 0.06)   # +-6 cm: a braced, leaning defender's face moves
        # a braced defender leans in and down over the podium: Kim's face sat 13-14 cm lower and 5-10 cm closer than
        # a braced Grandpa's in Unreal (2026-10-23), past this range's old edge (-9 cm); Grandpa fell onto the podium.
        # SLAP_LEAN scales the lean (0..1): the full range at once froze the striker (0 swings in 40 min), so step it
        ns = int(strike.sum())
        lean = self.spar["lean"] * LEAN
        self.mocap[strike, 0, 2] -= 0.93 * self.shorter[strike] + self._u(ns, 0.0, 0.08 * lean)
        self.mocap[strike, 0, 0] -= self._u(ns, 0.0, 0.10 * lean)
        self.struck[mask], self.hit_ok[mask], self.settle[mask] = False, False, 0
        self.hit_speed[mask], self.bonus[mask] = 0.0, 0.0
        self.hand_prev[mask] = self.geom_xpos[mask][:, self.hand]
        self.hand_prev_ctrl[mask] = self.geom_xpos[mask][:, self.hand]
        self.near[mask], self.whiff[mask] = 9.0, False
        self.phi_prev[mask] = self._palm_phi(self.geom_xpos[:, self.hand], self.mocap[:, 0])[mask]
        # absorb: one slap at 1.5-3 s, from the striker's right (+Y here) across the face, a little away from him
        habit = (self.mem[mask, 0] > 0) & (torch.rand(n, device=self.device) < 0.5)   # same timing as last time
        again = (prev_hit[mask] + torch.randint(-5, 6, (n,), device=self.device)).clamp(75, 149)
        self.t_hit[mask] = torch.where(habit, again, torch.randint(75, 150, (n,), device=self.device))
        j = self.slap_j * (torch.ones(n, device=self.device) if self.exam else self._u(n, 0.6, 1.4))
        ang = self._u(n, -0.26, 0.26)                       # +-15 deg around the swing direction
        dx, dy = -0.3 * torch.cos(ang) + torch.sin(ang), -0.3 * torch.sin(ang) - torch.cos(ang)
        f = torch.stack([dx, dy, torch.full_like(dx, 0.05)], -1)
        self.slap_f[mask] = f / f.norm(dim=-1, keepdim=True) * (j / 0.01)[:, None]   # j over 10 ms
        self.slap_r[mask] = torch.tensor([0.05, 0.07, -0.03], device=self.device)    # left cheek, from the head centre
        self.com_err[mask] = 0.0

    # ------------------------------------------------------------------ step
    def _forces(self, dt, s):
        """Guard worlds: random pushes. Absorb worlds: the slap, two physics steps at control step t_hit."""
        self.xfrc.zero_()
        if self.randomize and self.push_scale > 0 and self.cfg.get("pushes"):
            guard = self.role == 0
            self.push_wait -= dt
            # whole-batch masks, no .any()/.sum() host syncs (they ran every physics step)
            start = (self.push_wait <= 0) & (self.push_left <= 0) & guard
            N = self.N
            ang = self._u(N, -math.pi, math.pi)
            mag = self._u(N, 150.0, 300.0) * self.push_scale
            f = torch.stack([torch.cos(ang) * mag, torch.sin(ang) * mag, torch.zeros_like(mag)], -1)
            self.push_f = torch.where(start[:, None], f, self.push_f)
            self.push_left = torch.where(start, self._u(N, 0.1, 0.2), self.push_left)
            self.push_wait = torch.where(start, self._u(N, 1.0, 3.0), self.push_wait)
            body = torch.where(torch.rand(N, device=self.device) < 0.5, self.chest, self.head)
            self.push_body = torch.where(start, body, self.push_body)
            on = (self.push_left > 0) & guard
            self.xfrc[torch.arange(N, device=self.device), self.push_body, :3] = self.push_f * on[:, None]
            self.push_left -= dt
        if s < 2:
            slap = ((self.role == -1) & (self.t == self.t_hit))[:, None]
            self.xfrc[:, self.head, :3] += self.slap_f * slap
            self.xfrc[:, self.head, 3:] += torch.cross(self.slap_r, self.slap_f, dim=-1) * slap
            if s == 0:
                self.com0 = torch.where(slap, self.com[:, self.pelvis, :2], self.com0)

    def step(self, action):
        a = action.clamp(-1.0, 1.0)
        if self.randomize and not self.exam:
            effort = (a ** 2).mean(dim=-1)
            delta_stam = torch.where(effort > 0.20, -0.00016 * effort, 0.0003 * (1.0 - effort))
            self.stamina = (self.stamina + delta_stam).clamp(MIN_STAMINA, 1.0)
        tgt = torch.clamp(self.stand + torch.where(a > 0, a * (self.hi - self.stand), a * (self.stand - self.lo)), self.lo, self.hi)
        ms = self.max_step * self.stamina[:, None]                 # fatigue: a tired athlete moves slower
        tgt = torch.max(torch.min(tgt, self.goal + ms), self.goal - ms)
        old, self.goal = self.goal, tgt
        for s in range(DECIM):
            self.ctrl[:, self.act_id] = torch.where((s >= self.delay)[:, None], tgt, old)
            self._forces(0.005, s)
            wp.capture_launch(self.g_step)
            self._strike_check()
        # strike: the struck head recoils along the swing (mostly back, away from the striker) and comes back in ~1.5 s,
        # or, 1 in 3 (knocked off balance), keeps going: a 78 kg braced guard's head in match.py with the impact impulse.
        # The L2 striker had only ever seen a target that stayed put, and in matches chased it and fell
        k = (SETTLE - self.settle).float()[:, None]
        self.mocap[:, 0] = torch.where(self.struck[:, None], self.tgt_hit + self.recoil * (1 - torch.exp(-k / 5)) * torch.exp(-k * self.back), self.mocap[:, 0])
        wp.capture_launch(self.g_kin)

        fell = self.fell_now = self._fallen()
        rew, out = self._reward(a, fell)
        self.prev2_action = self.prev_action
        self.prev_action = a
        self.t += 1
        self.ret += rew
        stop = fell | out | self.whiff          # leaving the foot box ends the episode like a fall; so does a missed swing
        strike, absorb = self.role == 1, self.role == -1
        self.settle = torch.where(self.struck, self.settle - 1, self.settle)
        done = stop | (self.t >= self.T)
        done |= strike & self.struck & (self.settle <= 0)          # strike: did he stay up the 5 s recovery window?
        done |= absorb & (self.t >= self.t_hit + 250)              # absorb: 5 s after the slap
        timeout = done & ~stop
        self.done_hit = done & self.hit_ok & ~stop
        self.done_absorbed = done & ~stop & (self.com_err <= 0.05)    # absorb: up, COM back within 5 cm after 5 s
        if done.any():
            cols = (self.ret, self.t, fell, self.left_box, self.done_hit, self.hit_speed, self.role, self.done_absorbed, self.com_err, self.whiff)
            for r, l, f, b, h, v, ro, ab, ce, wf in zip(*(c[done].tolist() for c in cols)):
                self.episodes.append(dict(ret=r, len=l, fell=f, left_box=b, hit=h, speed=v, role=ro, absorbed=ab, com_err=ce, whiff=wf))
            self.reset(done)
        return self.observe(), rew, done, timeout

    def _palm_phi(self, hand, tgt):
        """Strike v4: 2 x closeness x how squarely the palm faces the target (every first touch had been back/edge/fingers)."""
        to = tgt - hand
        d = to.norm(dim=-1)
        palm_dir = -self.geom_xmat[:, self.hand][:, :, 2]
        face = ((to / d[:, None].clamp(min=1e-6)) * palm_dir).sum(-1).clamp(min=0)
        return 2.0 * torch.exp(-d ** 2 / 0.08) * face

    def _strike_check(self):
        """After each physics step: the first contact with the target decides the strike (Blueprint L2)."""
        hand = self.geom_xpos[:, self.hand]
        vel = (hand - self.hand_prev) / 0.005
        speed = vel.norm(dim=-1)
        self.hand_prev = hand.clone()
        n = self.nacon[0]
        idx = torch.arange(self.con_geom.shape[0], device=self.device)
        g0, g1 = self.con_geom[:, 0].long(), self.con_geom[:, 1].long()
        on = (idx < n) & (((g0 == self.tgeom) & (g1 == self.hand)) | ((g1 == self.tgeom) & (g0 == self.hand)))   # the hand only:
        # a head/chest/arm bump used to end the attempt for ~-3, cheaper than the clock (-95): he learned to head-butt it
        # every contact slot at once, scattered to its world: no host sync per physics step
        w = self.con_world.long().clamp(0, self.N - 1)                     # slots past nacon hold stale ids (masked by on)
        hit = torch.zeros(self.N, device=self.device).scatter_reduce_(0, w, on.float(), "amax").bool()
        early = self.t < WAIT                                # before the 4 s stand is over: a foul, not the strike
        self.bonus -= 0.5 * (hit & early & ~self.struck).float()
        new = hit & ~self.struck & ~early
        v = speed
        clean = hit & (v >= self.hit_v)                      # any hit counts (user 2026-10-25): the palm is technique, not validity
        self.hit_ok = torch.where(new, clean, self.hit_ok)
        self.hit_speed = torch.where(new, v, self.hit_speed)
        pay = torch.where(clean, 50.0 + 8.0 * v, 3.0 * v + v * v)
        self.bonus += torch.where(new, pay, torch.zeros_like(v))
        # one slap per turn (user): touching the target again 0.3 s or more after the strike costs 1 per physics step
        self.bonus -= 1.0 * (hit & self.struck & (self.settle < SETTLE - 15)).float()
        flat = vel * torch.tensor([1.0, 1.0, 0.0], device=self.device)
        flat = flat / flat.norm(dim=-1, keepdim=True).clamp(min=1e-6)
        self.recoil = torch.where(new[:, None], flat * self._u(self.N, 0.10, 0.60)[:, None], self.recoil)
        self.back = torch.where(new[:, None], (torch.rand(self.N, 1, device=self.device) < 0.67).float() / 75, self.back)
        self.tgt_hit = torch.where(new[:, None], self.mocap[:, 0], self.tgt_hit)
        self.struck |= new
        self.settle = torch.where(new, torch.full_like(self.settle, SETTLE), self.settle)

    def _fallen(self):
        n = self.nacon[0]
        idx = torch.arange(self.con_geom.shape[0], device=self.device)
        g0, g1 = self.con_geom[:, 0].long(), self.con_geom[:, 1].long()
        hit = (idx < n) & ((self.surface[g0] & self.bad[g1]) | (self.surface[g1] & self.bad[g0]))
        w = self.con_world.long()
        fell = torch.zeros(self.N, dtype=torch.bool, device=self.device)
        fell[w[hit]] = True
        # the defender (guard / absorb) may brace with his hands on the podium (user, 2026-10-22); the striker may not
        brace = hit & (((g0 == self.podium) & self.is_hand[g1]) | ((g1 == self.podium) & self.is_hand[g0]))
        braced = torch.zeros_like(fell)
        braced[w[hit & ~brace]] = True
        fell = torch.where(self.role != 1, braced, fell)
        self.pod_touch = torch.zeros_like(fell)
        if self.reach < 1.0:   # strike curriculum: the podium is not a knockdown yet (see __init__)
            pod = hit & ((g0 == self.podium) | (g1 == self.podium))
            self.pod_touch[w[pod]] = True
            soft = torch.zeros_like(fell)
            soft[w[hit & ~pod]] = True
            strike = self.role == 1
            fell = torch.where(strike, soft, fell)
            self.pod_touch &= strike
        return fell | (self.qpos[:, 2] < 0.45)

    def _reward(self, a, fell):
        R = quat_mat(self.qpos[:, 3:7])
        upright = R[:, 2, 2]                                           # 1 when the pelvis is vertical
        height = torch.exp(-((self.qpos[:, 2] - self.h0) ** 2) / 0.01)
        posture = torch.exp(-((self.qpos[:, self.jq] - self.stand) ** 2).sum(-1) / 1.0)
        smooth = ((a - self.prev_action) ** 2).sum(-1)
        jerk = ((a - 2.0 * self.prev_action + self.prev2_action) ** 2).sum(-1)
        r = 1.0 + 0.5 * upright.clamp(min=0) + 0.5 * height + 0.3 * posture - 0.01 * smooth - 0.002 * jerk
        if self.cfg.get("foot_box"):   # L1 guard: feet in the box, facing the opponent (+X), head where it started
            bx, by = self.cfg["foot_box"]
            d = (self.geom_xpos[:, self.feet, :2] - self.foot0).abs()
            out = (d[..., 0] > bx).any(-1) | (d[..., 1] > by).any(-1)
            self.left_box |= out
            facing = R[:, 0, 0]                                        # cos(yaw): pelvis x-axis on the world x-axis
            head = torch.exp(-((self.xpos[:, self.head, :2] - self.head0[:, :2]) ** 2).sum(-1) / 0.01)
            anchor = torch.exp(-(d ** 2).sum((-1, -2)) / 0.005)         # feet on their marks, before the hard limit
            r = r + 0.3 * facing + 0.5 * anchor + 0.3 * head
            # the brace: both palms on the podium's near edge, shoulder-width apart (pays the guard and the absorber)
            hands = self.geom_xpos[:, self.hands]                       # (N, 2, 3)
            bp = self.brace_pt - torch.stack([torch.zeros_like(self.shorter), torch.zeros_like(self.shorter), 0.59 * self.shorter], -1)[:, None]
            r = r + 0.4 * torch.exp(-((hands - bp) ** 2).sum(-1) / 0.01).sum(-1)
        else:
            out = torch.zeros_like(fell)

        # absorb (L3): after the slap, pay for the centre of mass coming back over its pre-slap spot
        self.com_err = (self.com[:, self.pelvis, :2] - self.com0).norm(dim=-1)
        after = self.t > self.t_hit
        r_absorb = r + torch.where(after, 1.0 * torch.exp(-self.com_err ** 2 / 0.0025), torch.zeros_like(r))

        # strike (L2), v2 (the first version paid ~0.8/step for a hand near the target: the brain hovered 30 s, never
        # struck). Before the strike every step costs a little; closing on the target pays only the distance gained (so
        # hovering earns nothing); the strike is where the reward is, and it grows with hand speed. The box stays a rule
        # only: any per-step pay for standing invites hovering. v3: no pay for standing after a tap.
        hand, tgt = self.geom_xpos[:, self.hand], self.mocap[:, 0]
        d = (tgt - hand).norm(dim=-1)
        d_prev = (tgt - self.hand_prev_ctrl).norm(dim=-1)
        phi = self._palm_phi(hand, tgt)
        go = (self.t >= WAIT).float()   # the 4 s stand: no pay (and no cost) for moving at the target yet
        before = go * (-0.05 + 30.0 * (d_prev - d) + 5.0 * (phi - self.phi_prev)) + 0.04 * upright.clamp(min=0) - 0.01 * smooth \
            - 2.0 * self.pod_touch.float()   # v5: approach x3 (10 was outweighed by the podium risk: he froze)
        self.phi_prev = phi
        r_strike = torch.where(self.struck, torch.zeros_like(r), before) + self.bonus
        if self.ref_q is not None and self.imit > 0:   # imitation: wind-up, strike, follow-through, back to the stand
            ref = self.ref_q[self.t.clamp(max=len(self.ref_q) - 1)]
            err = (((self.qpos[:, self.jq] - ref) ** 2) * self.imit_mask).sum(-1)
            during = (self.t < len(self.ref_q)).float()   # only while the reference plays: holding its last (stand) pose
            r_strike = r_strike + self.imit * torch.exp(-err) * during   # out the 30 s clock had paid more than slapping
        r_strike = r_strike - 20.0 * ((self.t + 1 >= self.T) & ~self.struck).float()      # the clock ran out: no strike
        # one accurate swing (user: no flailing, always the face): a fast swing (> 3 m/s) that came within 30 cm of the face
        # and is moving away again (15 cm past its closest) without a touch is a miss: the attempt ends, -50
        v_hand = (hand - self.hand_prev_ctrl).norm(dim=-1) / 0.02
        self.near = torch.where((v_hand > 3.0) & (d < self.near), d, self.near)
        self.whiff = (self.role == 1) & ~self.struck & (self.t >= WAIT) & (self.near < 0.30) & (d > self.near + 0.15)
        r_strike = r_strike - 50.0 * self.whiff.float()
        self.bonus.zero_()
        self.hand_prev_ctrl = hand.clone()

        r = torch.where(self.role == 1, r_strike, torch.where(self.role == -1, r_absorb, r))
        penalty = torch.where(self.role == 1, -100.0, -5.0)  # strike: falling must cost more than imitating the slap pays
        # (-30 < ~240 of imitation: he copied the reference, which falls open-loop, and fell in 96 % of strikes)
        return torch.where(fell | out, penalty, r), out

    # ------------------------------------------------------------------ observe (BRAIN_IO)
    def _heading(self, v, c, s):
        return torch.stack([c * v[:, 0] + s * v[:, 1], -s * v[:, 0] + c * v[:, 1], v[:, 2]], -1)

    def opp_hand(self):
        """Absorb: the scripted opponent's right hand: rest -> wound up (0.6-0.15 s before) -> our cheek -> rest (0.5 s)."""
        t = (self.t - self.t_hit).float()[:, None] / 50.0
        cheek = self.head0 + self.slap_r
        wind = self.hrest + ((t + 0.6) / 0.45).clamp(0, 1) * (self.hwind - self.hrest)
        swing = self.hwind + ((t + 0.15) / 0.15).clamp(0, 1) * (cheek - self.hwind)
        back = cheek + (t / 0.5).clamp(0, 1) * (self.hrest - cheek)
        return torch.where(t < -0.15, wind, torch.where(t < 0, swing, back))

    def observe(self):
        q, v = self.qpos, self.qvel
        R = quat_mat(q[:, 3:7])
        yaw = torch.atan2(R[:, 1, 0], R[:, 0, 0])
        c, s = torch.cos(yaw), torch.sin(yaw)
        role = self.role.float()
        b = torch.zeros_like(q[:, 0:3])                              # role 0: own foot-box centre (start pelvis x, y; z 0)
        b[:, 0], b[:, 1] = self.q0[0] - q[:, 0], self.q0[1] - q[:, 1]
        tgt = torch.where((self.role == 1)[:, None], self.mocap[:, 0], self.tbase)   # strike: target; absorb: opponent head
        box = torch.where((self.role == 0)[:, None], b, tgt - q[:, 0:3])
        clock = torch.where(self.role != 0, 1.0 - self.t.float() / self.T, torch.zeros_like(role))
        hand = torch.where((self.role == -1)[:, None], self.opp_hand() - q[:, 0:3], torch.zeros_like(b))
        cur = torch.cat([self._heading(box, c, s), self._heading(hand, c, s)], -1)
        self.hist = torch.where(self.fresh[:, None, None], cur[:, None].expand(-1, 16, -1), torch.cat([self.hist[:, 1:], cur[:, None]], 1))
        self.fresh[:] = False
        obs = torch.cat([
            q[:, self.jq] - self.stand,
            v[:, self.jv] * 0.1,
            q[:, 2:3],
            -R[:, 2, :],                     # R^T (0,0,-1)
            self._heading(v[:, 0:3], c, s),
            v[:, 3:6] * 0.25,
            self.prev_action,
            role[:, None], clock[:, None],                # role, turn clock remaining
            cur,                                          # role 0: own foot-box centre; else target / opponent head - pelvis;
                                                          # opponent hand - pelvis (absorb only; 0 otherwise)
            *[self.hist[:, -1 - lag] for lag in LAGS],    # the same 0.1, 0.2, 0.3 s ago
            self.mem, self.stamina[:, None], self.neck[:, None],
            self._heading(self.geom_xpos[:, self.hand] - self.xpos[:, self.pelvis], c, s),   # own palm: centre - pelvis,
            self._heading(-self.geom_xmat[:, self.hand][:, :, 2], c, s),                     # facing (BRAIN_IO 130-135)
        ], -1)
        if self.randomize:
            noise = torch.zeros(OBS, device=self.device)
            noise[:30], noise[30:60], noise[61:64] = 0.005, 0.05, 0.01
            obs = obs + torch.randn_like(obs) * noise
        return obs
