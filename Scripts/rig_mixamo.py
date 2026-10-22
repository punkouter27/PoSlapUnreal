"""Mixamo-style rigged FBX -> a PoSlap athlete (headless Blender): the 17-bone skeleton build_body.py expects.

    blender -b --factory-startup --python Scripts/rig_mixamo.py -- <in.fbx> <name> <height_m>

Keeps the original as Content/Art/Raw/<Name>_raw.blend. Bakes the bind (T-) pose, drops animation, puts the soles on
Z=0 and scales to the card height, merges the source vertex groups into our bones (spine1+spine2+clavicles -> chest,
toes -> foot, ...), limits to 4 weights per vertex. Writes Content/Art/Raw/<Name>_rigged.blend,
Content/Art/Exports/<Name>_rigged.fbx and training/characters/<name>_skeleton.json (same stats as Grandpa's).
"""
import json, sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
src, name, height = sys.argv[-3], sys.argv[-2], float(sys.argv[-1])
Name = name.capitalize()

# our bone <- (head from, tail from, source vertex groups)
MAP = {"pelvis": ("Hips.h", "Spine.h", ["Hips", "root"]),
       "belly": ("Spine.h", "Spine1.h", ["Spine"]),
       "chest": ("Spine1.h", "Neck.h", ["Spine1", "Spine2", "LeftShoulder", "RightShoulder"]),
       "neck": ("Neck.h", "Head.h", ["Neck"]),
       "head": ("Head.h", "Head.t", ["Head", "Head_end"])}
for s, S in (("l", "Left"), ("r", "Right")):
    MAP.update({f"upperarm_{s}": (f"{S}Arm.h", f"{S}ForeArm.h", [f"{S}Arm"]),
                f"forearm_{s}": (f"{S}ForeArm.h", f"{S}Hand.h", [f"{S}ForeArm"]),
                f"hand_{s}": (f"{S}Hand.h", f"{S}Hand.t", [f"{S}Hand", f"{S}Hand_end"]),
                f"thigh_{s}": (f"{S}UpLeg.h", f"{S}Leg.h", [f"{S}UpLeg"]),
                f"shin_{s}": (f"{S}Leg.h", f"{S}Foot.h", [f"{S}Leg"]),
                f"foot_{s}": (f"{S}Foot.h", f"{S}ToeBase.t", [f"{S}Foot", f"{S}ToeBase", f"{S}ToeBase_end"])})
PARENT = {"pelvis": None, "belly": "pelvis", "chest": "belly", "neck": "chest", "head": "neck"}
for s in "lr":
    PARENT.update({f"upperarm_{s}": "chest", f"forearm_{s}": f"upperarm_{s}", f"hand_{s}": f"forearm_{s}",
                   f"thigh_{s}": "pelvis", f"shin_{s}": f"thigh_{s}", f"foot_{s}": f"shin_{s}"})

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.fbx(filepath=src)
bpy.ops.file.pack_all()
bpy.ops.wm.save_as_mainfile(filepath=str(ROOT / "Content/Art/Raw" / f"{Name}_raw.blend"))

arm = next(o for o in bpy.data.objects if o.type == "ARMATURE")
mesh = next(o for o in bpy.data.objects if o.type == "MESH")
arm.animation_data_clear()
arm.data.pose_position = "REST"
src_pts = {}
for b in arm.data.bones:
    src_pts[b.name + ".h"] = arm.matrix_world @ b.head_local
    src_pts[b.name + ".t"] = arm.matrix_world @ b.tail_local

# bake the bind pose into the mesh, in world space
bpy.context.view_layer.objects.active = mesh
for md in [m for m in mesh.modifiers if m.type == "ARMATURE"]:
    bpy.ops.object.modifier_apply(modifier=md.name)
mw = mesh.matrix_world.copy()
mesh.parent = None
mesh.matrix_world = mw
bpy.ops.object.select_all(action="DESELECT")
mesh.select_set(True)
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
bpy.data.objects.remove(arm)

zs = np.array([v.co.z for v in mesh.data.vertices])
z0, top = float(zs.min()), float(zs.max())
k = height / (top - z0)
for v in mesh.data.vertices:
    v.co = Vector(((v.co.x) * k, v.co.y * k, (v.co.z - z0) * k))
P = {key: Vector((p.x * k, p.y * k, (p.z - z0) * k)) for key, p in src_pts.items()}
P["Head.t"] = Vector((P["Head.h"].x, P["Head.h"].y, height))  # crown, not the source's helper end
print(f"scale {k:.4f}: {top - z0:.3f} m -> {height} m")

# vertex groups: merge into ours, then keep 4 strongest per vertex, normalised
groups = {g.index: g.name for g in mesh.vertex_groups}
W = np.zeros((len(mesh.data.vertices), len(MAP)))
ours = list(MAP)
src_to_ours = {sg: i for i, n in enumerate(ours) for sg in MAP[n][2]}
for v in mesh.data.vertices:
    for g in v.groups:
        i = src_to_ours.get(groups[g.group])
        if i is not None:
            W[v.index, i] += g.weight
