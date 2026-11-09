"""A model that arrives rigged as a .glb with a simple 19-bone rig (hips, spine, chest, neck, head, shoulder/upperarm/
forearm/hand.L|R, thigh/shin/foot.L|R: Tong.glb, 2026-11-09) -> a Mixamo-named, T-posed FBX for Scripts/rig_mixamo.py.

    blender -b --factory-startup --python Scripts/rig_glb.py -- <in.glb> <out.fbx>
    blender -b --factory-startup --python Scripts/rig_mixamo.py -- <out.fbx> <name> <height_m>

Keeps the model's own skin weights (Scripts/rig_auto.py re-skins from scratch, and its heat solve found nothing on
Tong's chunky, bent-armed mesh). Renames the bones (vertex groups follow), adds the zero-weight toe and hand-end helpers
rig_mixamo.py reads, then turns each arm (upper arm, forearm, hand) onto Grandpa's rest line (T-pose, 10 deg down) and
bakes that pose in as the rest pose. Expects the model standing, facing -Y, character left = +X (Grandpa's frame).
"""
import math, sys

import bpy
from mathutils import Matrix, Vector

src, out = sys.argv[-2], sys.argv[-1]
ARM_DOWN = math.radians(10)
NAMES = {"hips": "Hips", "spine": "Spine", "chest": "Spine1", "neck": "Neck", "head": "Head"}
for s, S in (("L", "Left"), ("R", "Right")):
    NAMES.update({f"shoulder.{s}": f"{S}Shoulder", f"upperarm.{s}": f"{S}Arm", f"forearm.{s}": f"{S}ForeArm",
                  f"hand.{s}": f"{S}Hand", f"thigh.{s}": f"{S}UpLeg", f"shin.{s}": f"{S}Leg", f"foot.{s}": f"{S}Foot"})

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=src)
arm = next(o for o in bpy.data.objects if o.type == "ARMATURE")
mesh = next(o for o in bpy.data.objects if o.type == "MESH")
arm.animation_data_clear()
missing = [n for n in NAMES if n not in arm.data.bones]
if missing:
    sys.exit(f"[rig_glb] bones not found: {missing} (have {[b.name for b in arm.data.bones]})")
for old, new in NAMES.items():
    arm.data.bones[old].name = new   # the mesh's vertex groups are renamed with them

# helpers: a toe bone on each foot (its tail is the toe tip, at sole height) and the hand's end
bpy.context.view_layer.objects.active = arm
bpy.ops.object.mode_set(mode="EDIT")
eb = arm.data.edit_bones
mw_inv = arm.matrix_world.inverted()
gname = {g.index: g.name for g in mesh.vertex_groups}
lead = [gname[max(v.groups, key=lambda g: g.weight).group] if len(v.groups) else "" for v in mesh.data.vertices]
for S, sx in (("Left", 1), ("Right", -1)):
    foot = eb[f"{S}Foot"]
    near = [mesh.matrix_world @ v.co for v, l in zip(mesh.data.vertices, lead) if l == f"{S}Foot"]   # the skin this foot leads
    tip = min(near, key=lambda v: v.y) if near else arm.matrix_world @ foot.tail   # the furthest forward point (faces -Y)
    tip = Vector((tip.x, tip.y, min(v.z for v in near) if near else tip.z))
    toe = eb.new(f"{S}ToeBase")
    toe.head, toe.tail = mw_inv @ (tip + Vector((0, 0.04, 0))), mw_inv @ tip
    toe.parent, toe.use_deform = foot, False
    print(f"[rig_glb] {S} toe tip {tuple(round(c, 3) for c in tip)}")
bpy.ops.object.mode_set(mode="OBJECT")

# arms onto the rest line, bone by bone from the shoulder out
bpy.ops.object.mode_set(mode="POSE")
for S, sx in (("Left", 1), ("Right", -1)):
    want = Vector((sx * math.cos(ARM_DOWN), 0.0, -math.sin(ARM_DOWN)))
    for b in (f"{S}Arm", f"{S}ForeArm", f"{S}Hand"):
        bpy.context.view_layer.update()
        pb = arm.pose.bones[b]
        h, t = arm.matrix_world @ pb.head, arm.matrix_world @ pb.tail
        q = (t - h).normalized().rotation_difference(want)
        hl = pb.head.copy()
        R = (arm.matrix_world.inverted().to_3x3() @ q.to_matrix() @ arm.matrix_world.to_3x3()).to_4x4()
        pb.matrix = Matrix.Translation(hl) @ R @ Matrix.Translation(-hl) @ pb.matrix
    bpy.context.view_layer.update()
    h, t = arm.matrix_world @ arm.pose.bones[f"{S}Arm"].head, arm.matrix_world @ arm.pose.bones[f"{S}Hand"].tail
    print(f"[rig_glb] {S} arm shoulder->hand {tuple(round(c, 3) for c in (t - h).normalized())}")


