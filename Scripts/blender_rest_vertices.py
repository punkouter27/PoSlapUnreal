r"""Rest-pose vertices of each athlete's hands and head, for the drawn-mesh clipping check (read only: nothing saved
to the .blend).

    "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" -b --factory-startup --python Scripts/blender_rest_vertices.py -- [grandpa kim]

Writes Saved/collision_review/<name>_rest.npz: the world positions (rest pose, the bind pose = MuJoCo qpos0) of the
vertices weighted >= 0.5 to hand_l, hand_r or head, their normals, their bone, and every bone's head position. Bone names are
MuJoCo body names, so Scripts/collision_mesh_clip.py can carry each vertex rigidly with its body, as Web/art.js does.
"""
import sys
from pathlib import Path

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "Content/Art/Raw"
OUT = ROOT / "Saved/collision_review"
PARTS = ("hand_l", "hand_r", "head")


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
    rot = mesh.matrix_world.to_3x3().inverted().transposed()
    nrm = np.array([(rot @ v.normal).normalized() for v in me.vertices])
    ev.to_mesh_clear()
    idx = {g.index: g.name for g in mesh.vertex_groups}
    pos, part, nor = [], [], []
    for v in mesh.data.vertices:
        best = max(((idx[g.group], g.weight) for g in v.groups if idx.get(g.group) in PARTS), key=lambda x: x[1], default=None)
        if best and best[1] >= 0.5:
            pos.append(co[v.index]); part.append(PARTS.index(best[0])); nor.append(nrm[v.index])
    bones = {b.name: np.array(arm.matrix_world @ b.head_local) for b in arm.data.bones}
    OUT.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(OUT / f"{name}_rest.npz", pos=np.array(pos), part=np.array(part), nrm=np.array(nor), bone_names=np.array(list(bones)),
                        bone_pos=np.array(list(bones.values())))
    print(f"{name}: {len(pos)} vertices ({[int((np.array(part) == i).sum()) for i in range(3)]} hand_l/hand_r/head), {len(bones)} bones")


if __name__ == "__main__":
    args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else ["grandpa", "kim"]
    for n in args:
        run(n)
