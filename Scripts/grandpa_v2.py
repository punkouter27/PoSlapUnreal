"""Grandpa v2: real-time hygiene pass on the rigged Grandpa, written to NEW files (the in-game Grandpa is untouched).

    blender -b Content/Art/Raw/Grandpa_rigged.blend --python Scripts/grandpa_v2.py -- <motion.json>

motion.json (record_motion: a real Grandpa turn from match.py + a range-of-motion sweep, per-body world transforms)
drives the rig exactly as Unreal does (MuJoCo body -> bone), so the test animations are the game's own motion.
Writes:
  Content/Art/Raw/Grandpa_v2.blend
  Content/Art/Exports/Grandpa_v2.glb, Grandpa_v2.fbx, Grandpa_v2_LOD1.fbx, Grandpa_v2_LOD2.fbx, Grandpa_v2_Collision.fbx
  Content/Art/Exports/Grandpa_v2_Textures/  (BaseColor, Normal (GL) + Normal_DX, ORM, Mask; 2048, power of two)
  DOCS/20261028/grandpa_v2/  (report.json, slap.mp4, rom.mp4, stills)
"""
import bpy, bmesh, json, math, os, subprocess, sys, time
from pathlib import Path
from mathutils import Matrix, Quaternion, Vector
import numpy as np

MOTION = Path(sys.argv[sys.argv.index("--") + 1])
ROOT = Path(bpy.data.filepath).parents[3]
RAW, EXP = ROOT / "Content/Art/Raw", ROOT / "Content/Art/Exports"
TEX, DOC = EXP / "Grandpa_v2_Textures", ROOT / "DOCS/20261028/grandpa_v2"
TEX.mkdir(parents=True, exist_ok=True)
DOC.mkdir(parents=True, exist_ok=True)
R = {"steps": []}
t0 = time.time()


def step(name, **k):
    R["steps"].append({"step": name, "s": round(time.time() - t0, 1), **k})
    print(f"[v2] {name} {json.dumps(k)[:300]}", flush=True)


rig, mesh = bpy.data.objects["Grandpa_rig"], bpy.data.objects["Grandpa_mesh"]
scene = bpy.context.scene
for o in list(bpy.data.objects):
    if o not in (rig, mesh):
        bpy.data.objects.remove(o)


def ctx(o):
    return bpy.context.temp_override(object=o, active_object=o, selected_objects=[o], selected_editable_objects=[o])


def activate(o):
    for x in bpy.context.view_layer.objects:
        x.select_set(False)
    bpy.context.view_layer.objects.active = o
    o.select_set(True)


# --- textures: relink the scan's PBR set ---------------------------------------------------------------------------
for img in bpy.data.images:
    p = EXP / "Grandpa_tex" / f"{img.name}.png"
    if p.exists():
        img.filepath = str(p)
        img.reload()
step("textures relinked", images=[[i.name, list(i.size), i.colorspace_settings.name] for i in bpy.data.images if i.size[0]])

# --- motion: MuJoCo body frames -> Blender bones ------------------------------------------------------------------
M = json.loads(MOTION.read_text())
BODIES = M["bodies"]


def T(p):
    return Matrix.Translation(Vector(p[:3])) @ Quaternion(p[3:]).to_matrix().to_4x4()


REST = {b.name: b.matrix_local.copy() for b in rig.data.bones}
# the rigid map Blender rest -> MuJoCo qpos0 (Kabsch on joint positions); its residual is the skeleton-vs-sim check
H = np.array([list(rig.data.bones[n].head_local) for n in BODIES])
P = np.array([p[:3] for p in M["rest"]])
hc, pc = H.mean(0), P.mean(0)
U, _, Vt = np.linalg.svd((H - hc).T @ (P - pc))
Rk = (U @ np.diag([1, 1, np.sign(np.linalg.det(U @ Vt))]) @ Vt).T
tk = pc - Rk @ hc
resid = np.linalg.norm((Rk @ H.T).T + tk - P, axis=1)
G = Matrix.Translation(Vector(tk)) @ Matrix([list(r) for r in Rk]).to_4x4()
Gi = G.inverted()
T0i = [T(p).inverted() for p in M["rest"]]
step("skeleton vs simulator", rms_mm=round(float(np.sqrt((resid ** 2).mean())) * 1000, 2), max_mm=round(float(resid.max()) * 1000, 2),
     worst=BODIES[int(resid.argmax())])


def posed(frame):
    return {n: Gi @ T(frame[i]) @ T0i[i] @ G @ REST[n] for i, n in enumerate(BODIES)}


ORDER = []
def walk(b):
    ORDER.append(b.name)
    for c in b.children:
        walk(c)
