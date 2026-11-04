r"""Rest-pose vertices of each athlete's face (skinned to head OR neck), for the physics-3 jaw fit (read only: nothing
is saved to the .blend).

    "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" -b --factory-startup --python Scripts/blender_face_vertices.py -- [grandpa kim matt]

Writes Saved/collision_review/<name>_face.npz: the world positions (rest pose = MuJoCo qpos0, Blender frame) of every
vertex whose head + neck weight is >= 0.3, its head weight, its neck weight, and every bone's head position. The lower
jaw of Grandpa and Kim's whole lower face are skinned to the neck bone (DOCS/20261103/collision_review.md), so the head
group alone misses the jaw. training/build_body.py --fit-jaw reads these files into <name>_skeleton.json ("face").
"""
import sys
from pathlib import Path

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "Content/Art/Raw"
OUT = ROOT / "Saved/collision_review"


def run(name):
    bpy.ops.wm.open_mainfile(filepath=str(RAW / f"{name.capitalize()}_rigged.blend"))
    arm = next(o for o in bpy.data.objects if o.type == "ARMATURE")
    mesh = max((o for o in bpy.data.objects if o.type == "MESH" and any(m.type == "ARMATURE" for m in o.modifiers)),
               key=lambda o: len(o.data.vertices))
    arm.data.pose_position = "REST"
    bpy.context.view_layer.update()
    ev = mesh.evaluated_get(bpy.context.evaluated_depsgraph_get())
    me = ev.to_mesh()
    co = np.array([mesh.matrix_world @ v.co for v in me.vertices])
    ev.to_mesh_clear()
    idx = {g.index: g.name for g in mesh.vertex_groups}
    pos, wh, wn = [], [], []
    for v in mesh.data.vertices:
        w = {idx.get(g.group): g.weight for g in v.groups}
        h, n = w.get("head", 0.0), w.get("neck", 0.0)
        if h + n >= 0.3:
            pos.append(co[v.index]); wh.append(h); wn.append(n)
    bones = {b.name: np.array(arm.matrix_world @ b.head_local) for b in arm.data.bones}
    OUT.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(OUT / f"{name}_face.npz", pos=np.array(pos), w_head=np.array(wh), w_neck=np.array(wn),
                        bone_names=np.array(list(bones)), bone_pos=np.array(list(bones.values())))
    print(f"{name}: {len(pos)} head/neck vertices")


if __name__ == "__main__":
    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else ["grandpa", "kim", "matt"]
    for n in args:
        run(n)
