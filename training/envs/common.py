"""What the three GPU envs (slap.py, arena.py, locomotion.py) share beyond the maths in slap.py (hill, quat_mat).

* `blown_up`: a world whose state is no longer finite. MuJoCo Warp keeps stepping a NaN world forever (the NaN
  fails every height test, so no fall rule ever resets it) and its rows feed NaN observations to PPO; the envs end
  such an episode like a fall and reset the world (2026-11-03, the CUDA 719 hunt: tasks J10).
* `buffer_health`: how full the contact and constraint buffers ran, and Warp's own overflow flags. One host sync,
  called once per training iteration by train.py, so a future crash can be read against the buffers in the
  telemetry instead of guessed at.
"""
from __future__ import annotations

import mujoco
import torch
import warp as wp


def blown_up(qpos, qvel):
    """(N,) True where a world's state is not finite, or so fast it is about to stop being (MuJoCo's own blow-up
    test is |qvel| > 1e10; a slap env never has a joint near 1e3 rad/s)."""
    return ~(torch.isfinite(qpos).all(-1) & torch.isfinite(qvel).all(-1)) | (qvel.abs().amax(-1) > 1e3)


def buffer_health(dw):
    """The fullest the buffers ran (host sync: once per iteration): constraint rows per world vs njmax, contacts and
    broadphase pairs vs naconmax, and the OR of Warp's overflow bits (types.OverflowType)."""
    T = wp.to_torch
    nefc, ovf = T(dw.nefc), T(dw.overflow)
    bits = 0
    for b in torch.unique(ovf).tolist():
        bits |= int(b)
    return {"nefc_max": int(nefc.max()), "njmax": int(dw.njmax), "nacon": int(T(dw.nacon)[0]),
            "ncollision": int(T(dw.ncollision)[0]), "naconmax": int(dw.naconmax), "overflow": bits}


wp.config.quiet = True   # clean runtime logs: no "Module ... load on device" line per kernel in every run's log


def per_world(mw, name, num_envs):
    """Give the model field `name` one row per world (randomised per episode); returns its torch view."""
    a = getattr(mw, name)
    t = wp.to_torch(a)
    t = t.repeat(num_envs, *[1] * (t.dim() - 1)).contiguous()
    setattr(mw, name, wp.from_torch(t, dtype=a.dtype))
    return t


def capture(mw, dw):
    """Compile once, then replay: the step and the kinematics as CUDA graphs (g_step, g_kin)."""
    import mujoco_warp as mjw
    mjw.step(mw, dw); mjw.kinematics(mw, dw); wp.synchronize()
    with wp.ScopedCapture() as c:
        mjw.step(mw, dw)
    g_step = c.graph
    with wp.ScopedCapture() as c:
        mjw.kinematics(mw, dw)
    return g_step, c.graph


def face_lookup(m, prefixes, device):
    """Physics 3's face (build_body.face_parts): (per geom: the athlete whose face it is, else -1; per athlete: its
    face_zone site id). v1/v2 models have no jaw: the face is the head sphere alone, the site is never tested."""
    owner = torch.full((m.ngeom,), -1, dtype=torch.long, device=device)
    for k, p in enumerate(prefixes):
        for n in ("head", "jaw_l", "jaw_r"):
            i = mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_GEOM, p + n)
            if i >= 0:
                owner[i] = k
    sites = [mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_SITE, p + "face_zone") for p in prefixes]
    return owner, torch.tensor(sites, device=device), torch.tensor(m.site_size, dtype=torch.float32, device=device)


def in_face_zone(pos, site_xpos, site_xmat, half):
    """match.in_face_zone batched: (n, 3) contact points against their head's face_zone box ((n, 3), (n, 3, 3), (n, 3))."""
    local = (site_xmat.transpose(-1, -2) @ (pos - site_xpos)[..., None]).squeeze(-1)
    return (local.abs() <= half).all(-1)


def contact_slots(env, worlds):
    """Every contact slot at once (no host sync): (valid, geom1, geom2, world). Slots past nacon hold stale ids, so
    `valid` must mask every use; the world index is clamped so even a stale one indexes safely."""
    idx = torch.arange(env.con_geom.shape[0], device=env.device)
    g = env.con_geom.long()
    return idx < env.nacon[0], g[:, 0], g[:, 1], env.con_world.long().clamp(0, worlds - 1)


# ---------------------------------------------------------------- interface 4 (DOCS/BRAIN_IO.md, 2026-11-04)
def world_geoms(m, device):
    """(ngeom,) True for geoms of the world body (floor, podium, the course's step): what a sole stands on."""
    return torch.tensor([int(m.geom_bodyid[g]) == 0 for g in range(m.ngeom)], dtype=torch.bool, device=device)


def foot_lookup(m, feet, device):
    """(ngeom,) per geom: its slot 2 k + f for the sole box f (0 left, 1 right) of athlete k in `feet` [(l, r), ...],
    -1 for any other geom."""
    of = torch.full((m.ngeom,), -1, dtype=torch.long, device=device)
    for k, pair in enumerate(feet):
        for f, g in enumerate(pair):
            of[int(g)] = 2 * k + f
    return of