for b in rig.data.bones:
    if not b.parent:
        walk(b)


def apply_pose(frame, prev=None):
    Pm, out = posed(frame), {}
    for n in ORDER:
        b, pb = rig.data.bones[n], rig.pose.bones[n]
        Rp = b.parent.matrix_local if b.parent else Matrix.Identity(4)
        Pp = out[b.parent.name] if b.parent else Matrix.Identity(4)
        Pb = Pm.get(n, Pp @ Rp.inverted() @ b.matrix_local)
        out[n] = Pb
        loc, rot, _ = ((Rp.inverted() @ b.matrix_local).inverted() @ Pp.inverted() @ Pb).decompose()
        pb.rotation_mode = "QUATERNION"
        if prev is not None and n in prev and rot.dot(prev[n]) < 0:
            rot.negate()   # sign-continuous, so keys interpolate the short way
        pb.location, pb.rotation_quaternion, pb.scale = loc, rot, (1, 1, 1)
    return {n: rig.pose.bones[n].rotation_quaternion.copy() for n in ORDER}


def reset_pose():
    for pb in rig.pose.bones:
        pb.location, pb.rotation_quaternion, pb.rotation_euler, pb.scale = (0, 0, 0), (1, 0, 0, 0), (0, 0, 0), (1, 1, 1)


# --- deformation metrics over the range-of-motion sweep (volume, edge stretch) ----------------------------------
me = mesh.data
tri = np.array([list(t.vertices) for t in me.loop_triangles]) if me.loop_triangles else None
if tri is None:
    me.calc_loop_triangles()
    tri = np.array([list(t.vertices) for t in me.loop_triangles])
edges = np.array([list(e.vertices) for e in me.edges])


def coords(o):
    dg = bpy.context.evaluated_depsgraph_get()
    ev = o.evaluated_get(dg)
    m = ev.to_mesh()
    a = np.empty(len(m.vertices) * 3)
    m.vertices.foreach_get("co", a)
    ev.to_mesh_clear()
    return a.reshape(-1, 3)


def volume(c):
    a, b, d = c[tri[:, 0]], c[tri[:, 1]], c[tri[:, 2]]
    return float(np.einsum("ij,ij->i", a, np.cross(b, d)).sum() / 6)


def rom_metrics(label):
    reset_pose()
    bpy.context.view_layer.update()
    c0 = coords(mesh)
    v0, l0 = volume(c0), np.linalg.norm(c0[edges[:, 0]] - c0[edges[:, 1]], axis=1)
    per = {}
    for k in range(0, len(M["rom"]), 3):
        apply_pose(M["rom"][k])
        bpy.context.view_layer.update()
        c = coords(mesh)
        l = np.linalg.norm(c[edges[:, 0]] - c[edges[:, 1]], axis=1) / np.maximum(l0, 1e-6)
        g = per.setdefault(M["rom_joint"][k], {"dv": 0.0, "stretch": 1.0, "squash": 1.0})
        g["dv"] = max(g["dv"], abs(volume(c) / v0 - 1) * 100)
        g["stretch"] = max(g["stretch"], float(np.percentile(l, 99.9)))
        g["squash"] = min(g["squash"], float(np.percentile(l, 0.1)))
    reset_pose()
    for g in per.values():
        g.update({k: round(v, 3) for k, v in g.items()})
    step(f"ROM metrics ({label})", worst_dv=max(per.items(), key=lambda kv: kv[1]["dv"]))
    return per


R["rom_before"] = rom_metrics("original weights")

# --- skeleton: root bone at the origin, twist helpers -------------------------------------------------------------
activate(rig)
bpy.ops.object.mode_set(mode="EDIT")
eb = rig.data.edit_bones
root = eb.new("root")
root.head, root.tail, root.roll = (0, 0, 0), (0, 0.25, 0), 0.0   # identity rest matrix: (0,0,0), no rotation
eb["pelvis"].parent = root
TWIST = {"upperarm_l": "prox", "upperarm_r": "prox", "thigh_l": "prox", "thigh_r": "prox", "forearm_l": "dist", "forearm_r": "dist"}
for b, where in TWIST.items():
    p = eb[b]
    t = eb.new(b + "_twist")
    t.head, t.tail = (p.head, p.head.lerp(p.tail, 0.5)) if where == "prox" else (p.head.lerp(p.tail, 0.5), p.tail)
    t.align_roll(p.z_axis)
    t.parent, t.use_connect, t.use_deform = p, False, True
def mirror_err():
    out = []
    for b in eb:
        if b.name.endswith("_l") and b.name[:-2] + "_r" in eb:
            zl, zr = b.z_axis, eb[b.name[:-2] + "_r"].z_axis
            out.append([b.name[:-2], round(math.degrees(zl.angle(Vector((-zr.x, zr.y, zr.z)))), 1)])
    return out


