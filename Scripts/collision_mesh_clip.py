"""Drawn-mesh clipping at the slap: does the drawn palm sink into the drawn face?

    training/.venv/Scripts/python.exe Scripts/collision_mesh_clip.py

Inputs: Saved/collision_review/p<physics>_<pairing>_poses.npz (Scripts/collision_review.py: the hand and head bodies'
poses at every palm-head contact step of real matches) and Saved/collision_review/<name>_rest.npz
(Scripts/blender_rest_vertices.py: the hand and head vertices at rest). Each vertex rides rigidly on its body, the way
Web/art.js poses the bones (bone = body position, body rotation x a rest offset; the meshes are bound at qpos0).
The drawn face is the head vertices with their normals; a palm vertex under the skin of the nearest face vertex
is clipping (signed distance along that vertex's normal). "Mesh clamped" is the browser's new clamp (Web/fx.js clipPush, the same rule). "Clamped" applies Web/app.js's old pusher (the drawn hand moved out along the
head-centre line until the hand box clears the sphere by 4 mm). Also reported: how far the drawn skin sits outside
the collision shapes (face skin vs the head sphere at the cheek, palm skin vs the hand box).
"""
import json
import os
import sys
from pathlib import Path

import numpy as np
from scipy.spatial import cKDTree

ROOT = Path(__file__).resolve().parents[1]
TRAIN = ROOT / "training"
CR = ROOT / "Saved" / "collision_review"
PAIR_CHARS = {"gg": ("grandpa", "grandpa"), "gk": ("grandpa", "kim"), "kk": ("kim", "kim")}
NB = 2000
DIRS = None


def fib(n):
    i = np.arange(n) + 0.5
    phi, z = np.pi * (1 + 5 ** 0.5) * i, 1 - 2 * i / n
    r = np.sqrt(1 - z * z)
    return np.stack([r * np.cos(phi), r * np.sin(phi), z], 1)


def frame(up, left):
    x = up / np.linalg.norm(up)
    z = np.cross(x, left); z /= np.linalg.norm(z)
    return np.stack([x, np.cross(z, x), z], 1)   # columns


def rest_model(physics, rc, bc):
    os.environ["SLAP_PHYSICS"] = str(physics)
    sys.path.insert(0, str(TRAIN))
    import mujoco
    import importlib
    import constants
    importlib.reload(constants)
    import match
    importlib.reload(match)
    m, _, pre = match.load(None, rc, bc)
    d = mujoco.MjData(m)
    mujoco.mj_resetData(m, d)
    mujoco.mj_kinematics(m, d)
    return m, d, pre


def athlete(m, d, prefix, name):
    R = np.load(CR / f"{name}_rest.npz")
    bones = dict(zip(R["bone_names"], R["bone_pos"]))
    body = lambda n: m.body(prefix + n).id
    mj = {n: d.xpos[body(n)].copy() for n in ("pelvis", "head", "hand_l", "hand_r")}
    F = lambda P: frame(P["head"] - P["pelvis"], P["hand_l"] - P["hand_r"])
    M = F(mj) @ F(bones).T    # mesh -> world rotation (art.js meshToWorld)
    out = {}
    for k, part in enumerate(("hand_l", "hand_r", "head")):
        b = body(part)
        v = R["pos"][R["part"] == k]
        local = (d.xmat[b].reshape(3, 3).T @ (M @ (v - bones[part]).T)).T   # in the body's frame
        g = m.geom(prefix + part).id
        out[part] = dict(local=local, gpos=m.geom_pos[g].copy(), gquat=m.geom_quat[g].copy(), size=m.geom_size[g].copy())
    # the drawn face: the head vertices and their normals in the head body's frame (signed distance to the skin)
    H = out["head"]
    b = body("head")
    nrm = R["nrm"][R["part"] == 2]
    H["nrm"] = (d.xmat[b].reshape(3, 3).T @ (M @ nrm.T)).T
    H["tree"] = cKDTree(H["local"])
    H["radius"] = float(H["size"][0])
    return out


def quat_mat(q):
    w, x, y, z = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
                     [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
                     [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]])


def skin_outside(A):
    """How far the drawn skin sits outside the collision shapes: the face skin over the head sphere at the cheeks
    (the sphere's sideways points, signed distance to the skin), the palm skin past the hand box (p90 of the
    vertices' distance outside the box)."""
    H = A["head"]
    side = DIRS[np.abs(DIRS[:, 1]) > 0.8]   # the head frame's y is the athlete's left/right: the cheeks
    pts = H["gpos"] + side * H["radius"]
    face = np.median(signed(pts, H))
    out = []
    for hand in ("hand_l", "hand_r"):
        P = A[hand]
        q = (P["local"] - P["gpos"]) @ quat_mat(P["gquat"])   # in the box's frame
        out.append(float(np.percentile(np.max(np.abs(q) - P["size"], axis=1), 90)))
    return float(face), float(np.mean(out))


