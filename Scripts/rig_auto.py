"""Unrigged humanoid mesh (A- or T-pose scan / AI model, .glb) -> a Mixamo-style rigged FBX for Scripts/rig_mixamo.py.

    blender -b --factory-startup --python Scripts/rig_auto.py -- <in.glb> <out.fbx>
    blender -b --factory-startup --python Scripts/rig_mixamo.py -- <out.fbx> <name> <height_m>

Expects the model standing, facing -Y, character left = +X (Grandpa's frame). Cleans AI-mesh habits: drops a ground
plate (floor-level pieces outside the feet), welds UV-seam splits, removes scraps and slivers left on the soles.
Joint heights follow Grandpa's skeleton scaled to the model's height; the lateral and front-back positions are measured
from the mesh (leg and torso centres per height band; each arm's direction from the shoulder to the hand). Skin: Blender's
automatic (heat) weights, solved on a voxel-remeshed copy (the scan's own faces break Blender's solver) and transferred
to the real mesh. The model is scaled to human size first (heat weighting fails on a 0.78 m model). Then the arms are raised to Grandpa's rest pose (T-pose, arms 10 deg down: build_body.py's
joint frames) and that pose is baked in as the rest pose.
"""
import json, math, sys
from pathlib import Path

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parents[1]
src, out = sys.argv[-2], sys.argv[-1]
TEMPLATE = json.loads((ROOT / "training/characters/grandpa_skeleton.json").read_text())["bones"]
TEMPLATE_H = 1.70   # grandpa card height
ARM_DOWN = math.radians(10)

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=src)
mesh = next(o for o in bpy.data.objects if o.type == "MESH")
bpy.context.view_layer.objects.active = mesh
mesh.select_set(True)
bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

# drop flat loose parts (a ground plate or shadow card under the feet)
tall = max(v.co.z for v in mesh.data.vertices) - min(v.co.z for v in mesh.data.vertices)
for v in mesh.data.vertices:   # human size first: Blender's heat weighting finds no solution on a 0.78 m model
    v.co *= TEMPLATE_H / tall
tall = TEMPLATE_H
bm = bmesh.new()
bm.from_mesh(mesh.data)
bm.verts.ensure_lookup_table()
floor = min(v.co.z for v in bm.verts)
seen, drop, parts = set(), [], []
for v in bm.verts:
    if v.index in seen:
        continue
    stack, part = [v], []
    seen.add(v.index)
    while stack:
        u = stack.pop()
        part.append(u)
        for e in u.link_edges:
            w = e.other_vert(u)
            if w.index not in seen:
                seen.add(w.index)
                stack.append(w)
    parts.append(part)
# a ground plate: floor-level pieces that lie outside the feet (found from the ankle band, 5-10 % up, above any plate);
# checked before the seams are welded, while the plate is still its own pieces
ank = [v.co for v in bm.verts if floor + 0.05 * tall < v.co.z < floor + 0.10 * tall]
fx0, fx1 = min(c.x for c in ank) - 0.06 * tall, max(c.x for c in ank) + 0.06 * tall
fy0, fy1 = min(c.y for c in ank) - 0.12 * tall, max(c.y for c in ank) + 0.06 * tall   # toes reach forward (-Y)
for part in parts:
    if max(p.co.z for p in part) > floor + 0.04 * tall:
        continue
    xs, ys = [p.co.x for p in part], [p.co.y for p in part]
    cx, cy, m = sum(xs) / len(xs), sum(ys) / len(ys), 0.05 * tall
    if not (fx0 < cx < fx1 and fy0 < cy < fy1) or min(xs) < fx0 - m or max(xs) > fx1 + m or min(ys) < fy0 - m or max(ys) > fy1 + m:
        drop += part
bmesh.ops.delete(bm, geom=drop, context="VERTS")
# AI meshes come split along their UV seams (Matt: 595 loose islands): weld them so the body is one piece again
n0 = len(bm.verts)
bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-4 * tall)
print(f"[rig_auto] welded {n0 - len(bm.verts)} seam vertices")
floor = min(v.co.z for v in bm.verts)   # the soles now (the plate may have hung below them)
print(f"[rig_auto] soles {floor:.3f} m")
# plate slivers welded to a sole: long thin faces at floor level (the foot's own faces are ~1 cm)
sliver = [f for f in bm.faces if all(v.co.z < floor + 0.03 * tall for v in f.verts) and max(e.calc_length() for e in f.edges) > 0.04 * tall]
bmesh.ops.delete(bm, geom=sliver, context="FACES")
bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context="VERTS")
print(f"[rig_auto] removed {len(sliver)} floor-level slivers")
# and ribbons of needle-thin ones (area tiny for their longest edge), a few passes as each pass exposes the next
for _ in range(5):
    thin = [f for f in bm.faces if all(v.co.z < floor + 0.03 * tall for v in f.verts) and f.calc_area() < 0.05 * max(e.calc_length() for e in f.edges) ** 2]
    if not thin:
        break
    bmesh.ops.delete(bm, geom=thin, context="FACES")
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces], context="VERTS")
    print(f"[rig_auto] removed {len(thin)} needle-thin floor faces")