before = mirror_err()
for b in eb:   # left/right joint axes mirror each other (the scan rig's foot rolls were 112 deg apart: IK axis flips)
    if b.name.endswith("_l") and b.name[:-2] + "_r" in eb:
        zl = b.z_axis
        eb[b.name[:-2] + "_r"].align_roll(Vector((-zl.x, zl.y, zl.z)))
for b in TWIST:
    eb[b + "_twist"].align_roll(eb[b].z_axis)
after = mirror_err()
bpy.ops.object.mode_set(mode="OBJECT")
REST = {b.name: b.matrix_local.copy() for b in rig.data.bones}   # rolls changed: re-read the rest frames the motion maps onto
ORDER.clear()
walk(rig.data.bones["root"])
step("root + twist bones, mirrored rolls", bones=len(rig.data.bones), roll_mirror_deg_before=before, roll_mirror_deg_after=after)
R["roll_mirror"] = {"before": before, "after": after}
for b, where in TWIST.items():
    pb = rig.pose.bones[b + "_twist"]
    c = pb.constraints.new("COPY_ROTATION")
    c.target, c.subtarget = rig, b if where == "prox" else "hand" + b[-2:]
    c.use_x = c.use_z = False
    c.target_space = c.owner_space = "LOCAL"
    c.invert_y = where == "prox"      # shoulder/hip: counter half the limb's own twist; wrist: follow half the hand's
    c.influence = 0.5

# --- skin weights: twist shares, smooth, max 4, normalized --------------------------------------------------------
vg = mesh.vertex_groups
co = np.array([list(v.co) for v in me.vertices])
orig = [[(g.group, g.weight) for g in v.groups] for v in me.vertices]
for b, where in TWIST.items():
    src, dst = vg[b], vg.new(name=b + "_twist")
    head, tail = rig.data.bones[b].head_local, rig.data.bones[b].tail_local
    ax = np.array(tail - head)
    for v in me.vertices:
        w = next((g.weight for g in v.groups if g.group == src.index), 0.0)
        if w <= 0 or sum(g.weight > 1e-4 for g in v.groups) >= 4:   # no free slot: a 5th influence would be cut by the cap
            continue
        s = float(np.dot(co[v.index] - np.array(head), ax) / np.dot(ax, ax))
        f = 0.5 * min(max((0.5 - s) / 0.5 if where == "prox" else (s - 0.5) / 0.5, 0.0), 1.0)
        if f > 0:
            src.add([v.index], w * (1 - f), "REPLACE")
            dst.add([v.index], w * f, "REPLACE")
def tidy():
    with ctx(mesh):
        bpy.ops.object.vertex_group_clean(group_select_mode="ALL", limit=0.001)
        bpy.ops.object.vertex_group_limit_total(group_select_mode="ALL", limit=4)
        bpy.ops.object.vertex_group_normalize_all(group_select_mode="ALL", lock_active=False)


def snapshot():
    return [[(g.group, g.weight) for g in v.groups] for v in me.vertices]


def restore(snap):
    every = list(range(len(me.vertices)))
    for g in vg:
        g.remove(every)
    for i, gs in enumerate(snap):
        for gi, w in gs:
            vg[gi].add([i], w, "REPLACE")


def score(per):   # lower = better: volume change + worst stretch + worst squash, averaged over the joints
    return sum(g["dv"] / 100 + (g["stretch"] - 1) + (1 - g["squash"]) for g in per.values()) / len(per)


activate(mesh)
tidy()
R["rom_twist"] = rom_metrics("twist, no smoothing")
snap = snapshot()
bpy.ops.object.mode_set(mode="WEIGHT_PAINT")
bpy.ops.object.vertex_group_smooth(group_select_mode="ALL", factor=0.5, repeat=1)
bpy.ops.object.mode_set(mode="OBJECT")
tidy()
R["rom_smooth"] = rom_metrics("twist + smoothing")
cands = {"original": score(R["rom_before"]), "twist": score(R["rom_twist"]), "twist_smooth": score(R["rom_smooth"])}
kept = min(cands, key=cands.get)
R["weight_choice"] = {**{k: round(v, 4) for k, v in cands.items()}, "kept": kept}
if kept == "twist":
    restore(snap)
elif kept == "original":   # twist helpers don't pay on this mesh: drop them rather than ship idle bones
    for g in [g for g in vg if g.name.endswith("_twist")]:
        vg.remove(g)
    restore(orig)
    activate(rig)
    bpy.ops.object.mode_set(mode="EDIT")
    for b in TWIST:
        rig.data.edit_bones.remove(rig.data.edit_bones[b + "_twist"])
    bpy.ops.object.mode_set(mode="OBJECT")
    REST = {b.name: b.matrix_local.copy() for b in rig.data.bones}
    ORDER.clear()
    walk(rig.data.bones["root"])
    activate(mesh)