def signed(pts, H, near=None):
    """Signed depth of points (head frame) under the drawn skin: > 0 = inside the face, from the nearest vertex's
    normal; with `near`, points farther than that from every face vertex count as outside (a finger past the ear)."""
    dist, j = H["tree"].query(pts)
    dep = np.einsum("ij,ij->i", H["local"][j] - pts, H["nrm"][j])
    return dep if near is None else np.where(dist < near, dep, -dist)


def depth_of(hand_world, head_pos, head_R, H):
    """The drawn palm's depth in the drawn face (m, > 0 = clipping): the deepest 5 % of the hand's vertices (a patch,
    not one stray vertex), each within 4 cm of the skin."""
    return np.percentile(signed((hand_world - head_pos) @ head_R, H, near=0.04), 95)


def mesh_push(v, hp, hR, H, iters=5, skin=0.002):
    """The browser's mesh clamp (Web/fx.js clipPush): move the drawn hand out of the drawn face along the mean normal
    of the face vertices it is under, by the deepest 5 % of its vertices' depth (+2 mm), up to 5 times."""
    total = np.zeros(3)
    for _ in range(iters):
        loc = (v + total - hp) @ hR
        dist, j = H["tree"].query(loc)
        dep = np.einsum("ij,ij->i", H["local"][j] - loc, H["nrm"][j])
        inside = (dep > 0) & (dist < 0.04)
        if inside.sum() < max(1, len(v) // 100):
            break
        n = H["nrm"][j[inside]].mean(0)
        n /= max(np.linalg.norm(n), 1e-9)
        total = total + hR @ n * (np.percentile(dep[inside], 95) + skin)
    return v + total


def run(physics, pairing, tag=""):
    f = CR / f"p{physics}_{pairing}{tag}_poses.npz"   # tag "_v2b": collision_review.py --brains v2
    if not f.exists():
        return None
    poses = np.load(f)["poses"]
    rc, bc = PAIR_CHARS[pairing]
    m, d, pre = rest_model(physics, rc, bc)
    A = {"red": athlete(m, d, pre[0], rc), "blue": athlete(m, d, pre[1], bc)}
    raw, clamped, meshed, phys_pen = [], [], [], []
    for P in poses:   # rows: red hand_l, hand_r, head, blue hand_l, hand_r, head; each pos(3) + xmat(9)
        pose = {(s, p): (P[3 * si + pi][:3], P[3 * si + pi][3:].reshape(3, 3))
                for si, s in enumerate(("red", "blue")) for pi, p in enumerate(("hand_l", "hand_r", "head"))}
        for s, o in (("red", "blue"), ("blue", "red")):
            hp, hR = pose[(o, "head")]
            H = A[o]["head"]
            c = hp + hR @ H["gpos"]
            for hand in ("hand_l", "hand_r"):
                bp, bR = pose[(s, hand)]
                Hd = A[s][hand]
                gp = bp + bR @ Hd["gpos"]
                dist = np.linalg.norm(gp - c)
                thick = float(Hd["size"].min())
                need = H["radius"] + thick - dist
                if need < -0.01:   # this hand is not the one at the face
                    continue
                v = bp + (bR @ Hd["local"].T).T
                dep = depth_of(v, hp, hR, H)
                raw.append(dep)
                phys_pen.append(max(0.0, need))
                push = H["radius"] + thick + 0.004 - dist   # app.js buildPushers
                v2 = v + ((gp - c) / dist * push if push > 0 else 0.0)
                clamped.append(depth_of(v2, hp, hR, H))
                meshed.append(depth_of(mesh_push(v, hp, hR, H), hp, hR, H))
    raw, clamped, meshed = np.array(raw) * 1000, np.array(clamped) * 1000, np.array(meshed) * 1000
    q = lambda a, p: round(float(np.percentile(a, p)), 1) if len(a) else None
    so = {k: [round(x * 1000, 1) for x in skin_outside(A[k])] for k in A}
    return dict(physics=physics, pairing=pairing, brains=tag.strip("_") or "shipped", samples=int(len(raw)),
                drawn_depth_mm=dict(median=q(raw, 50), p90=q(raw, 90), max=q(raw, 100), clipping_pct=round(100 * float((raw > 0).mean()), 1) if len(raw) else None),
                clamped_depth_mm=dict(median=q(clamped, 50), p90=q(clamped, 90), max=q(clamped, 100), clipping_pct=round(100 * float((clamped > 0).mean()), 1) if len(clamped) else None),
                mesh_clamped_depth_mm=dict(median=q(meshed, 50), p90=q(meshed, 90), max=q(meshed, 100), clipping_pct=round(100 * float((meshed > 0).mean()), 1) if len(meshed) else None),
                skin_outside_mm={k: dict(face_over_sphere=v[0], palm_over_box_p90=v[1]) for k, v in so.items()})


if __name__ == "__main__":
    DIRS = fib(NB)
    res = [r for p in (1, 2, 3) for g in PAIR_CHARS for t in ("", "_v2b") if (r := run(p, g, t))]
    (CR / "mesh_clip.json").write_text(json.dumps(res, indent=1))
    for r in res:
        print(json.dumps(r))
