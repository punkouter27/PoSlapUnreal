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
from pathlib import Path

import mujoco
import mujoco_warp as mjw
import torch
import warp as wp

from brain_cpu import LAGS, MIN_NECK, MIN_STAMINA, OBS
from envs.slap import DECIM, WAIT, quat_mat
from levels import LEVELS

HERE = Path(__file__).resolve().parents[1]
PREFIX = ("red_", "blue_")
KO_WINDOW = 250     # control steps after the first head touch: the match's 5 s recovery window (match.py)


class ArenaEnv:
    def __init__(self, character="grandpa", level="L4", num_envs=4096, seed=0, randomize=True):
        self.level, self.cfg, self.W, self.randomize = level, LEVELS[level], num_envs, randomize
        self.N = 2 * num_envs          # agents
        dev = self.device = "cuda"
        torch.manual_seed(seed)
        wp.set_device("cuda:0")
        self.stream = torch.cuda.Stream()
        torch.cuda.set_stream(self.stream)
        wp.set_stream(wp.stream_from_torch(self.stream))

        fp = json.loads((HERE / "models" / f"{character}_fingerprint.json").read_text())
        m = self.m = mujoco.MjModel.from_xml_path(str(HERE / "models" / f"{character}_vs_{character}_arena.xml"))
        order = fp["joint_order"]
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
        self.head_g = t([m.geom(p + "head").id for p in PREFIX])
        self.hand_g = t([m.geom(p + "hand_r").id for p in PREFIX])
        self.feet = t([[m.geom(p + "foot_l").id, m.geom(p + "foot_r").id] for p in PREFIX])
        self.stand = t([fp["stand_pose_rad"].get(n, 0.0) for n in order])
        rng = t(fp["ctrl_range_rad"])
        self.lo, self.hi = rng[:, 0], rng[:, 1]
        self.max_step = t(fp["max_joint_speed_rad_s"]) * 0.02   # per joint (scalar in older fingerprints)
        self.T = 1500

        d0 = mujoco.MjData(m)
        for k in range(2):
            d0.qpos[self.jq[k].cpu().numpy()] = self.stand.cpu().numpy()
        mujoco.mj_forward(m, d0)
        self.q0 = torch.tensor(d0.qpos, device=dev, dtype=torch.float32)
        self.h0 = float(d0.qpos[2])
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
        self.geom_xpos, self.geom_xmat = T(self.dw.geom_xpos), T(self.dw.geom_xmat)
        self.con_geom, self.con_world, self.nacon = T(self.dw.contact.geom), T(self.dw.contact.worldid), T(self.dw.nacon)
        self.con_frame = T(self.dw.contact.frame)
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

        W, N, A = num_envs, self.N, self.A
        z = lambda *s: torch.zeros(*s, device=dev)
        self.striker = torch.zeros(W, dtype=torch.long, device=dev)
        self.goal, self.prev_action = self.stand.repeat(N, 1), z(N, A)
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
        self.force_striker = None   # exams: None = coin toss
        self.stamina, self.neck = torch.ones(W, 2, device=dev), torch.ones(W, 2, device=dev)
        self.hist, self.fresh, self.mem = z(N, 16, 6), torch.zeros(W, dtype=torch.bool, device=dev), z(W, 2, 2)

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
        if self.randomize:
            self.friction[mask, self.floor, 0] = self._u(n, 0.6, 1.2)
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
        self.goal[am], self.prev_action[am], self.ret[am], self.left_box[am] = self.stand, 0.0, 0.0, False
        self.t[mask] = 0
        wp.capture_launch(self.g_kin)
        self.foot0[mask] = self.geom_xpos[mask][:, self.feet, :2]
        self.head0[mask] = self.geom_xpos[mask][:, self.head_g]
        self.struck[mask], self.hit_ok[mask], self.hit_speed[mask], self.bonus[mask] = False, False, 0.0, 0.0
        hs, ht = self._sd(self.geom_xpos[:, self.hand_g]), self._sd(self.geom_xpos[:, self.head_g])
        self.hand_prev[mask], self.hand_prev_ctrl[mask] = hs[0][mask], hs[0][mask]
        self.phi_prev[mask] = self._palm_phi(hs[0], ht[1])[mask]
        self.com_err[mask] = 0.0

    # ------------------------------------------------------------------ step
    def step(self, action):
        a = action.clamp(-1.0, 1.0)
        tgt = torch.clamp(self.stand + torch.where(a > 0, a * (self.hi - self.stand), a * (self.stand - self.lo)), self.lo, self.hi)
        ms = self.max_step * self.stamina.reshape(-1, 1)          # fatigue: a tired athlete moves slower
        tgt = torch.max(torch.min(tgt, self.goal + ms), self.goal - ms)
        old, self.goal = self.goal, tgt
        tw, ow = tgt.view(self.W, 2, self.A), old.view(self.W, 2, self.A)
        for s in range(DECIM):
            on = (s >= self.delay)[:, None, None]
            u = torch.where(on, tw, ow)
            for k in range(2):
                self.ctrl[:, self.act_id[k]] = u[:, k]
            wp.capture_launch(self.g_step)
            self._strike_check()
        wp.capture_launch(self.g_kin)

        fell = self._fallen()                                  # (W, 2)
        rew, out = self._reward(a, fell)                       # (2W,), (W, 2)
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
                    ko.repeat_interleave(2)[dd], self.struck.repeat_interleave(2)[dd])
            for r, l, f, b, h, v, ro, k, st in zip(*(c.tolist() for c in cols)):
                self.episodes.append(dict(ret=r, len=l, fell=f, left_box=b, hit=h, speed=v, role=int(ro), ko=k, struck=st,
                                          absorbed=(not f) if ro < 0 else False, com_err=0.0))
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
        speed = (hand - self.hand_prev).norm(dim=-1) / 0.005
        self.hand_prev = hand.clone()
        n = self.nacon[0]
        idx = torch.arange(self.con_geom.shape[0], device=self.device)
        g0, g1 = self.con_geom[:, 0].long(), self.con_geom[:, 1].long()
        w = self.con_world.long().clamp(0, self.W - 1)                # slots past nacon hold stale ids
        dhead = self.head_g[1 - self.striker[w]]                        # the defender's head in that contact's world
        shand = self.hand_g[self.striker[w]]
        on = (idx < n) & ((g0 == dhead) | (g1 == dhead))
        other = torch.where(g0 == dhead, g1, g0)
        on &= self.owner[other] == self.striker[w]                     # touched by the striker's body
        if not bool(on.any()):
            return
        w, other, dhead, shand = w[on], other[on], dhead[on], shand[on]
        sign = torch.where(g0[on] == dhead, -1.0, 1.0)[:, None]
        normal = self.con_frame[on][:, 0, :] * sign
        palm_dir = -self.geom_xmat[w, shand][:, :, 2]
        palm = (other == shand) & ((normal * palm_dir).sum(-1) > 0.5)
        hit = torch.zeros(self.W, dtype=torch.bool, device=self.device)
        hit[w] = True
        good = torch.zeros_like(hit)
        good[w[palm]] = True
        early = self.t < WAIT                                # the 4 s stand (envs/slap.py WAIT): an early touch is a foul
        self.bonus[hit & early & ~self.struck] -= 0.5
        self.bonus[hit & self.struck & (self.t > self.t_hit + 15)] -= 1.0   # one slap per turn: a second touch costs
        new = hit & ~self.struck & ~early
        if bool(new.any()):
            v = speed[new]
            clean = good[new] & (v >= self.hit_v)
            self.hit_ok[new], self.hit_speed[new] = clean, v
            self.bonus[new] += torch.where(clean, 50.0 + 8.0 * v, torch.where(good[new], 3.0 * v + v * v, torch.full_like(v, -2.0)))
            dh = self._sd(self.geom_xpos[:, self.head_g])[1][new] - self._sd(self.head0)[1][new]   # feint: he had flinched
            self.bonus[new] += torch.where(good[new], 100.0 * (dh[:, :2].norm(dim=-1) - 0.03).clamp(0, 0.15), torch.zeros_like(v))
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
        a2, p2 = a.view(W, 2, -1), self.prev_action.view(W, 2, -1)
        smooth = ((a2 - p2) ** 2).sum(-1)
        r = 1.0 + 0.5 * upright.clamp(min=0) + 0.5 * height + 0.3 * posture - 0.01 * smooth
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
        r_str = torch.where(self.struck, torch.zeros_like(before), before) + self.bonus + 40.0 * ko.float()
        r_str = r_str - 20.0 * ((self.t + 1 >= self.T) & ~self.struck).float()
        self.bonus.zero_()

        rr = torch.where(role > 0, r_str[:, None].expand(-1, 2), r_def)
        penalty = torch.where(role > 0, -30.0, -5.0)
        rr = torch.where(fell | out, penalty, rr)
        return self._pair(rr), out

    # ------------------------------------------------------------------ observe (BRAIN_IO)
    def observe(self):
        W, q, v = self.W, self.qpos, self.qvel
        role = self._role()
        clock = (1.0 - self.t.float() / self.T)[:, None].expand(-1, 2)
        head, hand = self.geom_xpos[:, self.head_g], self.geom_xpos[:, self.hand_g]
        fresh = self.fresh.repeat_interleave(2)
        self.fresh[:] = False
        rows = []
        for k in range(2):
            rq, rv = self.rq[k], self.rv[k]
            R = quat_mat(q[:, rq + 3:rq + 7])
            yaw = torch.atan2(R[:, 1, 0], R[:, 0, 0])
            c, s = torch.cos(yaw), torch.sin(yaw)
            hd = lambda x: torch.stack([c * x[:, 0] + s * x[:, 1], -s * x[:, 0] + c * x[:, 1], x[:, 2]], -1)
            pel = self.xpos[:, self.pelvis[k]]
            opp_hand = torch.where((role[:, k] < 0)[:, None], hand[:, 1 - k] - pel, torch.zeros_like(pel))
            cur = torch.cat([hd(head[:, 1 - k] - pel), hd(opp_hand)], -1)
            rows.append(torch.cat([
                q[:, self.jq[k]] - self.stand,
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