R["rom_after"] = {"original": R["rom_before"], "twist": R["rom_twist"], "twist_smooth": R["rom_smooth"]}[kept]
step("weight variant", **R["weight_choice"])
infl = [sum(1 for g in v.groups if g.weight > 1e-4) for v in me.vertices]
sums = [sum(g.weight for g in v.groups) for v in me.vertices]
R["weights"] = {"max_influences": max(infl), "hist": {str(k): infl.count(k) for k in range(1, 5)},
                "unnormalized": sum(abs(s - 1) > 1e-3 for s in sums), "unweighted": infl.count(0)}
step("weights", **R["weights"])

# --- geometry hygiene ----------------------------------------------------------------------------------------------
bm = bmesh.new()
bm.from_mesh(me)
R["geometry"] = {"verts": len(bm.verts), "tris": sum(len(f.verts) - 2 for f in bm.faces), "ngons": sum(len(f.verts) > 4 for f in bm.faces),
                 "quads": sum(len(f.verts) == 4 for f in bm.faces), "zero_area": sum(f.calc_area() < 1e-10 for f in bm.faces),
                 "nonmanifold_edges": sum(not e.is_manifold for e in bm.edges), "loose_verts": sum(not v.link_edges for v in bm.verts)}
bm.free()
step("geometry", **R["geometry"])


def uv_stats(layer, res=512):
    uv = np.empty(len(me.loops) * 2)
    layer.data.foreach_get("uv", uv)
    uv = uv.reshape(-1, 2)
    cover = np.zeros((res, res), np.uint16)
    dens = []
    for poly in me.polygons:
        li = list(poly.loop_indices)
        for k in range(1, len(li) - 1):
            a, b, c = uv[li[0]], uv[li[k]], uv[li[k + 1]]
            area_uv = abs((b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1])) / 2
            if area_uv > 0:
                dens.append(math.sqrt(area_uv / max(poly.area / (len(li) - 2), 1e-12)))
            lo, hi = np.floor(np.minimum(np.minimum(a, b), c) * res).astype(int), np.ceil(np.maximum(np.maximum(a, b), c) * res).astype(int)
            lo, hi = np.clip(lo, 0, res - 1), np.clip(hi, 0, res)
            if (hi - lo).min() <= 0:
                continue
            xs, ys = np.meshgrid(np.arange(lo[0], hi[0]) + 0.5, np.arange(lo[1], hi[1]) + 0.5)
            p = np.stack([xs / res, ys / res], -1)
            d = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
            if abs(d) < 1e-14:
                continue
            l1 = ((b[1] - c[1]) * (p[..., 0] - c[0]) + (c[0] - b[0]) * (p[..., 1] - c[1])) / d
            l2 = ((c[1] - a[1]) * (p[..., 0] - c[0]) + (a[0] - c[0]) * (p[..., 1] - c[1])) / d
            inside = (l1 > 1e-6) & (l2 > 1e-6) & (1 - l1 - l2 > 1e-6)
            cover[lo[1]:hi[1], lo[0]:hi[0]] += inside
    used = (cover > 0).sum()
    dens = np.array(dens)
    return {"coverage_pct": round(100 * used / res ** 2, 1), "overlap_pct_of_used": round(100 * (cover > 1).sum() / max(used, 1), 2),
            "texel_density_cv": round(float(dens.std() / dens.mean()), 3),
            "texel_density_p95_p5": round(float(np.percentile(dens, 95) / np.percentile(dens, 5)), 2)}


R["uv0"] = uv_stats(me.uv_layers[0])
step("UV0", **R["uv0"])
lm = me.uv_layers.new(name="UVLightmap")
me.uv_layers.active = lm
with ctx(mesh):
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.008, area_weight=0.0, scale_to_bounds=False)
    bpy.ops.object.mode_set(mode="OBJECT")
me.uv_layers.active = me.uv_layers[0]   # (edit mode invalidates layer references: look them up again)
me.uv_layers[0].active_render = True
R["uv1"] = uv_stats(me.uv_layers["UVLightmap"])
step("UV1 lightmap", **R["uv1"])