# welded, the body is one piece: small pieces left over (plate scraps) go; a prop of 1 % of the mesh or more stays
bm.verts.ensure_lookup_table()
seen, scrap = set(), []
for v in bm.verts:
    if v.index in seen:
        continue
    stack, part = [v], []
    seen.add(v.index)
    while stack:
        u = stack.pop()
        part.append(u)
        for e in u.link_edges:
            w = e.other_vert(u)
            if w.index not in seen:
                seen.add(w.index)
                stack.append(w)
    if len(part) < 0.01 * len(bm.verts):
        scrap += part
bmesh.ops.delete(bm, geom=scrap, context="VERTS")
print(f"[rig_auto] removed {len(scrap)} vertices of small loose scraps")
bm.to_mesh(mesh.data)
bm.free()
print(f"[rig_auto] dropped {len(drop)} ground-plate vertices")

V = np.array([tuple(v.co) for v in mesh.data.vertices])
z0, top = V[:, 2].min(), V[:, 2].max()
H = top - z0
# centre the body on x = y = 0 (the spine is placed on x = 0): x halfway between the knees (arms and props skew a torso
# median), y the middle of the torso's depth
knees = V[(V[:, 2] > z0 + 0.25 * H) & (V[:, 2] < z0 + 0.32 * H)]
split = np.median(knees[:, 0])
cx = (knees[knees[:, 0] < split, 0].mean() + knees[knees[:, 0] >= split, 0].mean()) / 2
torso = V[(V[:, 2] > z0 + 0.55 * H) & (V[:, 2] < z0 + 0.70 * H) & (np.abs(V[:, 0] - cx) < 0.12 * H)]
shift = Vector((-float(cx), -float((np.percentile(torso[:, 1], 5) + np.percentile(torso[:, 1], 95)) / 2), 0))
for v in mesh.data.vertices:
    v.co += shift
V = np.array([tuple(v.co) for v in mesh.data.vertices])
print(f"[rig_auto] centred by {shift.x / H:+.3f} H, {shift.y / H:+.3f} H")
k = H / TEMPLATE_H
T = lambda bone, end: Vector(TEMPLATE[bone][end]) * k + Vector((0, 0, z0))   # grandpa's point, this model's size


def band(z, sel=None, half=0.012):
    """vertices within +-half*H of height z (optionally pre-filtered)"""
    P = V if sel is None else V[sel]
    return P[np.abs(P[:, 2] - z) < half * H]


def mid(P):
    return Vector((np.percentile(P[:, 0], 50), (np.percentile(P[:, 1], 5) + np.percentile(P[:, 1], 95)) / 2, 0))


pts = {}
# spine: grandpa's heights, this torso's centre (verts near the middle line, so A-pose arms don't pull it)
core = np.abs(V[:, 0]) < 0.09 * H
for key, bone, end in (("Hips.h", "pelvis", "head"), ("Spine.h", "belly", "head"), ("Spine1.h", "chest", "head"),
                       ("Neck.h", "neck", "head"), ("Head.h", "head", "head")):
    p = T(bone, end)
    c = mid(band(p.z, core))
    pts[key] = Vector((0.0, c.y, p.z))
