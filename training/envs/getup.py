"""Get-up env (level G1, item 44, 2026-11-04): N worlds of one athlete on the GPU, each episode starting from a real
knockdown state.

The states (`getup_states.py`) are the moment a trained brain went down in the slap env (envs/slap.py) under heavy
slaps and shoves: its first non-sole contact with the floor or the podium, qpos and qvel as they were. That is the
state a get-up brain inherits when the director (DOCS/20261104/director_transitions.md) hands the body over, so it is
the state it trains on (AGENTS.md §3: "get-up from real falls").

Everything a hand-over needs is shared with the match brain (envs/slap.py, which this env reuses): the observation
(BRAIN_IO, interface 4, role 0 = the own foot box), the joint order, the action map, the low-pass, the rate limit,
Hill's law, the 200/50 Hz timing and the motion-style terms (smooth, jerk, >10 Hz). At the start the goals are the
body's own joint angles and the previous action is the action that maps to them, so the first decision continues
from the body, never from a snapped pose.

Reward per step: pelvis height and head height (share of standing), upright, the CoM over the feet (item 46: within
5 cm of the stance offset), minus the motion-style costs; +100 once UP (pelvis >= 0.85 x stand height, pelvis
upright >= 0.9, only the soles touch floor or podium) has held for 1 s, which ends the episode as a bootstrapped
time-out (the value of standing on), as does the 10 s clock.
"""
from __future__ import annotations

import json

import mujoco
import numpy as np
import torch
import warp as wp

from constants import DECIM, models_dir
from envs import common
from envs.common import blown_up, contact_slots
from envs.slap import SlapEnv, hill, quat_mat

UP_Z, UP_TILT, UP_HOLD = 0.85, 0.9, 50   # share of stand height, pelvis z-axis vs world up, decisions (1 s)


def states_path(character, physics):
    return models_dir(physics) / "getup" / f"{character}_states.npz"


