"""Grandpa's MuJoCo body frames for the open-hands check renders: bind (qpos0, the T-pose the mesh is skinned in), the
stand pose (the fingerprint's stand_pose_rad) and a real slap contact (red's right hand box touching blue's head in a
grandpa_final mirror match, CPU MuJoCo, training/match.py's own loop; nothing is changed or saved in training/).

    cd training; .venv/Scripts/python.exe ../Scripts/open_hands_poses.py [out.json]

Writes Saved/open_hands/grandpa_poses.json: {"bodies": [...], "bind": [[x y z qw qx qy qz], ...], "stand": [...],
"slap": [...], "slap_info": {...}}, one row per red body (prefix stripped), world frame. Scripts/render_open_hands.py
drives the Blender rig with them the way Scripts/grandpa_v2.py does (MuJoCo body -> bone).
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRAIN = ROOT / "training"
sys.path.insert(0, str(TRAIN))
os.chdir(TRAIN)

import mujoco  # noqa: E402
import numpy as np  # noqa: E402

import match  # noqa: E402
from brain_cpu import muscle_step as real  # noqa: E402

OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "Saved/open_hands/grandpa_poses.json"
BRAIN = TRAIN / "models/brains/grandpa_final.onnx"

m, fps, (red, blue) = match.load(None, "grandpa", "grandpa")
ids = [i for i in range(m.nbody) if m.body(i).name.startswith(red)]
names = [m.body(i).name[len(red):] for i in ids]


def frames(d):
    return [np.concatenate([d.xpos[i], d.xquat[i]]).round(6).tolist() for i in ids]


d = mujoco.MjData(m)
mujoco.mj_resetData(m, d)
mujoco.mj_kinematics(m, d)
bind = frames(d)


class S:
    def __init__(self, fp, prefix):
        self.fp, self.prefix = fp, prefix


match.stand_both(m, d, [S(fps[0], red), S(fps[1], blue)])
stand = frames(d)

got = {}


def step(m, d, vmax):
    real(m, d, vmax)
    if got:
        return
    hand, head = m.geom(red + "hand_r").id, m.geom(blue + "head").id
    for c in d.contact[:d.ncon]:
        if {int(c.geom[0]), int(c.geom[1])} == {hand, head} and c.dist < 0:
            got.update(t=round(float(d.time), 3), pen_mm=round(-1000 * float(c.dist), 1), frames=frames(d))
            return


match.muscle_step = step
for seed in range(12):
    match.play(str(BRAIN), 0, blue=str(BRAIN), character="grandpa", blue_character="grandpa", seed=seed, log=False)
    if got:
        got["seed"] = seed
        break
if not got:
    raise SystemExit("open_hands_poses: no red hand_r - blue head contact in 12 matches")
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(dict(bodies=names, bind=bind, stand=stand, slap=got.pop("frames"),
                               slap_info=dict(brain=BRAIN.name, **got))))
print(f"[open_hands_poses] {OUT}: {len(names)} bodies, slap {got}")
