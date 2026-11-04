"""What the three GPU envs (slap.py, arena.py, locomotion.py) share beyond the maths in slap.py (hill, quat_mat).

* `blown_up`: a world whose state is no longer finite. MuJoCo Warp keeps stepping a NaN world forever (the NaN
  fails every height test, so no fall rule ever resets it) and its rows feed NaN observations to PPO; the envs end
  such an episode like a fall and reset the world (2026-11-03, the CUDA 719 hunt: tasks J10).
* `buffer_health`: how full the contact and constraint buffers ran, and Warp's own overflow flags. One host sync,
  called once per training iteration by train.py, so a future crash can be read against the buffers in the
  telemetry instead of guessed at.
"""
from __future__ import annotations

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


def contact_slots(env, worlds):
    """Every contact slot at once (no host sync): (valid, geom1, geom2, world). Slots past nacon hold stale ids, so
    `valid` must mask every use; the world index is clamped so even a stale one indexes safely."""
    idx = torch.arange(env.con_geom.shape[0], device=env.device)
    g = env.con_geom.long()
    return idx < env.nacon[0], g[:, 0], g[:, 1], env.con_world.long().clamp(0, worlds - 1)