class GetUpEnv(SlapEnv):
    def __init__(self, character="grandpa", level="G1", num_envs=4096, seed=0, randomize=True, states=None):
        from constants import physics
        path = states or states_path(character, physics())
        if not path.exists():
            raise FileNotFoundError(f"{path}: run getup_states.py --char {character} --physics {physics()} first")
        bank = np.load(path)
        self.bank_q = torch.tensor(bank["qpos"], dtype=torch.float32, device="cuda")
        self.bank_v = torch.tensor(bank["qvel"], dtype=torch.float32, device="cuda")
        super().__init__(character, level, num_envs, seed, randomize)
        m, dev = self.m, self.device
        assert self.bank_q.shape[1] == m.nq, f"{path}: {self.bank_q.shape[1]} qpos, the model has {m.nq}"
        self.T = int(self.cfg.get("seconds", 10.0) * 50)
        # the stance CoM offset (CoM - mid-feet) at the stand pose: the CoM-recovery reference for every episode
        d0 = mujoco.MjData(m)
        d0.qpos[:] = self.q0.cpu().numpy()
        mujoco.mj_forward(m, d0)
        feet = self.feet.cpu().numpy()
        self.com_stance = torch.tensor(d0.subtree_com[self.pelvis, :2] - d0.geom_xpos[feet, :2].mean(0), device=dev, dtype=torch.float32)
        self.head_z0 = float(d0.geom_xpos[m.geom("head").id, 2])
        self.head_g = m.geom("head").id
        self.up_t = torch.zeros(self.N, dtype=torch.long, device=dev)
        self.done_up = torch.zeros(self.N, dtype=torch.bool, device=dev)
        self.ep_up = torch.zeros(self.N, dtype=torch.bool, device=dev)
        self.t.zero_()
        self.reset(torch.ones(self.N, dtype=torch.bool, device=dev))

    def reset(self, mask):
        super().reset(mask)   # randomisation, fatigue, the foot box (the stand pose's), memory, the slap's bookkeeping
        if not hasattr(self, "up_t"):
            return            # SlapEnv.__init__'s first reset: the bank is loaded, the rest is set up after it
        n = int(mask.sum())
        if n == 0:
            return
        pick = torch.randint(0, self.bank_q.shape[0], (n,), device=self.device)
        self.qpos[mask], self.qvel[mask] = self.bank_q[pick], self.bank_v[pick]
        q = self.qpos[mask][:, self.jq]
        goal = torch.max(torch.min(q, self.hi), self.lo)   # continue from the body as it is
        self.goal[mask] = goal
        a = torch.where(goal > self.stand, (goal - self.stand) / (self.hi - self.stand).clamp(min=1e-6),
                        (goal - self.stand) / (self.stand - self.lo).clamp(min=1e-6))
        self.prev_action[mask], self.prev2_action[mask] = a.clamp(-1, 1), a.clamp(-1, 1)
        act = self.mact[mask]
        act[:, self.act_id] = q        # every muscle settled at its joint's angle (brain_cpu.settle_muscles)
        self.mact[mask] = act
        self.up_t[mask], self.ep_up[mask] = 0, False
        self.mocap[mask, 0] = self.tpark
        wp.capture_launch(self.g_kin)

    def _support(self):
        """(N,) True when only the soles touch floor or podium (the hands and knees are off them)."""
        valid, g0, g1, w = contact_slots(self, self.N)
        bad = valid & ((self.surface[g0] & self.bad[g1]) | (self.surface[g1] & self.bad[g0]))
        on = torch.zeros(self.N, device=self.device).scatter_reduce_(0, w, bad.float(), "amax")
        return on < 0.5

    def step(self, action):
        a = action.clamp(-1.0, 1.0)
        tgt = torch.clamp(self.stand + torch.where(a > 0, a * (self.hi - self.stand), a * (self.stand - self.lo)), self.lo, self.hi)
        tgt = common.lowpass(tgt, self.goal, self.lpf)
        ms = self.max_step * self.stamina[:, None]
        tgt = torch.max(torch.min(tgt, self.goal + ms), self.goal - ms)
        old, self.goal = self.goal, tgt
        for s in range(DECIM):
            self.ctrl[:, self.act_id] = torch.where((s >= self.delay)[:, None], tgt, old)
            self.xfrc.zero_()
            fr = self.frange[:, self.act_id].clone()
            self.frange[:, self.act_id] = hill(fr, self.qvel[:, self.jv], self.vmax)
            wp.capture_launch(self.g_step)
            self.frange[:, self.act_id] = fr
        wp.capture_launch(self.g_kin)

        boom = blown_up(self.qpos, self.qvel)
        self.blowups += boom.sum()
        R = quat_mat(self.qpos[:, 3:7])
        upright = R[:, 2, 2]
        z = self.qpos[:, 2]
        up_now = (z >= UP_Z * self.h0) & (upright >= UP_TILT) & self._support()
        self.up_t = torch.where(up_now, self.up_t + 1, torch.zeros_like(self.up_t))
        up = self.up_t >= UP_HOLD
        self.ep_up |= up
        hz = self.geom_xpos[:, self.head_g, 2]
        smooth = ((a - self.prev_action) ** 2).sum(-1)
        jerk = ((a - 2.0 * self.prev_action + self.prev2_action) ** 2).sum(-1)
        v = self.qvel[:, self.jv]
        osc, self.hp = common.hf_oscillation(v, self.v_prev, self.hp)
        self.v_prev = v
        com_pay, self.com_e = common.com_recovery(self.com[:, self.pelvis, :2], self.geom_xpos[:, self.feet, :2], self.com_stance)
        rew = (1.5 * (z / self.h0).clamp(0, 1) + 0.5 * (hz / self.head_z0).clamp(0, 1) + 0.5 * upright.clamp(min=0)
               + com_pay * up_now.float() - 0.01 * smooth - 0.002 * jerk - osc + 100.0 * up.float())
        rew = torch.where(boom, torch.full_like(rew, -5.0), rew)
        self.prev2_action, self.prev_action = self.prev_action, a
        self.t += 1
        self.ret += rew
        self.fell_now = boom                     # the exam's "fell": only a blow-up (lying down is where it starts)
        done = boom | up | (self.t >= self.T)
        # up ends the episode like a time-out (bootstrapped): its value is the standing that follows. As a terminal it
        # made lying until the clock (bootstrapped) worth more than getting up (smoke 2026-11-04: up 1.0 -> 0.19)
        timeout = done & ~boom
        self.done_up = done & up
        if done.any():
            cols = (self.ret, self.t, boom, self.ep_up)
            for r, l, f, u in zip(*(c[done].tolist() for c in cols)):
                self.episodes.append(dict(ret=r, len=l, fell=f, left_box=False, hit=u, speed=0.0, role=0, absorbed=False,
                                          com_err=0.0, whiff=False, up=u))
            self.reset(done)
        return self.observe(), rew, done, timeout