def turn(b, want):
    """Rotate pose bone b about its head so its head->tail points along want (world)."""
    bpy.context.view_layer.update()
    pb = arm.pose.bones[b]
    h, t = arm.matrix_world @ pb.head, arm.matrix_world @ pb.tail
    q = (t - h).normalized().rotation_difference(want.normalized())
    hl = pb.head.copy()
    R = (arm.matrix_world.inverted().to_3x3() @ q.to_matrix() @ arm.matrix_world.to_3x3()).to_4x4()
    pb.matrix = Matrix.Translation(hl) @ R @ Matrix.Translation(-hl) @ pb.matrix


# legs straight down (the scan stands with one leg stepped out: A8 wants mirror-image legs), feet pointing forward
for S in ("Left", "Right"):
    turn(f"{S}UpLeg", Vector((0.0, 0.0, -1.0)))
    turn(f"{S}Leg", Vector((0.0, 0.0, -1.0)))
    bpy.context.view_layer.update()
    pb = arm.pose.bones[f"{S}Foot"]
    d = (arm.matrix_world @ pb.tail) - (arm.matrix_world @ pb.head)
    flat = math.hypot(d.x, d.y)
    turn(f"{S}Foot", Vector((0.0, -flat, d.z)))   # its own pitch, turned to face -Y
bpy.ops.object.mode_set(mode="OBJECT")

# bake: the posed skin becomes the mesh, the pose becomes the rest pose, the skin is put back
bpy.context.view_layer.objects.active = mesh
mod = next(m for m in mesh.modifiers if m.type == "ARMATURE")
bpy.ops.object.modifier_apply(modifier=mod.name)
bpy.context.view_layer.objects.active = arm
bpy.ops.object.mode_set(mode="POSE")
bpy.ops.pose.armature_apply(selected=False)
bpy.ops.object.mode_set(mode="OBJECT")
# the rest skeleton mirrored across the hips' X (each pair's joints averaged): the scan's own joints differ by a few cm
# left to right (forearm 3.4 cm, A8 allows 3). Only the bones move; the skin keeps its shape at the rest pose.
bpy.context.view_layer.update()
cx = (arm.matrix_world @ arm.data.bones["Hips"].head_local).x
M, Mi = arm.matrix_world, arm.matrix_world.inverted()
bpy.context.view_layer.objects.active = arm
bpy.ops.object.mode_set(mode="EDIT")
eb = arm.data.edit_bones
for e in eb:
    if not e.name.startswith("Left"):
        continue
    r = eb[e.name.replace("Left", "Right", 1)]
    for k in ("head", "tail"):
        a, b = M @ getattr(e, k), M @ getattr(r, k)
        m = Vector(((abs(a.x - cx) + abs(b.x - cx)) / 2, (a.y + b.y) / 2, (a.z + b.z) / 2))
        setattr(e, k, Mi @ Vector((cx + m.x, m.y, m.z)))
        setattr(r, k, Mi @ Vector((cx - m.x, m.y, m.z)))
bpy.ops.object.mode_set(mode="OBJECT")
mesh.modifiers.new("Armature", "ARMATURE").object = arm
# centred on the hips across the ground (rig_mixamo.py only moves the soles to Z = 0); the mesh rides on the armature
bpy.context.view_layer.update()
hips = arm.matrix_world @ arm.data.bones["Hips"].head_local
for o in {arm, mesh.parent or arm}:
    o.location -= Vector((hips.x, hips.y, 0.0))
bpy.context.view_layer.update()
print(f"[rig_glb] centred by {-hips.x:+.3f}, {-hips.y:+.3f} m")

# the glTF's textures live only inside the file: unpack next to the FBX so it can embed them
import os
for img in bpy.data.images:
    if img.packed_file:
        img.filepath_raw = os.path.join(os.path.dirname(out), f"{img.name}.png")
        img.file_format = "PNG"
        img.save()
bpy.ops.object.select_all(action="SELECT")
bpy.ops.export_scene.fbx(filepath=out, use_selection=True, add_leaf_bones=False, bake_anim=False,
                         path_mode="COPY", embed_textures=True, mesh_smooth_type="FACE")
print(f"[rig_glb] wrote {out}: {len(arm.data.bones)} bones, {len(mesh.data.vertices)} vertices")
