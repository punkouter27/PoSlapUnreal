"""The eye-contact servo's A-B check (envs/arena.py): is the servo wired in, and does the switch work?

    .venv/Scripts/python.exe gaze_check.py

Prints one JSON line: the mean |the servo's neck yaw - the brain's own neck goal| (rad) with the servo ON
(applied) and what it WOULD be with the servo off (the switch).  verify_realism.py runs this twice, once with
SLAP_EYE_CONTACT=0, and asserts:
  * with the servo on, the neck yaw goal actually differs from the brain's own (> 0.01 rad);
  * with the servo off, the goal is untouched (0) while the servo's maths is still alive (> 0.01 rad);
  * the observation is 136 floats either way (the servo moves motors, not what the brain sees).
"""
import json

import torch

from constants import GAZE_ON
from envs.arena import ArenaEnv

env = ArenaEnv(("grandpa", "kim"), level="L4", num_envs=64, seed=7)
for _ in range(30):
    env.step(torch.zeros(env.N, env.A, device="cuda"))
print(json.dumps({
    "gaze_on": bool(GAZE_ON),
    "applied_rad": round(float(env.gaze_off.mean()), 6),
    "would_apply_rad": round(float(env.gaze_bias.mean()), 6),
    "obs_dim": int(env.observe().shape[-1]),
}))
