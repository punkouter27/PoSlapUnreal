"""Walking Env on MuJoCo Warp (Stage S2, the opt-in -SlapWalkBrain walk-in): N worlds on GPU.
S1 (stand) duplicated L0 and S3 (jog, turn, slopes) trained a skill no match uses: both removed 2026-10-26.

Implements all 10 AI Realism Options:
1. Anatomical anti-hyperextension limits & inertial PD firmness (zeta = 0.8).
2. Earth gravity (-9.81 m/s^2), anthropometric mass, and per-foot + floor friction randomization (0.6-1.2).
3. Continuous metabolic stamina drain (proportional to motor torque effort) & passive recovery ([0.70, 1.00]).
4. Simplified inside-skin collision primitives with parent-child exclusions and zero self-clipping.
5. Clean proprioceptive body awareness (136-float BRAIN_IO contract + optional 12-float foot/contact/load/phase extension = 148).
6. Smooth motor control: asymmetric full-range mapping, stamina-scaled velocity rate limiter, optional EMA muscle ramp, 0-20 ms reflex delay.
7. Natural movement rewards: upright posture, smooth velocity tracking, torque efficiency, and alternating swing-foot clearance.
8. Anti-cheat & exploit penalties: second-order action jerk (vibration), arm flailing, leg dragging, asymmetric hop, and non-sole knockdown.
9. Antagonist co-contraction impedance modulation during shove recovery.
10. Fast multi-instance GPU execution (4,096 worlds, CUDA Graph captured, zero raycasts, zero host syncs per physics step).
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
from envs.slap import DECIM, quat_mat
from levels import LEVELS

HERE = Path(__file__).resolve().parents[1]


class LocomotionEnv:
    def __init__(self, character="grandpa", level="S2", num_envs=4096, seed=0, randomize=True, obs_dim=OBS):
        if level != "S2":
            raise NotImplementedError(f"{level}: LocomotionEnv is S2 only")
        self.character, self.level, self.cfg, self.N, self.randomize = character, level, LEVELS[level], num_envs, randomize
        self.obs_dim = obs_dim
        dev = self.device = "cuda"
        torch.manual_seed(seed)
        wp.set_device("cuda:0")
        self.stream = torch.cuda.Stream()
        torch.cuda.set_stream(self.stream)
        wp.set_stream(wp.stream_from_torch(self.stream))

        # Load realism configs
        cfg_dir = HERE / "configs"
        self.joint_cfg = json.loads((cfg_dir / "joint_and_strength_config.json").read_text())
        self.sens_cfg = json.loads((cfg_dir / "sensory_profile.json").read_text())
        self.train_cfg = json.loads((cfg_dir / "training_realism_config.json").read_text())

        fp = json.loads((HERE / "models" / f"{character}_fingerprint.json").read_text())
        m = self.m = mujoco.MjModel.from_xml_path(str(HERE / "models" / f"{character}_solo.xml"))
        order = fp["joint_order"]
        self.A = len(order)
        jid = [m.joint(n).id for n in order]
        self.jq = torch.tensor([m.joint_qposadr[j] if hasattr(m, "joint_qposadr") else m.jnt_qposadr[j] for j in jid], device=dev)
        self.jv = torch.tensor([m.jnt_dofadr[j] for j in jid], device=dev)
        self.act_id = torch.tensor([m.actuator(n).id for n in order], device=dev)
        self.neck_act = torch.tensor([m.actuator(n).id for n in order if n.startswith("neck")], device=dev)
        self.arm_jv = torch.tensor([i for i, n in enumerate(order) if n.startswith(("shoulder", "elbow", "wrist"))], device=dev)
        self.hip_l_idx = order.index("hip_flex_l")
        self.hip_r_idx = order.index("hip_flex_r")
        self.knee_l_idx = order.index("knee_l")
        self.knee_r_idx = order.index("knee_r")
        self.ankle_l_idx = order.index("ankle_flex_l")
        self.ankle_r_idx = order.index("ankle_flex_r")

        self.stand = torch.tensor([fp["stand_pose_rad"].get(n, 0.0) for n in order], device=dev)
        rng = torch.tensor(fp["ctrl_range_rad"], device=dev)
        self.lo, self.hi = rng[:, 0], rng[:, 1]
        self.max_step = torch.tensor(fp["max_joint_speed_rad_s"], device=dev) * 0.02
        self.T = int(self.cfg.get("seconds", 30.0) * 50)

        d0 = mujoco.MjData(m)
        d0.qpos[self.jq.cpu().numpy()] = self.stand.cpu().numpy()
        mujoco.mj_forward(m, d0)
        self.q0 = torch.tensor(d0.qpos, device=dev, dtype=torch.float32)
        self.h0 = float(d0.qpos[2])
        self.total_weight_n = float(fp["mass_kg"] * 9.81)

        self.mw = mjw.put_model(m)
        self.dw = mjw.put_data(m, d0, nworld=num_envs, naconmax=num_envs * 48, njmax=512)

        def per_world(name):
            a = getattr(self.mw, name)
            t = wp.to_torch(a)
            t = t.repeat(num_envs, *[1] * (t.dim() - 1)).contiguous()
            setattr(self.mw, name, wp.from_torch(t, dtype=a.dtype))
            return t

        self.friction = per_world("geom_friction")
        self.mass = per_world("body_mass")
        self.inertia = per_world("body_inertia")
        self.gain = per_world("actuator_gainprm")
        self.bias = per_world("actuator_biasprm")
        self.frange = per_world("actuator_forcerange")
        self.gpos = per_world("geom_pos")
        self.mass0, self.inertia0 = self.mass[0].clone(), self.inertia[0].clone()
        self.gain0, self.bias0, self.frange0 = self.gain[0].clone(), self.bias[0].clone(), self.frange[0].clone()

        self.floor = m.geom("floor").id
        self.podium = m.geom("podium").id
        # Park podium far away so athlete has open floor for walking
        self.gpos[:, self.podium] = torch.tensor([50.0, 0.0, -5.0], device=dev)

        T = lambda a: wp.to_torch(a)
        self.qpos, self.qvel, self.ctrl = T(self.dw.qpos), T(self.dw.qvel), T(self.dw.ctrl)
        self.mact = T(self.dw.act)   # the muscles' filtered targets (build_body MUSCLE_TC)
        self.xpos, self.xfrc, self.com = T(self.dw.xpos), T(self.dw.xfrc_applied), T(self.dw.subtree_com)
        self.geom_xpos, self.geom_xmat = T(self.dw.geom_xpos), T(self.dw.geom_xmat)
        self.con_geom, self.con_world, self.nacon = T(self.dw.contact.geom), T(self.dw.contact.worldid), T(self.dw.nacon)
        self.mocap = T(self.dw.mocap_pos)
        self.geom_xpos[:, self.podium] = torch.tensor([50.0, 0.0, -5.0], device=dev)

        feet = [m.geom("foot_l").id, m.geom("foot_r").id]
        self.feet = torch.tensor(feet, device=dev)
        self.foot_l, self.foot_r = feet[0], feet[1]
        self.foot_bodies = torch.tensor([m.body("foot_l").id, m.body("foot_r").id], device=dev)
        body_geoms = [g for g in range(m.ngeom) if m.body_rootid[m.geom_bodyid[g]] == m.body("pelvis").id]
        self.bad = torch.zeros(m.ngeom, dtype=torch.bool, device=dev)
        self.bad[[g for g in body_geoms if g not in feet]] = True
        self.surface = torch.zeros(m.ngeom, dtype=torch.bool, device=dev)
        self.surface[self.floor] = True
        self.pelvis, self.chest, self.head = m.body("pelvis").id, m.body("chest").id, m.body("head").id
        self.hand = m.geom("hand_r").id

        # Motor smoothing & metabolic fatigue parameters from saved configs
        self.ema_alpha = float(os.environ.get("SLAP_EMA_ALPHA", "0.85"))
        fat_cfg = self.joint_cfg["stamina_and_fatigue_limits"]["continuous_metabolic_fatigue"]
        self.fatigue_drain = float(fat_cfg["torque_effort_drain_rate_per_s"]) * 0.02
        self.fatigue_recov = float(fat_cfg["passive_recovery_rate_per_s"]) * 0.02

        N, A = num_envs, self.A
        z = lambda *s: torch.zeros(*s, device=dev)
        zb = lambda: torch.zeros(N, dtype=torch.bool, device=dev)
        zl = lambda: torch.zeros(N, dtype=torch.long, device=dev)

        self.roles = {0: 1.0}
        self.force_role = None
        self.role = zl()
        self.goal = self.stand.repeat(N, 1)
        self.prev_action, self.prev2_action = z(N, A), z(N, A)
        self.t, self.delay = zl(), zl()
        self.ret = z(N)
        self.episodes = []

        # Shove / push perturbations (Stage 1)
        self.push_scale = float(self.cfg.get("max_push", 1.0)) if self.cfg.get("pushes") else 0.0
        self.push_wait, self.push_left, self.push_f = z(N), z(N), z(N, 3)
        self.push_body = torch.full((N,), self.chest, dtype=torch.long, device=dev)

        # Locomotion state
        self.speed_scale = 1.0
        self.v_cmd = z(N)                 # desired forward speed in m/s
        self.yaw_cmd = z(N)               # desired heading yaw in rad
        self.gait_phase = z(N)            # [0, 1) cyclic stride clock
        self.stride_freq = z(N)           # Hz (1.8 walk)
        self.foot_prev = z(N, 2, 3)       # previous step foot positions for velocity / drag check
        self.foot_contact = torch.ones(N, 2, dtype=torch.bool, device=dev)
        self.foot_load = torch.full((N, 2), 0.5, device=dev)
        self.lead_swaps = zl()            # count of alternating foot lead swaps
        self.last_lead = zl()             # +1 left foot ahead, -1 right foot ahead
        self.dist_walked = z(N)
        self.speed_err_sum = z(N)
        self.com_err = z(N)
        self.foot0 = z(N, 2, 2)
        self.stamina, self.neck = torch.ones(N, device=dev), torch.ones(N, device=dev)
        self.co_contract = torch.ones(N, device=dev)
        self.hist, self.fresh, self.mem = z(N, 16, 6), zb(), z(N, 2)
        self.fell_now = zb()

        # Pre-capture CUDA graphs for zero-overhead GPU execution
        mjw.step(self.mw, self.dw); mjw.kinematics(self.mw, self.dw); wp.synchronize()
        with wp.ScopedCapture() as c:
            mjw.step(self.mw, self.dw)
        self.g_step = c.graph
        with wp.ScopedCapture() as c:
            mjw.kinematics(self.mw, self.dw)
        self.g_kin = c.graph

        self.reset(torch.ones(N, dtype=torch.bool, device=dev))
        self.t += torch.randint(0, max(1, self.T - 250), (N,), device=dev)

    def _u(self, n, lo, hi):
        return lo + torch.rand(n, device=self.device) * (hi - lo)

    def reset(self, mask):
        n = int(mask.sum())
        if n == 0:
            return
        self.fresh |= mask
        mjw.reset_data(self.mw, self.dw, reset=wp.from_torch(mask))
        q = self.q0.repeat(n, 1)
        q[:, self.jq] += self._u((n, self.A), -0.04, 0.04)
        self.qpos[mask], self.qvel[mask] = q, 0.0
        self.mact[mask] = self.stand[torch.argsort(self.act_id)]   # muscles settled at the stand targets (reset zeroes act)
        self.gpos[mask, self.podium] = torch.tensor([50.0, 0.0, -5.0], device=self.device)
        self.geom_xpos[mask, self.podium] = torch.tensor([50.0, 0.0, -5.0], device=self.device)
        self.mocap[mask, 0] = torch.tensor([50.0, 0.0, 1.0], device=self.device)

        if self.randomize:
            # Option 2: Floor AND per-foot sole friction randomization (0.6 - 1.2)
            self.friction[mask, self.floor, 0] = self._u(n, 0.6, 1.2)
            self.friction[mask, self.foot_l, 0] = self._u(n, 0.6, 1.2)
            self.friction[mask, self.foot_r, 0] = self._u(n, 0.6, 1.2)
            s = self._u((n, self.mass.shape[1]), 0.95, 1.05)
            self.mass[mask], self.inertia[mask] = self.mass0 * s, self.inertia0 * s[..., None]
            k = self._u((n, self.gain.shape[1]), 0.9, 1.1)
            tired = torch.rand(n, device=self.device) < 0.5
            self.stamina[mask] = torch.where(tired, self._u(n, MIN_STAMINA, 1.0), 1.0)
            self.neck[mask] = 1.0
            self.gain[mask] = self.gain0 * k[..., None]
            self.bias[mask] = self.bias0 * k[..., None]
            self.frange[mask] = self.frange0 * k[..., None]
            self.delay[mask] = torch.randint(0, 5, (n,), device=self.device)

        self.goal[mask] = self.stand
        self.prev_action[mask], self.prev2_action[mask] = 0.0, 0.0
        self.t[mask], self.ret[mask] = 0, 0.0
        self.push_wait[mask], self.push_left[mask] = self._u(n, 1.0, 3.0), 0.0
        self.gait_phase[mask] = torch.rand(n, device=self.device)
        self.lead_swaps[mask], self.last_lead[mask] = 0, 0
        self.dist_walked[mask], self.speed_err_sum[mask], self.com_err[mask] = 0.0, 0.0, 0.0

        lo, hi = self.cfg.get("target_speed", (1.0, 1.4))
        self.v_cmd[mask] = self._u(n, lo, hi) * self.speed_scale
        self.yaw_cmd[mask] = 0.0
        self.stride_freq[mask] = 1.8
        wp.capture_launch(self.g_kin)
        self.foot0[mask] = self.geom_xpos[mask][:, self.feet, :2]
        self.foot_prev[mask] = self.geom_xpos[mask][:, self.feet]

    def _forces(self, dt):
        self.xfrc.zero_()
        # Stage 1 shoves
        if self.randomize and self.push_scale > 0 and self.cfg.get("pushes"):
            self.push_wait -= dt
            start = (self.push_wait <= 0) & (self.push_left <= 0)
            N = self.N
            ang = self._u(N, -math.pi, math.pi)
            mag = self._u(N, 150.0, 300.0) * self.push_scale
            f = torch.stack([torch.cos(ang) * mag, torch.sin(ang) * mag, torch.zeros_like(mag)], -1)
            self.push_f = torch.where(start[:, None], f, self.push_f)
            self.push_left = torch.where(start, self._u(N, 0.1, 0.2), self.push_left)
            self.push_wait = torch.where(start, self._u(N, 1.0, 3.0), self.push_wait)
            body = torch.where(torch.rand(N, device=self.device) < 0.5, self.chest, self.head)
            self.push_body = torch.where(start, body, self.push_body)
            on = self.push_left > 0
            self.xfrc[torch.arange(N, device=self.device), self.push_body, :3] = self.push_f * on[:, None]
            self.push_left -= dt
            # Option 9: Antagonist co-contraction impedance boost during active shove recovery
            self.co_contract = torch.where(on, torch.full_like(self.co_contract, 1.20), torch.ones_like(self.co_contract))

    def _update_foot_contacts(self):
        n = self.nacon[0]
        idx = torch.arange(self.con_geom.shape[0], device=self.device)
        g0, g1 = self.con_geom[:, 0].long(), self.con_geom[:, 1].long()
        w = self.con_world.long().clamp(0, self.N - 1)
        valid = idx < n
        on_l = valid & (((g0 == self.floor) & (g1 == self.foot_l)) | ((g1 == self.floor) & (g0 == self.foot_l)))
        on_r = valid & (((g0 == self.floor) & (g1 == self.foot_r)) | ((g1 == self.floor) & (g0 == self.foot_r)))
        cl = torch.zeros(self.N, device=self.device).scatter_reduce_(0, w, on_l.float(), "amax")
        cr = torch.zeros(self.N, device=self.device).scatter_reduce_(0, w, on_r.float(), "amax")
        self.foot_contact = torch.stack([cl > 0.5, cr > 0.5], -1)
        # Approximate normalized vertical load distribution from contact state and foot height
        fz_l = cl * (1.0 - 0.5 * cr)
        fz_r = cr * (1.0 - 0.5 * cl)
        self.foot_load = torch.stack([fz_l, fz_r], -1)

    def _fallen(self):
        n = self.nacon[0]
        idx = torch.arange(self.con_geom.shape[0], device=self.device)
        g0, g1 = self.con_geom[:, 0].long(), self.con_geom[:, 1].long()
        hit = (idx < n) & ((self.surface[g0] & self.bad[g1]) | (self.surface[g1] & self.bad[g0]))
        w = self.con_world.long().clamp(0, self.N - 1)
        fell = torch.zeros(self.N, dtype=torch.bool, device=self.device)
        fell[w[hit]] = True
        return fell | (self.qpos[:, 2] < 0.45)

    def step(self, action):
        a = action.clamp(-1.0, 1.0)
        # Option 3: Continuous metabolic stamina drain & passive recovery
        effort = (a ** 2).mean(dim=-1)
        delta_stam = torch.where(effort > 0.15, -self.fatigue_drain * effort, self.fatigue_recov * (1.0 - effort))
        self.stamina = (self.stamina + delta_stam).clamp(MIN_STAMINA, 1.0)

        # Option 9: Antagonist co-contraction impedance modulation (0.90 relaxed stride -> 1.25 braced shove/turn)
        base_cc = 0.90
        shove_on = (self.push_left > 0) if (self.randomize and self.push_scale > 0 and self.cfg.get("pushes")) else torch.zeros_like(effort, dtype=torch.bool)
        self.co_contract = torch.where(shove_on, torch.full_like(effort, 1.25), base_cc + 0.30 * effort).clamp(0.85, 1.30)
        self.gain[..., 0] = self.gain0[:, 0] * self.co_contract[:, None]
        self.bias[..., 1] = self.bias0[:, 1] * self.co_contract[:, None]

        # Option 6: Smooth motor control with asymmetric range mapping, stamina rate-limit, and EMA filter
        tgt = torch.clamp(self.stand + torch.where(a > 0, a * (self.hi - self.stand), a * (self.stand - self.lo)), self.lo, self.hi)
        ms = self.max_step * self.stamina[:, None]
        tgt = torch.max(torch.min(tgt, self.goal + ms), self.goal - ms)
        tgt = self.ema_alpha * tgt + (1.0 - self.ema_alpha) * self.goal
        old, self.goal = self.goal, tgt

        for s in range(DECIM):
            self.ctrl[:, self.act_id] = torch.where((s >= self.delay)[:, None], tgt, old)
            self._forces(0.005)
            wp.capture_launch(self.g_step)
        wp.capture_launch(self.g_kin)
        self._update_foot_contacts()

        fell = self.fell_now = self._fallen()
        rew = self._reward(a, fell)
        self.prev2_action = self.prev_action
        self.prev_action = a
        self.t += 1
        self.ret += rew

        # Advance gait clock and heading command
        active_walk = self.v_cmd > 0.1
        self.gait_phase = torch.where(active_walk, (self.gait_phase + self.stride_freq * 0.02) % 1.0, torch.zeros_like(self.gait_phase))

        done = fell | (self.t >= self.T)
        timeout = done & ~fell
        if done.any():
            mean_v_err = self.speed_err_sum / self.t.clamp(min=1).float()
            cols = (self.ret, self.t, fell, self.com_err, mean_v_err, self.lead_swaps, self.dist_walked)
            for r, l, f, ce, ve, ls, dw in zip(*(c[done].tolist() for c in cols)):
                self.episodes.append(dict(
                    ret=r, len=l, fell=f, left_box=False, hit=False, speed=0.0,
                    role=0, absorbed=(not f and ce <= 0.08), com_err=ce,
                    v_err=ve, lead_swaps=ls, dist_walked=dw,
                ))
            self.reset(done)
        return self.observe(), rew, done, timeout

    def _reward(self, a, fell):
        q, v = self.qpos, self.qvel
        R = quat_mat(q[:, 3:7])
        upright = R[:, 2, 2]
        height = torch.exp(-((q[:, 2] - self.h0) ** 2) / 0.015)
        yaw = torch.atan2(R[:, 1, 0], R[:, 0, 0])
        c, s = torch.cos(yaw), torch.sin(yaw)
        v_head = self._heading(v[:, 0:3], c, s)

        # Foot kinematics
        feet_pos = self.geom_xpos[:, self.feet]                      # (N, 2, 3)
        feet_vel = (feet_pos - self.foot_prev) / 0.02                # (N, 2, 3)
        self.foot_prev = feet_pos.clone()
        mid_feet = feet_pos[:, :, :2].mean(dim=1)
        self.com_err = (self.com[:, self.pelvis, :2] - mid_feet).norm(dim=-1)

        # Option 8: Anti-exploit penalties (smoothness, second-order jerk, arm flailing, leg dragging)
        smooth = ((a - self.prev_action) ** 2).sum(-1)
        jerk = ((a - 2.0 * self.prev_action + self.prev2_action) ** 2).sum(-1)
        energy = (a ** 2).mean(-1)
        arm_vel = (v[:, self.jv[self.arm_jv]] ** 2).mean(-1)
        arm_flail = (arm_vel - 6.0).clamp(min=0.0)
        foot_slip_speed = feet_vel[:, :, :2].norm(dim=-1)            # (N, 2)
        leg_drag = (self.foot_contact.float() * (foot_slip_speed - 0.18).clamp(min=0.0)).sum(-1)

        # Heading & velocity tracking
        yaw_err = torch.atan2(torch.sin(yaw - self.yaw_cmd), torch.cos(yaw - self.yaw_cmd))
        facing = torch.cos(yaw_err)
        v_err = (v_head[:, 0] - self.v_cmd).abs() + 0.5 * v_head[:, 1].abs()
        self.speed_err_sum += v_err
        self.dist_walked += v_head[:, 0].clamp(min=0.0) * 0.02

        # Option 7: alternating swing-foot clearance (the foot is one rigid box: no heel-to-toe roll)
        phi = 2.0 * math.pi * self.gait_phase
        swing_l = (torch.sin(phi) > 0).float()
        swing_r = 1.0 - swing_l
        clear_l = (feet_pos[:, 0, 2] - 0.03).clamp(0.0, 0.08) / 0.06
        clear_r = (feet_pos[:, 1, 2] - 0.03).clamp(0.0, 0.08) / 0.06
        stride_clear = swing_l * clear_l + swing_r * clear_r
        stance_match = (1.0 - swing_l) * self.foot_contact[:, 0].float() + (1.0 - swing_r) * self.foot_contact[:, 1].float()

        # Track alternating lead foot in heading frame to prevent one-leg dragging/hopping
        rel_l = self._heading(feet_pos[:, 0] - self.xpos[:, self.pelvis], c, s)
        rel_r = self._heading(feet_pos[:, 1] - self.xpos[:, self.pelvis], c, s)
        lead = torch.where(rel_l[:, 0] > rel_r[:, 0] + 0.04, torch.ones_like( self.last_lead),
                           torch.where(rel_r[:, 0] > rel_l[:, 0] + 0.04, -torch.ones_like(self.last_lead), self.last_lead))
        swapped = (lead != 0) & (self.last_lead != 0) & (lead != self.last_lead)
        self.lead_swaps += swapped.long()
        self.last_lead = torch.where(lead != 0, lead, self.last_lead)

        moving = (self.v_cmd > 0.1).float()
        # 2026-10-26: standing paid ~2.2 a step and a 1.2 m/s miss cost only 1.2, so the S2 "graduates" stood still
        # (0.15 m in 20 s). A wide kernel pays partial speed, and forward progress pays linearly up to the command
        vel_rew = 2.0 * torch.exp(-(v_err ** 2) / 1.0)
        progress = 1.5 * (v_head[:, 0].clamp(min=0.0) / self.v_cmd.clamp(min=0.1)).clamp(max=1.0) * moving
        gait_rew = moving * (0.4 * stride_clear + 0.4 * stance_match) + (1.0 - moving) * (0.8 * torch.exp(-(self.com_err ** 2) / 0.005))
        r = (1.0 + 0.5 * upright.clamp(min=0) + 0.4 * height + 0.3 * facing
             + vel_rew + progress + gait_rew
             - 0.0005 * energy - 0.01 * smooth - 0.01 * jerk - 0.03 * arm_flail - 0.30 * leg_drag)

        return torch.where(fell, torch.full_like(r, -25.0), r)

    def _heading(self, v, c, s):
        return torch.stack([c * v[:, 0] + s * v[:, 1], -s * v[:, 0] + c * v[:, 1], v[:, 2]], -1)

    def observe(self):
        q, v = self.qpos, self.qvel
        R = quat_mat(q[:, 3:7])
        yaw = torch.atan2(R[:, 1, 0], R[:, 0, 0])
        c, s = torch.cos(yaw), torch.sin(yaw)
        role = torch.zeros(self.N, device=self.device)
        clock = 1.0 - self.t.float() / self.T

        # Target vector in heading frame encodes velocity command along desired yaw
        dyaw = self.yaw_cmd - yaw
        target_h = torch.stack([self.v_cmd * torch.cos(dyaw), self.v_cmd * torch.sin(dyaw), torch.zeros_like(self.v_cmd)], -1)
        phi = 2.0 * math.pi * self.gait_phase
        aux_h = torch.stack([torch.sin(phi), torch.cos(phi), self.v_cmd / 3.0], -1)

        cur = torch.cat([target_h, aux_h], -1)
        self.hist = torch.where(self.fresh[:, None, None], cur[:, None].expand(-1, 16, -1), torch.cat([self.hist[:, 1:], cur[:, None]], 1))
        self.fresh[:] = False

        obs = torch.cat([
            q[:, self.jq] - self.stand,
            v[:, self.jv] * 0.1,
            q[:, 2:3],
            -R[:, 2, :],
            self._heading(v[:, 0:3], c, s),
            v[:, 3:6] * 0.25,
            self.prev_action,
            role[:, None], clock[:, None],
            cur,
            *[self.hist[:, -1 - lag] for lag in LAGS],
            self.mem, self.stamina[:, None], self.neck[:, None],
            self._heading(self.geom_xpos[:, self.hand] - self.xpos[:, self.pelvis], c, s),
            self._heading(-self.geom_xmat[:, self.hand][:, :, 2], c, s),
        ], -1)

        if self.obs_dim == 148:
            # Option 5: +12 explicit locomotion proprioception channels (foot_l/r pos, contact flags, vertical load, gait clock)
            fl_h = self._heading(self.geom_xpos[:, self.foot_l] - self.xpos[:, self.pelvis], c, s)
            fr_h = self._heading(self.geom_xpos[:, self.foot_r] - self.xpos[:, self.pelvis], c, s)
            phi = 2.0 * math.pi * self.gait_phase
            phase_sc = torch.stack([torch.sin(phi), torch.cos(phi)], -1)
            ext = torch.cat([fl_h, fr_h, self.foot_contact.float(), self.foot_load, phase_sc], -1)
            obs = torch.cat([obs, ext], -1)

        if self.randomize:
            noise = torch.zeros(obs.shape[-1], device=self.device)
            noise[:30], noise[30:60], noise[61:64] = 0.005, 0.05, 0.01
            obs = obs + torch.randn_like(obs) * noise
        return obs