# --- LODs (before shape keys: a modifier can't be applied over them) ----------------------------------------------
R["lods"] = []
for n, ratio in ((1, 0.5), (2, 0.25)):
    o = mesh.copy()
    o.data = mesh.data.copy()
    o.name = f"Grandpa_v2_LOD{n}"
    scene.collection.objects.link(o)
    activate(o)
    d = o.modifiers.new("Decimate", "DECIMATE")
    d.ratio = ratio
    with ctx(o):
        bpy.ops.object.modifier_move_to_index(modifier="Decimate", index=0)
        bpy.ops.object.modifier_apply(modifier="Decimate")
        bpy.ops.object.vertex_group_limit_total(group_select_mode="ALL", limit=4)
        bpy.ops.object.vertex_group_normalize_all(group_select_mode="ALL", lock_active=False)
    o.data.uv_layers.remove(o.data.uv_layers["UVLightmap"])
    o.hide_render = True
    R["lods"].append({"name": o.name, "tris": sum(len(p.vertices) - 2 for p in o.data.polygons)})
step("LODs", lods=R["lods"])

# --- morph targets: breathing, belly jiggle -------------------------------------------------------------------------
mesh.shape_key_add(name="Basis", from_mix=False)
wt = lambda v, name: next((g.weight for g in v.groups if g.group == vg[name].index), 0.0)
breathe, jiggle = mesh.shape_key_add(name="Breathe", from_mix=False), mesh.shape_key_add(name="Jiggle_Belly", from_mix=False)
for v in me.vertices:
    c, b = wt(v, "chest"), wt(v, "belly")
    breathe.data[v.index].co = v.co + v.normal * 0.010 * (c + 0.6 * b)
    jiggle.data[v.index].co = v.co + (v.normal * 0.004 + Vector((0, 0, -0.012))) * b
R["morphs"] = {k.name: len(k.data) for k in me.shape_keys.key_blocks}
step("morph targets", **R["morphs"])

# --- PBR: AO bake (Cycles), ORM + HDRP mask packing, 2048 power-of-two ---------------------------------------------
mat = me.materials[0]
nodes = mat.node_tree.nodes
imgs = {n.image.name: n for n in nodes if n.type == "TEX_IMAGE" and n.image}
base_n = next(n for k, n in imgs.items() if not k.endswith(("normal", "roughness")))
norm_n = next(n for k, n in imgs.items() if k.endswith("normal"))
mr_n = next(n for k, n in imgs.items() if k.endswith("roughness"))
S = 2048
scene.render.engine = "CYCLES"
scene.cycles.device = "CPU"   # GPU device setup crashed headless (old iGPU driver); AO at 2k is quick on CPU
scene.cycles.samples = 32
ao_img = bpy.data.images.new("T_Grandpa_v2_AO", S, S, alpha=False, float_buffer=False)
ao_img.colorspace_settings.name = "Non-Color"
ao_node = nodes.new("ShaderNodeTexImage")
ao_node.image = ao_img
nodes.active = ao_node
rig.data.pose_position = "REST"
activate(mesh)
with ctx(mesh):
    bpy.ops.object.bake(type="AO", margin=16, use_clear=True)
nodes.remove(ao_node)
rig.data.pose_position = "POSE"
step("AO baked", device=scene.cycles.device, size=S)


def px(img):
    c = img.copy()
    c.scale(S, S)
    a = np.empty(S * S * 4, np.float32)
    c.pixels.foreach_get(a)
    bpy.data.images.remove(c)
    return a.reshape(S, S, 4)


def save(name, arr, colorspace):
    img = bpy.data.images.new(name, S, S, alpha=True, float_buffer=False)
    img.colorspace_settings.name = colorspace
    img.pixels.foreach_set(np.ascontiguousarray(arr, np.float32).ravel())
    img.filepath_raw = str(TEX / f"{name}.png")
    img.file_format = "PNG"
    img.save()
    return img


ao = np.empty(S * S * 4, np.float32)   # the bake lives in this buffer only (a copy of a generated image comes back blank)
ao_img.pixels.foreach_get(ao)
ao = ao.reshape(S, S, 4)[..., 0]
mr = px(mr_n.image)
rough, metal = mr[..., 1], mr[..., 2]       # glTF metallicRoughness: G = roughness, B = metallic
one = np.ones_like(ao)
out = {
    "T_Grandpa_v2_BaseColor": (px(base_n.image), "sRGB"),
    "T_Grandpa_v2_Normal": (px(norm_n.image), "Non-Color"),
    "T_Grandpa_v2_ORM": (np.stack([ao, rough, metal, one], -1), "Non-Color"),                    # UE: R AO, G roughness, B metallic
    "T_Grandpa_v2_Mask": (np.stack([metal, ao, one, 1 - rough], -1), "Non-Color"),               # HDRP: R metal, G AO, B detail, A smooth
}
n_dx = out["T_Grandpa_v2_Normal"][0].copy()
n_dx[..., 1] = 1 - n_dx[..., 1]
out["T_Grandpa_v2_Normal_DX"] = (n_dx, "Non-Color")                                              # UE / DirectX green
saved = {k: save(k, a, cs) for k, (a, cs) in out.items()}
base_n.image, norm_n.image, mr_n.image = saved["T_Grandpa_v2_BaseColor"], saved["T_Grandpa_v2_Normal"], saved["T_Grandpa_v2_ORM"]
R["textures"] = [{"name": k, "size": [S, S], "pow2": True, "colorspace": i.colorspace_settings.name} for k, i in saved.items()]
R["ao_mean"] = round(float(ao.mean()), 3)
step("textures packed", n=len(saved), ao_mean=R["ao_mean"])

