"""Mixamo-style or AccuRig/CC (UE5 Mannequin names) rigged FBX -> a PoSlap athlete (headless Blender): the 17-bone
skeleton build_body.py expects. An AccuRig A-pose gets its arms straightened to Grandpa/Matt's rest (10 deg below horizontal).

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
from mathutils import Matrix, Vector

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
UE = "spine_01" in arm.data.bones   # Reallusion AccuRig / CC export: UE5 Mannequin names, A-pose bind
if UE:
    # our bone <- (head from, tail from, source anchor bones); every other source bone (twists, fingers, toes, breasts,
    # face) joins the anchor it hangs under. Spine split by height to match Grandpa/Matt (pelvis to ~59 %, belly to ~66 %).
    MAP = {"pelvis": ("pelvis.h", "spine_03.h", ["pelvis", "spine_01", "spine_02"]),
           "belly": ("spine_03.h", "spine_04.h", ["spine_03"]),
           "chest": ("spine_04.h", "neck_01.h", ["spine_04", "spine_05", "clavicle_l", "clavicle_r"]),
           "neck": ("neck_01.h", "head.h", ["neck_01", "neck_02"]),
           "head": ("head.h", "Head.t", ["head"])}
    for s in "lr":
        MAP.update({f"upperarm_{s}": (f"upperarm_{s}.h", f"lowerarm_{s}.h", [f"upperarm_{s}"]),
                    f"forearm_{s}": (f"lowerarm_{s}.h", f"hand_{s}.h", [f"lowerarm_{s}"]),
                    f"hand_{s}": (f"hand_{s}.h", f"hand_{s}.tip", [f"hand_{s}"]),
                    f"thigh_{s}": (f"thigh_{s}.h", f"calf_{s}.h", [f"thigh_{s}"]),
                    f"shin_{s}": (f"calf_{s}.h", f"foot_{s}.h", [f"calf_{s}"]),
                    f"foot_{s}": (f"foot_{s}.h", f"foot_{s}.tip", [f"foot_{s}"])})
    anchor = {sb: n for n in MAP for sb in MAP[n][2]}
    for b in arm.data.bones:   # every source bone -> the first anchor up its parent chain
        p = b
        while p is not None and p.name not in anchor:
            p = p.parent
        if p is not None and b.name not in anchor:
            MAP[anchor[p.name]][2].append(b.name)
    # A-pose -> Grandpa/Matt's rest: each arm straight, along +-X, 10 deg below horizontal (palms turn down with it)
    arm.data.pose_position = "POSE"
    mi = arm.matrix_world.inverted().to_3x3()
    for s, sx in (("l", 1.0), ("r", -1.0)):
        target = (mi @ Vector((sx * np.cos(np.radians(10)), 0.0, -np.sin(np.radians(10))))).normalized()
        for b, nxt in ((f"upperarm_{s}", f"lowerarm_{s}"), (f"lowerarm_{s}", f"hand_{s}"), (f"hand_{s}", f"middle_03_{s}")):
            bpy.context.view_layer.update()
            pb, pn = arm.pose.bones[b], arm.pose.bones[nxt]
            h = pb.head.copy()
            q = (pn.head - h).normalized().rotation_difference(target)
            pb.matrix = Matrix.Translation(h) @ q.to_matrix().to_4x4() @ Matrix.Translation(-h) @ pb.matrix
    bpy.context.view_layer.update()
    src_pts = {}
    for pb in arm.pose.bones:
        src_pts[pb.name + ".h"] = arm.matrix_world @ pb.head
        src_pts[pb.name + ".t"] = arm.matrix_world @ pb.tail
else:
    arm.data.pose_position = "REST"
    src_pts = {}
    for b in arm.data.bones:
        src_pts[b.name + ".h"] = arm.matrix_world @ b.head_local
        src_pts[b.name + ".t"] = arm.matrix_world @ b.tail_local

# bake the bind (UE: the straightened) pose into the mesh, in world space
bpy.context.view_layer.objects.active = mesh
if mesh.data.shape_keys:   # CC visemes/expressions: an armature can't be applied over them (cheek morphs come later)
    print(f"dropping {len(mesh.data.shape_keys.key_blocks)} shape keys")
    mesh.shape_key_clear()
for md in [m for m in mesh.modifiers if m.type == "ARMATURE"]:
    bpy.ops.object.modifier_apply(modifier=md.name)
mw = mesh.matrix_world.copy()
mesh.parent = None
mesh.matrix_world = mw
bpy.ops.object.select_all(action="DESELECT")
mesh.select_set(True)
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
bpy.data.objects.remove(arm)

if UE:   # CC bone tails don't mark the fingertip or toe tip: take them from the mesh (95th pct along the limb)
    Vw = np.array([tuple(v.co) for v in mesh.data.vertices])
    gname = {g.index: g.name for g in mesh.vertex_groups}
    lead = np.array([gname[max(v.groups, key=lambda g: g.weight).group] if len(v.groups) else "" for v in mesh.data.vertices])
    for s in "lr":
        for b, start, d in ((f"hand_{s}", src_pts[f"hand_{s}.h"], src_pts[f"middle_03_{s}.h"] - src_pts[f"hand_{s}.h"]),
                            (f"foot_{s}", src_pts[f"ball_{s}.h"], Vector((0.0, -1.0, 0.0)))):
            d = np.array(d.normalized())
            sel = Vw[np.isin(lead, MAP[b][2])]
            reach = float(np.percentile((sel - np.array(start)) @ d, 99))
            tip = np.array(start) + reach * d
            if b.startswith("foot"):
                tip[2] = src_pts[f"ball_{s}.h"].z   # toe tip at ball height, as Mixamo's ToeBase end
            src_pts[f"{b}.tip"] = Vector(tip)

zs = np.array([v.co.z for v in mesh.data.vertices])
z0, top = float(zs.min()), float(zs.max())
k = height / (top - z0)
for v in mesh.data.vertices:
    v.co = Vector(((v.co.x) * k, v.co.y * k, (v.co.z - z0) * k))
P = {key: Vector((p.x * k, p.y * k, (p.z - z0) * k)) for key, p in src_pts.items()}
hh = P[MAP["head"][0]]
P["Head.t"] = Vector((hh.x, hh.y, height))  # crown, not the source's helper end
print(f"scale {k:.4f}: {top - z0:.3f} m -> {height} m")
if UE:   # AccuRig's arm joints: one shoulder sunk into the chest, elbows at the sleeve hem (A8 asymmetric / short humerus)
    cx = float(P["pelvis.h"].x)
    sh = max(abs(P["upperarm_l.h"].x - cx), abs(P["upperarm_r.h"].x - cx))   # the wider shoulder = the armpit edge
    for s, sx in (("l", 1.0), ("r", -1.0)):
        P[f"upperarm_{s}.h"].x = cx + sx * sh
        # elbow at 56 % shoulder -> wrist (upper arm : forearm = 0.186 : 0.146 H, Winter); the wrist stays AccuRig's
        P[f"lowerarm_{s}.h"] = P[f"upperarm_{s}.h"].lerp(P[f"hand_{s}.h"], 0.186 / (0.186 + 0.146))
    for key in ("foot_{}.tip",):   # toe tips mirrored: the scan's right foot turns out more than its left
        l, r = P[key.format("l")], P[key.format("r")]
        m = Vector(((abs(l.x - cx) + abs(r.x - cx)) / 2, (l.y + r.y) / 2, (l.z + r.z) / 2))
        P[key.format("l")], P[key.format("r")] = Vector((cx + m.x, m.y, m.z)), Vector((cx - m.x, m.y, m.z))
    for s in "lr":
        print(f"  arm {s}: shoulder {tuple(round(c, 3) for c in P[f'upperarm_{s}.h'])} elbow {tuple(round(c, 3) for c in P[f'lowerarm_{s}.h'])}")

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
if UE:   # upper arm / forearm weights re-split along the arm so the skin bends at the moved elbow (6 cm blend)
    Vn = np.array([tuple(v.co) for v in mesh.data.vertices])
    for s in "lr":
        iu, i_f = ours.index(f"upperarm_{s}"), ours.index(f"forearm_{s}")
        sh, el, wr = (np.array(P[f"{j}_{s}.h"]) for j in ("upperarm", "lowerarm", "hand"))
        d = (wr - sh) / np.linalg.norm(wr - sh)
        f = np.clip(((Vn - sh) @ d - (el - sh) @ d + 0.03) / 0.06, 0.0, 1.0)
        w = W[:, iu] + W[:, i_f]
        W[:, iu], W[:, i_f] = w * (1 - f), w * f
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
       "frame": "blender: Z up, metres, character faces -Y, character left = +X; rest = " + ("arms straight 10 deg below horizontal (AccuRig/CC source rig, A-pose straightened" if UE else "T-pose (Mixamo-style source rig") + ", Scripts/rig_mixamo.py)",
       "mesh_stats": "per bone: vertices whose strongest weight (>0.5) is that bone; r_med = median distance to bone segment; x/y/z = 5th/95th percentile",
       "bones": bones, "torso_slices": slices, "torso_note": "z bands +-1.5cm, arm verts excluded, 3rd/97th percentile extents"}
(ROOT / "training/characters" / f"{name}_skeleton.json").write_text(json.dumps(out, indent=1))
print(f"RIG OK: {len(ours)} bones, {len(V)} verts ({unweighted} unweighted), {len(slices)} torso slices")
for n in ours:
    b = bones[n]
    print(f"  {n:11s} head {b['head']} tail {b['tail']} verts {b['mesh']['n']} r_med {b['mesh']['r_med']}")
