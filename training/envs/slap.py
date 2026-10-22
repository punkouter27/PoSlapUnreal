"""Slap training env on MuJoCo Warp: N worlds of one athlete, on the GPU (levels L0, L1 for now).

Observation and action are DOCS/BRAIN_IO.md exactly; Unreal's USlapController builds the same 108 numbers.
Randomised every episode (Blueprint 6): floor friction, +-5 % mass per part, +-10 % strength, 0-20 ms action
delay, pushes of 150-300 N on chest or head, a little sensor noise.
A fall is Unreal's knockdown rule: any non-foot geom touching the floor (MuJoCo contacts, not a height guess).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import mujoco
import mujoco_warp as mjw
import numpy as np
import torch
import warp as wp

from levels import LEVELS

HERE = Path(__file__).resolve().parents[1]
DECIM = 4           # physics steps per brain step (200 Hz / 50 Hz)
OBS = 108           # DOCS/BRAIN_IO.md


def quat_mat(q):    # (N, 4) wxyz -> (N, 3, 3), body -> world
    w, x, y, z = q.unbind(-1)
    return torch.stack([1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y),
                        2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x),
                        2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)], -1).view(-1, 3, 3)


class SlapEnv:
    def __init__(self, character="grandpa", level="L0", num_envs=4096, seed=0, randomize=True):
        if level not in ("L0", "L1", "L2"):
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
        self.stand = torch.tensor([fp["stand_pose_rad"].get(n, 0.0) for n in order], device=dev)
        rng = torch.tensor(fp["ctrl_range_rad"], device=dev)   # listed in joint order
        self.lo, self.hi = rng[:, 0], rng[:, 1]
        self.scale = 0.5 * (self.hi - self.lo)
        self.max_step = fp["max_joint_speed_rad_s"] * 0.02
        self.T = int(self.cfg["seconds"] * 50)

        d0 = mujoco.MjData(m)
        d0.qpos[self.jq.cpu().numpy()] = self.stand.cpu().numpy()
        mujoco.mj_forward(m, d0)
        self.q0 = torch.tensor(d0.qpos, device=dev, dtype=torch.float32)
        self.h0 = float(d0.qpos[2])
        self.mw = mjw.put_model(m)
        self.dw = mjw.put_data(m, d0, nworld=num_envs, naconmax=num_envs * 24, njmax=256)

        # model fields randomised per world: one row each
        def per_world(name):
            a = getattr(self.mw, name)
            t = wp.to_torch(a)
            t = t.repeat(num_envs, *[1] * (t.dim() - 1)).contiguous()
            setattr(self.mw, name, wp.from_torch(t, dtype=a.dtype))
            return t
        self.friction, self.mass, self.inertia = per_world("geom_friction"), per_world("body_mass"), per_world("body_inertia")
        self.gain, self.bias, self.frange = per_world("actuator_gainprm"), per_world("actuator_biasprm"), per_world("actuator_forcerange")
        self.mass0, self.inertia0 = self.mass[0].clone(), self.inertia[0].clone()
        self.gain0, self.bias0, self.frange0 = self.gain[0].clone(), self.bias[0].clone(), self.frange[0].clone()
        self.floor = m.geom("floor").id

        T = lambda a: wp.to_torch(a)
        self.qpos, self.qvel, self.ctrl = T(self.dw.qpos), T(self.dw.qvel), T(self.dw.ctrl)
        self.xpos, self.xfrc = T(self.dw.xpos), T(self.dw.xfrc_applied)
        self.geom_xpos = T(self.dw.geom_xpos)
        self.con_geom, self.con_world, self.nacon = T(self.dw.contact.geom), T(self.dw.contact.worldid), T(self.dw.nacon)
        feet = [m.geom("foot_l").id, m.geom("foot_r").id]
        self.feet = torch.tensor(feet, device=dev)
        body_geoms = [g for g in range(m.ngeom) if m.body_rootid[m.geom_bodyid[g]] == m.body("pelvis").id]
        self.bad = torch.zeros(m.ngeom, dtype=torch.bool, device=dev)
        self.bad[[g for g in body_geoms if g not in feet]] = True
        self.surface = torch.zeros(m.ngeom, dtype=torch.bool, device=dev)
        self.surface[self.floor] = True
        self.pelvis, self.chest, self.head = m.body("pelvis").id, m.body("chest").id, m.body("head").id
        self.strike = self.cfg["role"] == 1
        if self.strike:   # L2: the mocap sphere stands in for the opponent's head (arena: 0.80 m ahead, 0.74 m up)
            self.tgeom, self.hand = int(m.body_geomadr[m.body("target").id]), m.geom("hand_r").id
            self.tbase = self.q0[:3] + torch.tensor([0.8015, 0.0, 0.74], device=dev)
            self.mocap, self.geom_xmat = T(self.dw.mocap_pos), T(self.dw.geom_xmat)
            self.con_frame = T(self.dw.contact.frame)

        N, A = num_envs, self.A
        z = lambda *s: torch.zeros(*s, device=dev)
        self.goal, self.prev_action, self.t = self.stand.repeat(N, 1), z(N, A), torch.zeros(N, dtype=torch.long, device=dev)
        self.delay = torch.zeros(N, dtype=torch.long, device=dev)
        self.push_wait, self.push_left, self.push_f = z(N), z(N), z(N, 3)
        self.push_body = torch.full((N,), self.chest, dtype=torch.long, device=dev)
        self.ret, self.foot0, self.head0, self.left_box = z(N), z(N, 2, 2), z(N, 2), torch.zeros(N, dtype=torch.bool, device=dev)
        self.push_scale = 1.0   # pushes at this share of 150-300 N (the trainer's curriculum; exams use 1)
        self.episodes = []   # (return, fell, left foot box, length, clean hit, hit speed) of finished episodes; the trainer drains it
        self.struck = torch.zeros(N, dtype=torch.bool, device=dev)
        self.hit_ok = torch.zeros(N, dtype=torch.bool, device=dev)
        self.done_hit = torch.zeros(N, dtype=torch.bool, device=dev)
        self.settle = torch.zeros(N, dtype=torch.long, device=dev)   # control steps left after the strike
        self.hit_speed, self.bonus, self.hand_prev, self.hand_prev_ctrl = z(N), z(N), z(N, 3), z(N, 3)

        # CUDA graphs: compile once, then replay
        mjw.step(self.mw, self.dw); mjw.kinematics(self.mw, self.dw); wp.synchronize()
        with wp.ScopedCapture() as c:
            mjw.step(self.mw, self.dw)
        self.g_step = c.graph
        with wp.ScopedCapture() as c:
            mjw.kinematics(self.mw, self.dw)
        self.g_kin = c.graph
        self.reset(torch.ones(N, dtype=torch.bool, device=dev))

    # ------------------------------------------------------------------ reset
    def _u(self, n, lo, hi):
        return lo + torch.rand(n, device=self.device) * (hi - lo)

    def reset(self, mask):
        n = int(mask.sum())
        if n == 0:
            return
        mjw.reset_data(self.mw, self.dw, reset=wp.from_torch(mask))
        q = self.q0.repeat(n, 1)
        q[:, self.jq] += self._u((n, self.A), -0.05, 0.05)          # never the same start twice
        self.qpos[mask], self.qvel[mask] = q, 0.0
        if self.randomize:
            self.friction[mask, self.floor, 0] = self._u(n, 0.6, 1.2)
            s = self._u((n, self.mass.shape[1]), 0.95, 1.05)
            self.mass[mask], self.inertia[mask] = self.mass0 * s, self.inertia0 * s[..., None]
            k = self._u((n, self.gain.shape[1]), 0.9, 1.1)
            self.gain[mask], self.bias[mask], self.frange[mask] = self.gain0 * k[..., None], self.bias0 * k[..., None], self.frange0 * k[..., None]
            self.delay[mask] = torch.randint(0, 5, (n,), device=self.device)
        self.goal[mask], self.prev_action[mask], self.t[mask], self.ret[mask] = self.stand, 0.0, 0, 0.0
        self.push_wait[mask], self.push_left[mask] = self._u(n, 1.0, 3.0), 0.0
        self.left_box[mask] = False
        wp.capture_launch(self.g_kin)
        self.foot0[mask] = self.geom_xpos[mask][:, self.feet, :2]
        self.head0[mask] = self.xpos[mask][:, self.head, :2]
        if self.strike:
            self.mocap[mask, 0] = self.tbase + self._u((n, 3), -0.03, 0.03)
            self.struck[mask], self.hit_ok[mask], self.settle[mask] = False, False, 0
            self.hit_speed[mask], self.bonus[mask] = 0.0, 0.0
            self.hand_prev[mask] = self.geom_xpos[mask][:, self.hand]
            self.hand_prev_ctrl[mask] = self.geom_xpos[mask][:, self.hand]

    # ------------------------------------------------------------------ step
    def _pushes(self, dt):
        if not (self.cfg["pushes"] and self.randomize and self.push_scale > 0):
            return
        self.xfrc.zero_()
        self.push_wait -= dt
        start = (self.push_wait <= 0) & (self.push_left <= 0)
        n = int(start.sum())
        if n:
            ang = self._u(n, -math.pi, math.pi)
            mag = self._u(n, 150.0, 300.0) * self.push_scale
            self.push_f[start] = torch.stack([torch.cos(ang) * mag, torch.sin(ang) * mag, torch.zeros_like(mag)], -1)
            self.push_left[start] = self._u(n, 0.1, 0.2)
            self.push_wait[start] = self._u(n, 1.0, 3.0)
            self.push_body[start] = torch.where(torch.rand(n, device=self.device) < 0.5, self.chest, self.head)
        on = self.push_left > 0
        if on.any():
            w = on.nonzero().squeeze(-1)
            self.xfrc[w, self.push_body[w], :3] = self.push_f[w]
        self.push_left -= dt

    def step(self, action):
        a = action.clamp(-1.0, 1.0)
        tgt = torch.clamp(self.stand + a * self.scale, self.lo, self.hi)
        tgt = torch.max(torch.min(tgt, self.goal + self.max_step), self.goal - self.max_step)
        old, self.goal = self.goal, tgt
        for s in range(DECIM):
            self.ctrl[:, self.act_id] = torch.where((s >= self.delay)[:, None], tgt, old)
            self._pushes(0.005)
            wp.capture_launch(self.g_step)
            if self.strike:
                self._strike_check()
        wp.capture_launch(self.g_kin)

        fell = self.fell_now = self._fallen()
        rew, out = self._reward(a, fell)
        self.prev_action = a
        self.t += 1
        self.ret += rew
        stop = fell | out                       # L1: leaving the foot box ends the episode like a fall
        done = stop | (self.t >= self.T)
        if self.strike:                         # L2: the episode ends 1.5 s after the strike (did he stay up?)
            self.settle = torch.where(self.struck, self.settle - 1, self.settle)
            done = done | (self.struck & (self.settle <= 0))
        timeout = done & ~stop
        self.done_hit = done & self.hit_ok & ~stop
        if done.any():
            for r, l, f, b, h, v in zip(self.ret[done].tolist(), self.t[done].tolist(), fell[done].tolist(),
                                        self.left_box[done].tolist(), self.done_hit[done].tolist(), self.hit_speed[done].tolist()):
                self.episodes.append((r, f, b, l, h, v))
            self.reset(done)
        return self.observe(), rew, done, timeout

    def _strike_check(self):
        """After each physics step: the first contact with the target decides the strike (Blueprint L2)."""
        hand = self.geom_xpos[:, self.hand]
        speed = (hand - self.hand_prev).norm(dim=-1) / 0.005
        self.hand_prev = hand.clone()
        n = self.nacon[0]
        idx = torch.arange(self.con_geom.shape[0], device=self.device)
        g0, g1 = self.con_geom[:, 0].long(), self.con_geom[:, 1].long()
        on = (idx < n) & ((g0 == self.tgeom) | (g1 == self.tgeom))
        if not bool(on.any()):
            return
        w = self.con_world.long()[on]
        other = torch.where(g0[on] == self.tgeom, g1[on], g0[on])
        sign = torch.where(g0[on] == self.tgeom, -1.0, 1.0)[:, None]
        normal = self.con_frame[on][:, 0, :] * sign                       # from the hand towards the target
        palm_dir = -self.geom_xmat[w, self.hand][:, :, 2]                   # hand box -z = palm
        palm = (other == self.hand) & ((normal * palm_dir).sum(-1) > 0.5)
        hit = torch.zeros(self.N, dtype=torch.bool, device=self.device)
        hit[w] = True
        good = torch.zeros_like(hit)
        good[w[palm]] = True
        new = hit & ~self.struck
        if bool(new.any()):
            v = speed[new]
            clean = good[new] & (v >= 8.0)
            self.hit_ok[new], self.hit_speed[new] = clean, v
            self.bonus[new] += torch.where(clean, 30.0 + 3.0 * v, torch.where(good[new], 5.0 + 2.0 * v, torch.full_like(v, -10.0)))
            self.struck |= new
            self.settle[new] = 75

    def _fallen(self):
        n = self.nacon[0]
        idx = torch.arange(self.con_geom.shape[0], device=self.device)
        g0, g1 = self.con_geom[:, 0].long(), self.con_geom[:, 1].long()
        hit = (idx < n) & ((self.surface[g0] & self.bad[g1]) | (self.surface[g1] & self.bad[g0]))
        fell = torch.zeros(self.N, dtype=torch.bool, device=self.device)
        fell[self.con_world[hit].long()] = True
        return fell | (self.qpos[:, 2] < 0.45)

    def _reward(self, a, fell):
        R = quat_mat(self.qpos[:, 3:7])
        upright = R[:, 2, 2]                                           # 1 when the pelvis is vertical
        height = torch.exp(-((self.qpos[:, 2] - self.h0) ** 2) / 0.01)
        posture = torch.exp(-((self.qpos[:, self.jq] - self.stand) ** 2).sum(-1) / 1.0)
        smooth = ((a - self.prev_action) ** 2).sum(-1)
        r = 1.0 + 0.5 * upright.clamp(min=0) + 0.5 * height + 0.3 * posture - 0.01 * smooth
        if self.strike:   # L2: no pay for waiting; reach the target fast, a clean palm strike pays most, then stay up
            hand, tgt = self.geom_xpos[:, self.hand], self.mocap[:, 0]
            d = (tgt - hand).norm(dim=-1)
            u = (tgt - hand) / d[:, None].clamp(min=1e-6)
            toward = (u * (hand - self.hand_prev_ctrl) / 0.02).sum(-1).clamp(0, 12)
            reach = torch.exp(-d ** 2 / 0.05) + 0.1 * toward * torch.exp(-d ** 2 / 0.1)
            before = -0.05 + 0.3 * upright.clamp(min=0) + reach - 0.01 * smooth
            after = 0.5 + 0.5 * upright.clamp(min=0) + 0.5 * height
            r = torch.where(self.struck, after, before) + self.bonus
            self.bonus.zero_()
            self.hand_prev_ctrl = hand.clone()
        if self.cfg.get("foot_box"):   # L1 guard: feet in the box, facing the opponent (+X), head where it started
            bx, by = self.cfg["foot_box"]
            d = (self.geom_xpos[:, self.feet, :2] - self.foot0).abs()
            out = (d[..., 0] > bx).any(-1) | (d[..., 1] > by).any(-1)
            self.left_box |= out
            facing = R[:, 0, 0]                                        # cos(yaw): pelvis x-axis on the world x-axis
            head = torch.exp(-((self.xpos[:, self.head, :2] - self.head0) ** 2).sum(-1) / 0.01)
            anchor = torch.exp(-(d ** 2).sum((-1, -2)) / 0.005)         # feet on their marks, before the hard limit
            r = r + 0.3 * facing + 0.5 * anchor + (0.0 if self.strike else 0.3) * head   # a striker must lean in
        else:
            out = torch.zeros_like(fell)
        return torch.where(fell | out, torch.full_like(r, -5.0), r), out

    # ------------------------------------------------------------------ observe (BRAIN_IO)
    def observe(self):
        q, v = self.qpos, self.qvel
        R = quat_mat(q[:, 3:7])
        yaw = torch.atan2(R[:, 1, 0], R[:, 0, 0])
        c, s = torch.cos(yaw), torch.sin(yaw)
        lin = v[:, 0:3]
        heading = torch.stack([c * lin[:, 0] + s * lin[:, 1], -s * lin[:, 0] + c * lin[:, 1], lin[:, 2]], -1)
        bx, by = self.q0[0] - q[:, 0], self.q0[1] - q[:, 1]          # box centre = start pelvis x, y
        box = torch.stack([c * bx + s * by, -s * bx + c * by, torch.zeros_like(bx)], -1)
        role_clock = torch.zeros(self.N, 2, device=self.device)
        if self.strike:
            rel = self.mocap[:, 0] - q[:, 0:3]
            box = torch.stack([c * rel[:, 0] + s * rel[:, 1], -s * rel[:, 0] + c * rel[:, 1], rel[:, 2]], -1)
            role_clock[:, 0] = 1.0
            role_clock[:, 1] = 1.0 - self.t.float() / self.T
        obs = torch.cat([
            q[:, self.jq] - self.stand,
            v[:, self.jv] * 0.1,
            q[:, 2:3],
            -R[:, 2, :],                     # R^T (0,0,-1)
            heading,
            v[:, 3:6] * 0.25,
            self.prev_action,
            role_clock,                                   # role, turn clock remaining
            box,                                          # role 0: own foot-box centre; striker: target - pelvis (heading)
            torch.zeros(self.N, 3, device=self.device),   # opponent hand: none here
        ], -1)
        if self.randomize:
            noise = torch.zeros(OBS, device=self.device)
            noise[:30], noise[30:60], noise[61:64] = 0.005, 0.05, 0.01
            obs = obs + torch.randn_like(obs) * noise
        return obs