# --- collision proxy: one convex hull per body part (UCX_), for a static-mesh import -------------------------------
lod2 = bpy.data.objects["Grandpa_v2_LOD2"]
proxy = lod2.copy()
proxy.data = lod2.data.copy()
proxy.name = proxy.data.name = "SM_Grandpa_v2_Proxy"
proxy.modifiers.clear()
proxy.vertex_groups.clear()
proxy.parent = None
scene.collection.objects.link(proxy)
proxy.hide_render = True
hulls, rng = [], np.random.default_rng(0)
for i, part in enumerate(BODIES):
    ids = [v.index for v in me.vertices if any(g.weight > 0.5 and vg[g.group].name in (part, part + "_twist") for g in v.groups)]
    if len(ids) < 4:
        continue
    pts = co[rng.choice(ids, min(48, len(ids)), replace=False)]
    hb = bmesh.new()
    for p in pts:
        hb.verts.new(p)
    bmesh.ops.convex_hull(hb, input=hb.verts)
    bmesh.ops.delete(hb, geom=[v for v in hb.verts if not v.link_faces], context="VERTS")
    hm = bpy.data.meshes.new(f"UCX_SM_Grandpa_v2_Proxy_{i:02d}")
    hb.to_mesh(hm)
    hb.free()
    ho = bpy.data.objects.new(hm.name, hm)
    scene.collection.objects.link(ho)
    ho.hide_render = True
    hulls.append({"part": part, "verts": len(hm.vertices), "faces": len(hm.polygons)})
step("collision hulls", n=len(hulls), max_verts=max(h["verts"] for h in hulls))
R["collision"] = hulls

# --- test animations: the game's own motion -------------------------------------------------------------------------
actions = {}
for label, frames in (("Slap_Sim", M["frames"]), ("ROM_Test", M["rom"])):
    if rig.animation_data:
        rig.animation_data.action = None
    prev = None
    for f, fr in enumerate(frames, start=1):
        prev = apply_pose(fr, prev)
        for pb in rig.pose.bones:
            if pb.name in BODIES:
                pb.keyframe_insert("location", frame=f, group=pb.name)
                pb.keyframe_insert("rotation_quaternion", frame=f, group=pb.name)
    act = rig.animation_data.action
    act.name, act.use_fake_user = label, True
    act.frame_range = (1, len(frames))
    actions[label] = act
reset_pose()
rig.animation_data.action = actions["Slap_Sim"]
step("test animations", actions={k: len(v.frame_range) and int(v.frame_range[1]) for k, v in actions.items()})

# --- names, transforms, save ------------------------------------------------------------------------------------------
rig.name, rig.data.name = "Armature", "Grandpa_v2_Skeleton"   # FBX -> UE: an object named "Armature" adds no extra root
mesh.name, me.name = "SK_Grandpa_v2", "SK_Grandpa_v2"
for o in (rig, mesh):
    assert all(abs(x - 1) < 1e-6 for x in o.scale) and o.matrix_world == Matrix.Identity(4), o.name
me.calc_tangents(uvmap=me.uv_layers[0].name)
signs = {round(l.bitangent_sign) for l in me.loops}
me.free_tangents()
R["tangents"] = {"mikktspace": True, "bitangent_signs": sorted(signs)}
R["skeleton"] = {"bones": len(rig.data.bones), "deform": sum(b.use_deform for b in rig.data.bones), "root": "root",
                 "root_rest_identity": rig.data.bones["root"].matrix_local == Matrix.Identity(4),
                 "leaf": [b.name for b in rig.data.bones if not b.children], "hierarchy": {b.name: b.parent.name if b.parent else None for b in rig.data.bones}}
bpy.ops.wm.save_as_mainfile(filepath=str(RAW / "Grandpa_v2.blend"), copy=True)
step("saved", blend=str(RAW / "Grandpa_v2.blend"))

# --- exports ---------------------------------------------------------------------------------------------------------------
def select(*objs):
    for x in bpy.context.view_layer.objects:
        x.select_set(False)
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]