def foot_contacts(env, worlds, foot_of, is_world, athletes=1):
    """BRAIN_IO 136-139 per athlete, (worlds, athletes, 4): heel_l, toe_l, heel_r, toe_r. A contact between a sole box
    and a world geom, its point taken into the box's frame: heel when it lies behind the box centre along the box's
    long (local x) axis, toe at or ahead of it. The contact list is the last physics step's (as d->contact in Unreal and
    the browser at decision time); no host sync."""
    valid, g0, g1, w = contact_slots(env, worlds)
    f0, f1 = foot_of[g0], foot_of[g1]
    sole = torch.where(f0 >= 0, f0, f1)
    other = torch.where(f0 >= 0, g1, g0)
    on = valid & (sole >= 0) & is_world[other]
    g = torch.where(f0 >= 0, g0, g1)
    pos = env.con_pos[:, :3]
    axis = env.geom_xmat[w, g][:, :, 0]                       # the box's local x axis in world coordinates
    toe = (((pos - env.geom_xpos[w, g]) * axis).sum(-1) >= 0).long()
    slot = w * (4 * athletes) + sole.clamp(min=0) * 2 + toe   # world, athlete, foot, heel/toe
    out = torch.zeros(worlds * 4 * athletes, device=env.device)
    out.scatter_reduce_(0, torch.where(on, slot, torch.zeros_like(slot)), on.float(), "amax")
    return out.view(worlds, athletes, 4)


def lowpass(tgt, goal, on):
    """The interface-4 action low-pass (constants.LPF_ALPHA, before the rate limit) on rows where `on` ((rows, 1) bool,
    or a bool): target = goal + alpha (target - goal). Rows of older brains (interface <= 3) pass through unchanged."""
    from constants import LPF_ALPHA
    f = goal + LPF_ALPHA * (tgt - goal)
    return torch.where(on, f, tgt) if isinstance(on, torch.Tensor) else (f if on else tgt)


# ---------------------------------------------------------------- rules 2 (training/rules.json, physics 4 on)
# What the referee's new calls cost in reward (training only). A foul is one point on every card; in reward units a
# point is put at 20: a clean palm strike pays ~120 (50 + 8 v), a striker's time-out without a strike costs 20, so a
# foul is as bad as not striking at all and a sixth of a clean slap.
FOUL = 20.0        # the defender's flinch (once a turn); the striker's clubbing (on top of the reduced pay)
CLUB_PAY = 0.25    # a first touch that is not a palm still is the strike, at a quarter of its pay: about +10 net at
                   # 9 m/s against +120 for the palm - never better than a palm, never worse than refusing to strike


def palm_led(env, on, g0, other, w, worlds):
    """match.py's palm test on every contact slot, per world: a striking contact (`on`) whose `other` geom is a hand
    box with its palm face (the box's -z axis) leading - the contact normal (frame row 0, from geom 0 to geom 1)
    turned to point from the striker's part to the face, against the hand's z axis, over 0.5. Any such contact in the
    world counts (match.py takes the first face contact in MuJoCo's list; the lists differ in order, not in content)."""
    sign = torch.where(g0 == other, 1.0, -1.0)
    z = env.geom_xmat[w, other][:, :, 2]
    lead = -sign * (env.con_frame[:, 0] * z).sum(-1)
    pc = on & env.is_hand[other] & (lead > 0.5)
    return torch.zeros(worlds, device=env.device).scatter_reduce_(0, w, pc.float(), "amax").bool()


# The motion-style terms every policy shares (AGENTS.md §3 hand-overs): training only, never an input.
HF_BETA = (1.0 / (2 * 3.141592653589793 * 10.0)) / (1.0 / (2 * 3.141592653589793 * 10.0) + 0.02)   # 10 Hz high-pass at 50 Hz
HF_W, HF_CAP = 0.002, 2.0          # cost per (rad/s)^2 of >10 Hz joint velocity, summed over joints, capped per step
COM_W, COM_VAR = 0.3, 0.0025       # CoM recovery: 0.3 exp(-e^2 / (5 cm)^2), e = horizontal CoM drift from its stance spot


def hf_oscillation(v, v_prev, hp):
    """Item 47: the joint velocities' content above ~10 Hz, a first-order high-pass at the 50 Hz decision
    (hp = beta (hp + v - v_prev)): cheap, one (rows, joints) state. Returns (penalty per row, new hp state); a smooth
    swing leaves hp near 0, a joint buzzing at 15-25 Hz drives it to the swing's amplitude."""
    hp = HF_BETA * (hp + v - v_prev)
    return (HF_W * (hp ** 2).sum(-1)).clamp(max=HF_CAP), hp


def com_recovery(com_xy, feet_xy, ref):
    """Item 46: the L3 criterion (CoM back within 5 cm) as a per-step pay at every level: e = |(CoM - mid-feet) - ref|,
    ref = the same offset at the episode's stance (each body's own lean). Returns (pay, e)."""
    e = ((com_xy - feet_xy.mean(-2)) - ref).norm(dim=-1)
    return COM_W * torch.exp(-e ** 2 / COM_VAR), e


# ---------------------------------------------------------------- interface 5: player command vector (140-141)
def cmd_strike_bonus(cmd_power, speed, hit_v, clean, phi):
    """Extra touch reward when strike command power (+1) vs accuracy (-1) is non-zero; 0 when neutral."""
    pwr = cmd_power.clamp(min=0.0) * (3.0 * (speed - hit_v) * clean.float())
    acc = (-cmd_power).clamp(min=0.0) * (20.0 * clean.float() + 10.0 * phi)
    return pwr + acc


def cmd_brace_reward(cmd_brace, upright, qvel):
    """Defence stability pay while timed brace command (141) is active (+1); 0 when neutral."""
    vel_pen = 0.1 * (qvel ** 2).mean(-1).clamp(max=10.0) / 10.0
    return cmd_brace * (0.4 * upright.clamp(min=0.0) - vel_pen)