pts["Head.t"] = Vector((0.0, pts["Head.h"].y, top))
# legs: centre of each leg's band at grandpa's hip / knee / ankle heights; toe = the front of the foot
for s, S, sign in (("l", "Left", 1), ("r", "Right", -1)):
    side = sign * V[:, 0] > 0.01 * H
    for key, bone, end in ((f"{S}UpLeg.h", f"thigh_{s}", "head"), (f"{S}Leg.h", f"shin_{s}", "head"), (f"{S}Foot.h", f"foot_{s}", "head")):
        p = T(bone, end)
        zb = p.z if bone != f"thigh_{s}" else T(f"thigh_{s}", "tail").z * 0.25 + p.z * 0.75   # hips: a band just below the crotch
        c = mid(band(zb, side))
        pts[key] = Vector((c.x, c.y, p.z))
    ank = pts[f"{S}Foot.h"]
    foot = V[side & (V[:, 2] < z0 + 0.05 * H) & (np.abs(V[:, 0] - ank.x) < 0.08 * H)]
    toe_y = max(float(np.percentile(foot[:, 1], 3)), ank.y - 0.16 * H)   # forward is -Y; a real foot is ~15 % of height
    pts[f"{S}ToeBase.t"] = Vector((float(np.median(foot[:, 0])), toe_y, z0 + 0.02 * H))
    # arm: grandpa's shoulder; the direction to this model's hand; grandpa's segment lengths along it
    sh = T(f"upperarm_{s}", "head")
    sh = Vector((sign * abs(sh.x), pts["Spine1.h"].y, sh.z))
    lens = [((T(f"{b}_l", "tail") - T(f"{b}_l", "head")).length + (T(f"{b}_r", "tail") - T(f"{b}_r", "head")).length) / 2
            for b in ("upperarm", "forearm", "hand")]   # both sides alike (Grandpa's scan has one fist)
    reach = sum(lens)
    up = V[V[:, 2] > z0 + 0.3 * H]   # above any leftover ground plate
    lat = sign * up[:, 0]            # in an A- or T-pose the hands are the most lateral points
    hand = Vector(up[lat > np.percentile(lat, 99.5)].mean(0))
    u = (hand - sh).normalized()
    pts[f"{S}Arm.h"] = sh
    pts[f"{S}ForeArm.h"] = sh + u * lens[0]
    pts[f"{S}Hand.h"] = sh + u * (lens[0] + lens[1])
    pts[f"{S}Hand.t"] = sh + u * reach
    print(f"[rig_auto] {S} arm {math.degrees(math.asin(max(-1, min(1, -u.z)))):.0f} deg down, hand cluster {(hand - sh).length / H:.2f} H from the shoulder")

# plate leftovers welded to the soles: floor-level vertices outside each foot's measured footprint (heel to just past
# the toe tip, ankle +-6 % sideways)
bm = bmesh.new()
bm.from_mesh(mesh.data)
foot_box = [(pts[f"{S}Foot.h"].x - 0.06 * H, pts[f"{S}Foot.h"].x + 0.06 * H, pts[f"{S}ToeBase.t"].y - 0.015 * H, pts[f"{S}Foot.h"].y + 0.06 * H)
            for S in ("Left", "Right")]
left_over = [v for v in bm.verts if v.co.z < z0 + 0.03 * H and not any(b[0] < v.co.x < b[1] and b[2] < v.co.y < b[3] for b in foot_box)]
bmesh.ops.delete(bm, geom=left_over, context="VERTS")
bm.to_mesh(mesh.data)
bm.free()
print(f"[rig_auto] removed {len(left_over)} floor-level vertices outside the feet")
# hip joints are symmetric (the band below the crotch picks up the shorts' drape)
hx, hy = (pts["LeftUpLeg.h"].x - pts["RightUpLeg.h"].x) / 2, (pts["LeftUpLeg.h"].y + pts["RightUpLeg.h"].y) / 2
pts["LeftUpLeg.h"].x, pts["RightUpLeg.h"].x, pts["LeftUpLeg.h"].y, pts["RightUpLeg.h"].y = hx, -hx, hy, hy
CHAIN = [("Hips", None, "Hips.h", "Spine.h"), ("Spine", "Hips", "Spine.h", "Spine1.h"), ("Spine1", "Spine", "Spine1.h", "Neck.h"),
         ("Neck", "Spine1", "Neck.h", "Head.h"), ("Head", "Neck", "Head.h", "Head.t")]
for S in ("Left", "Right"):
    CHAIN += [(f"{S}Arm", "Spine1", f"{S}Arm.h", f"{S}ForeArm.h"), (f"{S}ForeArm", f"{S}Arm", f"{S}ForeArm.h", f"{S}Hand.h"),
              (f"{S}Hand", f"{S}ForeArm", f"{S}Hand.h", f"{S}Hand.t"), (f"{S}UpLeg", "Hips", f"{S}UpLeg.h", f"{S}Leg.h"),
              (f"{S}Leg", f"{S}UpLeg", f"{S}Leg.h", f"{S}Foot.h"), (f"{S}Foot", f"{S}Leg", f"{S}Foot.h", f"{S}ToeBase.t")]
ad = bpy.data.armatures.new("Armature")
ao = bpy.data.objects.new("Armature", ad)
bpy.context.scene.collection.objects.link(ao)
bpy.context.view_layer.objects.active = ao
bpy.ops.object.mode_set(mode="EDIT")
for n, p, h, t in CHAIN:
    e = ad.edit_bones.new(n)
    e.head, e.tail = pts[h], pts[t]
    if p:
        e.parent = ad.edit_bones[p]
bpy.ops.object.mode_set(mode="OBJECT")
# the ToeBase end point rig_mixamo.py reads as "LeftToeBase.t": a zero-weight helper bone at the toe
bpy.ops.object.mode_set(mode="EDIT")
for S in ("Left", "Right"):
    e = ad.edit_bones.new(f"{S}ToeBase")
    e.head, e.tail = pts[f"{S}ToeBase.t"] + Vector((0, 0.03 * H, 0)), pts[f"{S}ToeBase.t"]
    e.parent, e.use_deform = ad.edit_bones[f"{S}Foot"], False