select(rig, mesh)
bpy.ops.export_scene.gltf(filepath=str(EXP / "Grandpa_v2.glb"), export_format="GLB", use_selection=True, export_yup=True,
                          export_tangents=True, export_normals=True, export_texcoords=True, export_materials="EXPORT",
                          export_skins=True, export_all_influences=False, export_morph=True, export_morph_normal=True,
                          export_animations=True, export_animation_mode="ACTIONS")
fbx = dict(use_selection=True, object_types={"ARMATURE", "MESH"}, axis_forward="-Z", axis_up="Y", apply_unit_scale=True,
           use_mesh_modifiers=True, mesh_smooth_type="FACE", use_tspace=True, add_leaf_bones=False, use_armature_deform_only=True,
           primary_bone_axis="Y", secondary_bone_axis="X", bake_anim=True, bake_anim_use_all_actions=True,
           bake_anim_use_nla_strips=False, bake_anim_force_startend_keying=True, path_mode="COPY", embed_textures=False)
bpy.ops.export_scene.fbx(filepath=str(EXP / "Grandpa_v2.fbx"), **fbx)
for n in (1, 2):
    select(rig, bpy.data.objects[f"Grandpa_v2_LOD{n}"])
    bpy.ops.export_scene.fbx(filepath=str(EXP / f"Grandpa_v2_LOD{n}.fbx"), **{**fbx, "bake_anim": False})
select(proxy, *[o for o in scene.objects if o.name.startswith("UCX_")])
bpy.ops.export_scene.fbx(filepath=str(EXP / "Grandpa_v2_Collision.fbx"), **{**fbx, "object_types": {"MESH"}, "bake_anim": False})
R["exports"] = {p.name: round(p.stat().st_size / 1e6, 2) for p in EXP.glob("Grandpa_v2*.*")}
step("exported", **R["exports"])

# --- render: three-point light, contact-shadow floor, slap + ROM videos, stills ---------------------------------------
front = Vector(np.linalg.inv(Rk) @ np.array([1.0, 0.0, 0.0]))   # red faces +X in MuJoCo
front.z = 0
front.normalize()
side = front.cross(Vector((0, 0, 1)))
ctr = Vector((float(H[0][0]), float(H[0][1]), 0.95))
for eng in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
    try:
        scene.render.engine = eng
        break
    except TypeError:
        pass
scene.render.resolution_x, scene.render.resolution_y, scene.render.fps = 720, 960, 30
scene.render.film_transparent = False
try:
    scene.eevee.taa_render_samples = 32
except AttributeError:
    pass
