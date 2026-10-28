"""Run from training/ (PYTHONPATH=.): records the first turns of a Grandpa match (red strikes) as per-body world transforms at 30 fps.
    .venv/Scripts/python.exe record_motion.py <out.json> [seconds]"""
import json, sys
import mujoco
import numpy as np
import match

out, seconds = sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 12.0
frames, every = [], round(1 / 30 / 0.005)  # ~30 fps from the 200 Hz physics
state = {"k": 0, "m": None}
orig = match.muscle_step


class Done(Exception):
    pass


def rec(m, d, vmax):
    orig(m, d, vmax)
    if state["k"] % every == 0:
        frames.append(d.qpos.copy())
    state["k"] += 1
    state["m"] = m
    if state["k"] * 0.005 >= seconds:
        raise Done


match.muscle_step = rec
try:
    match.play("models/brains/grandpa_L4.onnx", 0, log=False)
except Done:
    pass
m = state["m"]
d = mujoco.MjData(m)
bodies = [i for i in range(m.nbody) if m.body(i).name.startswith("red_")]
names = [m.body(i).name[4:] for i in bodies]


def pose(q):
    d.qpos[:] = q
    mujoco.mj_kinematics(m, d)
    return [[*d.xpos[i].round(5).tolist(), *d.xquat[i].round(6).tolist()] for i in bodies]


rom, joints = [], []  # range of motion: each red hinge (left/right together) 0 -> low -> high -> 0, from the T-pose
hinges = [j for j in range(m.njnt) if m.jnt_type[j] == mujoco.mjtJoint.mjJNT_HINGE and m.joint(j).name.startswith("red_")]
groups = {}
for j in hinges:
    groups.setdefault(m.joint(j).name[4:].removesuffix("_l").removesuffix("_r"), []).append(j)
for g, js in groups.items():
    for s in np.concatenate([np.linspace(0, -1, 8), np.linspace(-1, 1, 14), np.linspace(1, 0, 8)]):
        q = m.qpos0.copy()
        for j in js:
            lo, hi = m.jnt_range[j]
            q[m.jnt_qposadr[j]] = (-s * lo if s < 0 else s * hi) if m.jnt_limited[j] else s
        rom.append(pose(q))
        joints.append(g)
json.dump({"fps": 1 / (every * 0.005), "bodies": names, "rest": pose(m.qpos0), "frames": [pose(q) for q in frames],
           "rom": rom, "rom_joint": joints}, open(out, "w"))
print(len(frames), "frames,", len(rom), "ROM frames,", len(groups), "joint groups")