W[W < np.sort(W, 1)[:, -4:-3]] = 0.0
W /= np.maximum(W.sum(1, keepdims=True), 1e-9)
unweighted = int((W.sum(1) == 0).sum())
for g in list(mesh.vertex_groups):
    mesh.vertex_groups.remove(g)
for i, n in enumerate(ours):
    vg = mesh.vertex_groups.new(name=n)
    for vi in np.nonzero(W[:, i])[0]:
        vg.add([int(vi)], float(W[vi, i]), "REPLACE")

# the new armature
ad = bpy.data.armatures.new("Armature")
ao = bpy.data.objects.new("Armature", ad)
bpy.context.scene.collection.objects.link(ao)
bpy.context.view_layer.objects.active = ao
bpy.ops.object.mode_set(mode="EDIT")
for n in ours:   # PARENT order: parents first
    e = ad.edit_bones.new(n)
    e.head, e.tail = P[MAP[n][0]], P[MAP[n][1]]
    e.use_deform = True
for n, p in PARENT.items():
    if p:
        ad.edit_bones[n].parent = ad.edit_bones[p]
bpy.ops.object.mode_set(mode="OBJECT")
mesh.parent = ao
mesh.modifiers.new("Armature", "ARMATURE").object = ao
mesh.name = Name
bpy.ops.wm.save_as_mainfile(filepath=str(ROOT / "Content/Art/Raw" / f"{Name}_rigged.blend"))
bpy.ops.object.select_all(action="SELECT")
bpy.ops.export_scene.fbx(filepath=str(ROOT / "Content/Art/Exports" / f"{Name}_rigged.fbx"), use_selection=True,
                         add_leaf_bones=False, bake_anim=False, path_mode="COPY", embed_textures=True,
                         mesh_smooth_type="FACE")

# skeleton json (build_body.py): heads/tails + per-bone mesh stats + torso slices, in Blender frame (Z up, faces -Y)
V = np.array([tuple(v.co) for v in mesh.data.vertices])
best, wmax = W.argmax(1), W.max(1)
bones = {}
for i, n in enumerate(ours):
    h, t = np.array(P[MAP[n][0]]), np.array(P[MAP[n][1]])
    sel = V[(best == i) & (wmax > 0.5)]
    if len(sel) < 10:   # a bone that never leads (this source's neck): every vertex it moves noticeably
        sel = V[W[:, i] > 0.2]
    seg = t - h
    u = np.clip(((sel - h) @ seg) / (seg @ seg), 0, 1)
    r = np.linalg.norm(sel - (h + u[:, None] * seg), axis=1)
    q = lambda c: [round(float(np.percentile(sel[:, c], 5)), 4), round(float(np.percentile(sel[:, c], 95)), 4)]
    bones[n] = {"parent": PARENT[n], "head": [round(float(x), 5) for x in h], "tail": [round(float(x), 5) for x in t],
                "mesh": {"n": int(len(sel)), "r_med": round(float(np.median(r)), 4), "x": q(0), "y": q(1), "z": q(2)}}
arm_ids = [ours.index(n) for n in ours if n.split("_")[0] in ("upperarm", "forearm", "hand")]
torso = V[~np.isin(best, arm_ids)]
slices = []
for z in np.arange(0.45, 0.93, 0.025) * height:
    s = torso[np.abs(torso[:, 2] - z) < 0.015]
    if len(s) > 10:
        slices.append({"z": round(float(z), 4), "x": [round(float(np.percentile(s[:, 0], 3)), 4), round(float(np.percentile(s[:, 0], 97)), 4)],
                       "y": [round(float(np.percentile(s[:, 1], 3)), 4), round(float(np.percentile(s[:, 1], 97)), 4)]})
out = {"source": f"Content/Art/Exports/{Name}_rigged.fbx",
       "frame": "blender: Z up, metres, character faces -Y, character left = +X; rest = T-pose (Mixamo-style source rig, Scripts/rig_mixamo.py)",
       "mesh_stats": "per bone: vertices whose strongest weight (>0.5) is that bone; r_med = median distance to bone segment; x/y/z = 5th/95th percentile",
       "bones": bones, "torso_slices": slices, "torso_note": "z bands +-1.5cm, arm verts excluded, 3rd/97th percentile extents"}
(ROOT / "training/characters" / f"{name}_skeleton.json").write_text(json.dumps(out, indent=1))
print(f"RIG OK: {len(ours)} bones, {len(V)} verts ({unweighted} unweighted), {len(slices)} torso slices")
for n in ours:
    b = bones[n]
    print(f"  {n:11s} head {b['head']} tail {b['tail']} verts {b['mesh']['n']} r_med {b['mesh']['r_med']}")