world = bpy.data.worlds.new("Studio")
world.use_nodes = True
world.node_tree.nodes["Background"].inputs[0].default_value = (0.015, 0.016, 0.02, 1)
scene.world = world
bpy.ops.mesh.primitive_plane_add(size=12, location=(ctr.x, ctr.y, 0))
floor = bpy.context.active_object
fm = bpy.data.materials.new("Floor")
fm.use_nodes = True
bsdf = next(n for n in fm.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
bsdf.inputs["Base Color"].default_value = (0.05, 0.05, 0.055, 1)
bsdf.inputs["Roughness"].default_value = 0.55
floor.data.materials.append(fm)


def light(name, kind, energy, pos, color, size=2.0):
    ld = bpy.data.lights.new(name, kind)
    ld.energy, ld.color = energy, color
    if kind == "AREA":
        ld.size = size
    lo = bpy.data.objects.new(name, ld)
    scene.collection.objects.link(lo)
    lo.location = pos
    tc = lo.constraints.new("TRACK_TO")
    tc.target, tc.track_axis, tc.up_axis = target, "TRACK_NEGATIVE_Z", "UP_Y"
    return lo


target = bpy.data.objects.new("Focus", None)
scene.collection.objects.link(target)
target.location = ctr
light("Key", "AREA", 900, ctr + front * 2.2 + side * 2.0 + Vector((0, 0, 1.6)), (1.0, 0.93, 0.85))     # key : fill ~ 4 : 1
light("Fill", "AREA", 220, ctr + front * 2.4 - side * 2.4 + Vector((0, 0, 0.3)), (0.8, 0.88, 1.0), 3.0)
light("Rim", "AREA", 1100, ctr - front * 2.2 - side * 0.6 + Vector((0, 0, 1.8)), (0.9, 0.95, 1.0), 1.2)
cam = bpy.data.objects.new("Camera", bpy.data.cameras.new("Camera"))
scene.collection.objects.link(cam)
scene.camera = cam
cam.data.lens = 70
cc = cam.constraints.new("TRACK_TO")
cc.target, cc.track_axis, cc.up_axis = target, "TRACK_NEGATIVE_Z", "UP_Y"


def shoot(pos, frame, path):
    cam.location = pos
    scene.frame_set(frame)
    scene.render.filepath = str(path)
    scene.render.image_settings.file_format = "PNG"
    bpy.ops.render.render(write_still=True)


view34 = ctr + (front * 0.8 + side * 0.6).normalized() * 4.6 + Vector((0, 0, 0.35))
key = mesh.data.shape_keys
hands = [BODIES.index("hand_l"), BODIES.index("hand_r")]
speed = [max(np.linalg.norm(np.subtract(M["frames"][f][h][:3], M["frames"][f - 1][h][:3])) for h in hands) * M["fps"] for f in range(1, len(M["frames"]))]
speed = [v if v < 16.0 else 0.0 for v in speed]   # > 16 m/s is the turn reset teleporting him back to his box, not a slap
peak = int(np.argmax(speed)) + 2
R["slap_peak"] = {"frame": peak, "hand_speed_ms": round(max(speed), 2)}
for k, f in enumerate(range(1, len(M["frames"]) + 1, 45)):   # breathing on the morph target through the slap clip
    key.key_blocks["Breathe"].value = 0.0 if k % 2 == 0 else 0.8
    key.key_blocks["Breathe"].keyframe_insert("value", frame=f)
rig.animation_data.action = actions["ROM_Test"]
rig.animation_data.action = None
reset_pose()
shoot(ctr + front * 4.8 + Vector((0, 0, 0.1)), 1, DOC / "rest_front.png")
shoot(ctr + side * 4.8 + Vector((0, 0, 0.1)), 1, DOC / "rest_side.png")
rig.animation_data.action = actions["Slap_Sim"]
if hasattr(rig.animation_data, "action_slot") and actions["Slap_Sim"].slots:
    rig.animation_data.action_slot = actions["Slap_Sim"].slots[0]
for name, f in (("windup", peak - 12), ("contact", peak), ("follow", peak + 12)):
    shoot(view34, max(1, min(f, len(M["frames"]))), DOC / f"slap_{name}.png")


def video(action, n, name, pos):
    rig.animation_data.action = actions[action]
    if hasattr(rig.animation_data, "action_slot") and actions[action].slots:
        rig.animation_data.action_slot = actions[action].slots[0]
    cam.location = pos
    fr = DOC / f"_frames_{name}"
    fr.mkdir(exist_ok=True)
    scene.frame_start, scene.frame_end = 1, n
    scene.render.filepath = str(fr / "f_")
    scene.render.image_settings.file_format = "PNG"
    bpy.ops.render.render(animation=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", "30", "-i", str(fr / "f_%04d.png"), "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-crf", "23", "-movflags", "+faststart", str(DOC / f"{name}.mp4")], check=True)
    for p in fr.glob("*.png"):
        p.unlink()
    fr.rmdir()


video("Slap_Sim", len(M["frames"]), "slap", view34)
video("ROM_Test", len(M["rom"]), "rom", ctr + (front * 0.9 + side * 0.45).normalized() * 5.2 + Vector((0, 0, 0.2)))
step("rendered", videos=["slap.mp4", "rom.mp4"])

# --- verify the exports by re-importing them into an empty file ---------------------------------------------------------
R["verify"] = {}
for f in ("Grandpa_v2.glb", "Grandpa_v2.fbx"):
    bpy.ops.wm.read_homefile(use_empty=True)
    (bpy.ops.import_scene.gltf if f.endswith(".glb") else bpy.ops.import_scene.fbx)(filepath=str(EXP / f))
    arm = next(o for o in bpy.data.objects if o.type == "ARMATURE")
    sk = max((o for o in bpy.data.objects if o.type == "MESH"), key=lambda o: len(o.data.vertices))   # not the bone-shape icospheres
    inf = max(sum(1 for g in v.groups if g.weight > 1e-4) for v in sk.data.vertices)
    R["verify"][f] = {"bones": len(arm.data.bones), "root": [b.name for b in arm.data.bones if not b.parent],
                      "verts": len(sk.data.vertices), "max_influences": inf,
                      "shape_keys": [k.name for k in sk.data.shape_keys.key_blocks] if sk.data.shape_keys else [],
                      "actions": sorted(a.name for a in bpy.data.actions), "uv_layers": [u.name for u in sk.data.uv_layers],
                      "dims_m": [round(x, 3) for x in arm.dimensions], "mesh_dims_m": [round(x, 3) for x in sk.dimensions]}
    step(f"verified {f}", **R["verify"][f])
R["seconds"] = round(time.time() - t0)
(DOC / "report.json").write_text(json.dumps(R, indent=1))
print("[v2] done", R["seconds"], "s")