bpy.ops.object.mode_set(mode="OBJECT")

# skin: heat weights on a clean voxel copy (AI meshes' degenerate, overlapping faces break Blender's heat solve), then
# copied to the real mesh by nearest surface
proxy = mesh.copy()
proxy.data = mesh.data.copy()
bpy.context.scene.collection.objects.link(proxy)
rm = proxy.modifiers.new("Remesh", "REMESH")
rm.mode, rm.voxel_size = "VOXEL", 0.006 * H
bpy.context.view_layer.objects.active = proxy
bpy.ops.object.modifier_apply(modifier=rm.name)
bpy.ops.object.select_all(action="DESELECT")
proxy.select_set(True)
ao.select_set(True)
bpy.context.view_layer.objects.active = ao
bpy.ops.object.parent_set(type="ARMATURE_AUTO")
print(f"[rig_auto] voxel proxy {len(proxy.data.vertices)} verts, weighted {sum(1 for v in proxy.data.vertices if v.groups)}")
for g in proxy.vertex_groups:
    mesh.vertex_groups.new(name=g.name)
dt = mesh.modifiers.new("Weights", "DATA_TRANSFER")
dt.object, dt.use_vert_data = proxy, True
dt.data_types_verts = {"VGROUP_WEIGHTS"}
dt.vert_mapping = "POLYINTERP_NEAREST"
dt.layers_vgroup_select_src, dt.layers_vgroup_select_dst = "ALL", "NAME"
bpy.context.view_layer.objects.active = mesh
bpy.ops.object.modifier_apply(modifier=dt.name)
bpy.data.objects.remove(proxy)
mesh.parent = ao
mesh.modifiers.new("Armature", "ARMATURE").object = ao
# heat weighting skips parts it can't solve (non-manifold scans): those vertices copy their nearest weighted neighbour
from mathutils.kdtree import KDTree
ok = [v for v in mesh.data.vertices if any(g.weight > 0 for g in v.groups)]
tree = KDTree(len(ok))
for i, v in enumerate(ok):
    tree.insert(v.co, i)
tree.balance()
names = {g.index: g.name for g in mesh.vertex_groups}
filled = 0
for v in mesh.data.vertices:
    if any(g.weight > 0 for g in v.groups):
        continue
    src_v = ok[tree.find(v.co)[1]]
    for g in src_v.groups:
        mesh.vertex_groups[names[g.group]].add([v.index], g.weight, "REPLACE")
    filled += 1
print(f"[rig_auto] heat weights: {len(ok)} of {len(mesh.data.vertices)} vertices; {filled} filled from their nearest neighbour")

# arms up to the rest pose build_body.py expects (T-pose, 10 deg down), baked in as the new rest pose
bpy.context.view_layer.objects.active = ao
bpy.ops.object.mode_set(mode="POSE")
for S, sign in (("Left", 1), ("Right", -1)):
    pb = ao.pose.bones[f"{S}Arm"]
    cur = (pts[f"{S}Hand.t"] - pts[f"{S}Arm.h"]).normalized()
    want = Vector((sign * math.cos(ARM_DOWN), 0.0, -math.sin(ARM_DOWN)))
    q = cur.rotation_difference(want)   # world rotation about the shoulder
    pb.matrix = Matrix.Translation(pb.head) @ q.to_matrix().to_4x4() @ Matrix.Translation(-pb.head) @ pb.matrix   # armature at the origin
    bpy.context.view_layer.update()
bpy.ops.object.mode_set(mode="OBJECT")
bpy.context.view_layer.objects.active = mesh
bpy.ops.object.modifier_apply(modifier=next(m.name for m in mesh.modifiers if m.type == "ARMATURE"))
bpy.context.view_layer.objects.active = ao
bpy.ops.object.mode_set(mode="POSE")
bpy.ops.pose.armature_apply(selected=False)
bpy.ops.object.mode_set(mode="OBJECT")
mesh.modifiers.new("Armature", "ARMATURE").object = ao

# the glTF's textures live only inside the file: write them next to the FBX so it can embed them
tex = Path(out).with_suffix(".textures")
tex.mkdir(exist_ok=True)
for img in bpy.data.images:
    if img.packed_file or not Path(bpy.path.abspath(img.filepath)).exists():
        img.filepath_raw, img.file_format = str(tex / f"{bpy.path.clean_name(img.name)}.png"), "PNG"
        img.save()
bpy.ops.object.select_all(action="SELECT")
bpy.ops.export_scene.fbx(filepath=out, use_selection=True, add_leaf_bones=True,   # "_end" leaves keep the tails (FBX stores none) bake_anim=False, path_mode="COPY",
                         embed_textures=True, mesh_smooth_type="FACE")
print(f"[rig_auto] OK -> {out}")
