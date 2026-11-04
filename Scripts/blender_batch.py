r"""Headless asset batch: every athlete and arena .blend in Content/Art/Raw -> topology cleanup, LODs, baked detail maps,
runtime exports and a manifest. One command (Blender 5.2, background):

    "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" -b --factory-startup --python Scripts/blender_batch.py -- [assets...] [--skip clean,lods,bake,export] [--size 2048] [--report <dir>]

Assets (default: all), found in Content/Art/Raw:
  athletes  <Name>_rigged.blend (grandpa, kim, matt). <Name>_raw.blend, <NAME>.glb and Grandpa_rigged_scanhands.blend are
            sources (the high-res scan, the scan's hands), never written.
  arena     Arena_v2.blend (the live arena: Scripts/arena_env.py imports its export) as "arena";
            Arena.blend (the first arena, kept for reference) as "arena_v1": cleaned and reported, no runtime export.
Steps per asset:
  clean   per mesh: loose vertices/edges deleted, duplicate faces deleted, boundary cracks welded (merge by distance
          0.1 mm, boundary vertices only, so two surfaces that merely touch are never fused), degenerate edges/faces
          dissolved (< 1 um), small holes (<= 12 edges) filled on the (closed) athletes, each patch on one rim texel. Every surviving
          vertex is checked against the rest positions it had: none may move more than 0.1 mm, or the asset is not saved.
          Shape keys, weights and UVs ride along (bmesh layers). The armature is never touched: bone names, rest
          matrices, joint frames and limb lengths are compared before/after (must be identical). Arena: each prop gets
          a second UV layer "BakeUV" (Smart UV Project, non-overlapping) for its baked maps; UV 0 (the render UV) stays.
  lods    athletes: Scripts/athlete_lods.py in a child Blender (LOD1 50 %, LOD2 25 % faces, CheekDent_L/R rebuilt).
  export  athletes: blender_cheek_morphs.export (Content/Art/Exports/<Name>_rigged.fbx + .glb, Web/assets/<name>.glb with
          gltf-transform meshopt + WebP, morphs and _CHEEKMASK kept). arena: Content/Art/Exports/Arena.glb + Arena.fbx,
          Web/assets/arena.glb (gltf-transform optimize, meshopt + WebP) and Arena_Mobile.glb (Scripts/arena_mobile.py).
  bake    detail maps, linear data (import with sRGB off), 8 px gutter:
            athletes, UV 0, Content/Art/Exports/<Name>_tex/:
              T_<Name>_AO.png        Cycles ambient occlusion at rest (T-pose), 30 cm reach, grayscale
              T_<Name>_Cavity.png    curvature: 0.5 = flat, < 0.5 cavity (creases, wrinkles), > 0.5 convex; grayscale
              T_<Name>_NormalScan.png tangent-space normal (OpenGL +Y) from the high-res scan, when there is one (a raw
                                     scan with >= 1.5x the faces, aligned by a similarity ICP on the torso/head/legs;
                                     rays reach 1.5 cm, so re-posed arms and remodelled hands stay flat (0.5, 0.5, 1))
            arena props, BakeUV, Content/Art/Exports/Textures/Bake/T_<Prop>_AO.png, T_<Prop>_Cavity.png (1 m reach,
              64-1024 px by the prop's area). The maps are delivered as files (manifest), not wired into the materials:
              the site and the game decide where they apply.
Then Content/Art/Exports/asset_manifest.json: per asset the source, cleanup counts, LODs, bakes and exports with byte
sizes, triangle counts and texture sizes; and (--report) an HTML page with the bake thumbnails.
"""
import datetime
import json
import math
import struct
import subprocess
import sys
import time
import zlib
from pathlib import Path

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector, bvhtree, kdtree

sys.path.insert(0, str(Path(__file__).resolve().parent))
from blender_cheek_morphs import export as export_athlete, rig_and_mesh, write_png_gray  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RAW, EXP, WEB = ROOT / "Content/Art/Raw", ROOT / "Content/Art/Exports", ROOT / "Web/assets"
GLTF_TRANSFORM = ROOT / "Web/node_modules/.bin/gltf-transform.cmd"
MOVE_TOL = 1e-4        # m: no rest vertex may move further in the cleanup
WELD = 1e-4            # m: merge distance for boundary cracks
ATHLETES = ("grandpa", "kim", "matt")
ARENAS = {"arena": "Arena_v2.blend", "arena_v1": "Arena.blend"}
AXIS_ROTATIONS = [m for m in (np.eye(3)[list(p)] * np.array(sg)[:, None] for p in
                                ((0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0))
                                for sg in ((1, 1, 1), (1, 1, -1), (1, -1, 1), (1, -1, -1), (-1, 1, 1), (-1, 1, -1), (-1, -1, 1), (-1, -1, -1)))
                  if np.linalg.det(m) > 0]   # the 24 axis-aligned rotations: where the raw scan's up and front are
ARM_GROUPS = ("upperarm_l", "upperarm_r", "forearm_l", "forearm_r", "hand_l", "hand_r")


def log(msg):
    print(f"[batch] {msg}", flush=True)


# ------------------------------------------------------------------------------------------------------------ cleanup
def clean_mesh(obj, fill_holes):
    """Topology cleanup of one mesh (all shape keys / weights / UVs ride along). Returns counts and the largest move."""
    me = obj.data
    rest = np.array([v.co[:] for v in me.vertices])
    bm = bmesh.new()
    bm.from_mesh(me)

    def counts():
        return dict(verts=len(bm.verts), faces=len(bm.faces),
                    nonmanifold_edges=sum(1 for e in bm.edges if len(e.link_faces) > 2),
                    boundary_edges=sum(1 for e in bm.edges if e.is_boundary),
                    loose_verts=sum(1 for v in bm.verts if not v.link_faces),
                    loose_edges=sum(1 for e in bm.edges if not e.link_faces))

    before = counts()
    done = {}
    loose_e = [e for e in bm.edges if not e.link_faces]
    if loose_e:
        bmesh.ops.delete(bm, geom=loose_e, context="EDGES_FACES")
    loose_v = [v for v in bm.verts if not v.link_faces]
    if loose_v:
        bmesh.ops.delete(bm, geom=loose_v, context="VERTS")
    done["loose_deleted"] = len(loose_e) + len(loose_v)
    seen, dup = set(), []
    for f in bm.faces:
        k = tuple(sorted(v.index for v in f.verts))
        if k in seen:
            dup.append(f)
        seen.add(k)
    if dup:
        bmesh.ops.delete(bm, geom=dup, context="FACES_ONLY")
    done["duplicate_faces_deleted"] = len(dup)
    rim = [v for v in bm.verts if any(e.is_boundary for e in v.link_edges)]
    n0 = len(bm.verts)
    if rim:
        bmesh.ops.remove_doubles(bm, verts=rim, dist=WELD)
    done["crack_verts_welded"] = n0 - len(bm.verts)
    n0 = len(bm.verts), len(bm.faces)
    bmesh.ops.dissolve_degenerate(bm, dist=1e-6, edges=bm.edges[:])
    done["degenerate_removed"] = (n0[0] - len(bm.verts)) + (n0[1] - len(bm.faces))
    filled = 0
    if fill_holes:
        bnd = [e for e in bm.edges if e.is_boundary]
        if bnd:
            res = bmesh.ops.holes_fill(bm, edges=bnd, sides=12)
            uvl = bm.loops.layers.uv.active
            for f in res["faces"]:
                filled += 1
                # one rim texel for the whole patch: the rim's corners can sit in different UV islands, and a face
                # spanning islands would smear the atlas across it
                other = next((o for lp in f.loops for o in lp.vert.link_loops if o.face is not f), None)
                if other is not None and uvl is not None:
                    for lp in f.loops:
                        lp[uvl].uv = other[uvl].uv
                f.smooth = True
    done["holes_filled_faces"] = filled
    after = counts()
    bm.verts.ensure_lookup_table()
    tree = kdtree.KDTree(len(rest))
    for i, p in enumerate(rest):
        tree.insert(p, i)
    tree.balance()
    moved = max((tree.find(v.co)[2] for v in bm.verts), default=0.0)
    changed = any(done.values())
    if changed:
        bm.to_mesh(me)
        me.update()
    bm.free()
    return dict(before=before, after=after, ops=done, max_move_mm=round(moved * 1000, 4), changed=changed)


def bone_state(arm):
    return {b.name: (np.array(b.matrix_local), np.array(b.head_local), np.array(b.tail_local), b.parent.name if b.parent else None)
            for b in arm.data.bones}


def same_bones(a, b):
    return a.keys() == b.keys() and all(np.array_equal(a[k][0], b[k][0]) and np.array_equal(a[k][1], b[k][1]) and
                                         np.array_equal(a[k][2], b[k][2]) and a[k][3] == b[k][3] for k in a)


def add_bake_uv(obj):
    """A non-overlapping second UV layer "BakeUV" (Smart UV Project) for a prop's baked maps; UV 0 stays the render UV."""
    me = obj.data
    if not me.polygons:
        return False
    if "BakeUV" in me.uv_layers:
        return False
    render = me.uv_layers.active_index if me.uv_layers else None
    lay = me.uv_layers.new(name="BakeUV")
    me.uv_layers.active = lay
    for o in bpy.context.view_layer.objects:
        o.select_set(o is obj)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.004, area_weight=0.0, scale_to_bounds=False)
    bpy.ops.object.mode_set(mode="OBJECT")
    if render is not None:
        me.uv_layers.active_index = render
        for i, l in enumerate(me.uv_layers):
            l.active_render = i == render
    return True


# ------------------------------------------------------------------------------------------------------------- bakes
def uv_raster(obj, values, size, uv_name=None, gutter=8, background=0.5):
    """Per-vertex values rasterised into a UV layer (barycentric), gutter grown into empty texels. Row 0 = v 0."""
    me = obj.data
    me.calc_loop_triangles()
    lay = me.uv_layers[uv_name] if uv_name else me.uv_layers[0]
    uv = np.zeros(len(me.loops) * 2)
    lay.data.foreach_get("uv", uv)
    uv = uv.reshape(-1, 2)
    img = np.zeros((size, size))
    cov = np.zeros((size, size), bool)
    for t in me.loop_triangles:
        vals = values[list(t.vertices)]
        P = uv[list(t.loops)] * size - 0.5
        x0, y0 = np.floor(P.min(0)).astype(int)
        x1, y1 = np.ceil(P.max(0)).astype(int)
        x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, size - 1), min(y1, size - 1)
        if x1 < x0 or y1 < y0:
            continue
        (ax, ay), (bx, by), (cx, cy) = P
        den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(den) < 1e-12:
            continue
        xs, ys = np.meshgrid(np.arange(x0, x1 + 1), np.arange(y0, y1 + 1))
        l1 = ((by - cy) * (xs - cx) + (cx - bx) * (ys - cy)) / den
        l2 = ((cy - ay) * (xs - cx) + (ax - cx) * (ys - cy)) / den
        l3 = 1 - l1 - l2
        inside = (l1 >= -0.05) & (l2 >= -0.05) & (l3 >= -0.05)
        sub, sc = img[y0:y1 + 1, x0:x1 + 1], cov[y0:y1 + 1, x0:x1 + 1]
        v = l1 * vals[0] + l2 * vals[1] + l3 * vals[2]
        sub[inside] = v[inside]
        sc |= inside
    for _ in range(gutter):
        acc, n = np.zeros_like(img), np.zeros_like(img)
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            acc += np.roll(np.roll(img * cov, dy, 0), dx, 1)
            n += np.roll(np.roll(cov.astype(float), dy, 0), dx, 1)
        grow = (~cov) & (n > 0)
        img[grow] = acc[grow] / n[grow]
        cov = cov | grow
    img[~cov] = background
    return img


def curvature(obj):
    """Per-vertex convexity, 0.5 = flat: n . (mean of the neighbours - v) / mean edge length (> 0 = a pit), one
    smoothing pass, mapped as 0.5 - k (clipped), so creases are dark and ridges light."""
    me = obj.data
    co = np.array([v.co[:] for v in me.vertices])
    nrm = np.array([v.normal[:] for v in me.vertices])
    ev = np.array([e.vertices[:] for e in me.edges])
    n = len(co)
    acc, cnt, el = np.zeros((n, 3)), np.zeros(n), np.zeros(n)
    L = np.linalg.norm(co[ev[:, 0]] - co[ev[:, 1]], axis=1)
    for a, b in ((0, 1), (1, 0)):
        np.add.at(acc, ev[:, a], co[ev[:, b]])
        np.add.at(cnt, ev[:, a], 1)
        np.add.at(el, ev[:, a], L)
    cnt = np.maximum(cnt, 1)
    k = np.einsum("ij,ij->i", nrm, acc / cnt[:, None] - co) / np.maximum(el / cnt, 1e-9)
    sm, c2 = np.zeros(n), np.zeros(n)
    for a, b in ((0, 1), (1, 0)):
        np.add.at(sm, ev[:, a], k[ev[:, b]])
        np.add.at(c2, ev[:, a], 1)
    k = 0.5 * k + 0.5 * sm / np.maximum(c2, 1)
    s = np.percentile(np.abs(k), 98) or 1.0
    return np.clip(0.5 - 0.5 * k / s, 0, 1)


def setup_cycles(samples, distance):
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.samples = samples
    sc.render.bake.margin = 8
    if sc.world is None:
        sc.world = bpy.data.worlds.new("BakeWorld")
    sc.world.light_settings.distance = distance


def bake_image(obj, name, size, kind, uv_name=None, fill=(1, 1, 1, 1), **kw):
    """Cycles bake of obj into a new linear image (AO or NORMAL); an image node made active in each material."""
    img = bpy.data.images.new(name, size, size, alpha=False, float_buffer=False)
    img.colorspace_settings.name = "Non-Color"
    img.generated_color = fill
    me = obj.data
    if not me.materials:
        me.materials.append(bpy.data.materials.new(f"_bake_{obj.name}"))
    temp = []
    for i, m in enumerate(me.materials):
        if m is None:
            m = me.materials[i] = bpy.data.materials.new(f"_bake_{obj.name}_{i}")
        m.use_nodes = True
        node = m.node_tree.nodes.new("ShaderNodeTexImage")
        node.image = img
        m.node_tree.nodes.active = node
        temp.append((m, node))
    if uv_name:
        me.uv_layers.active = me.uv_layers[uv_name]
    for o in bpy.context.view_layer.objects:
        o.select_set(o is obj or o in kw.get("selected", ()))
    bpy.context.view_layer.objects.active = obj
    args = dict(type=kind, use_clear=False, margin=8)
    if kind == "NORMAL":
        args.update(normal_space="TANGENT", use_selected_to_active=True, cage_extrusion=kw["cage"], max_ray_distance=kw["reach"])
    bpy.ops.object.bake(**args)
    for m, node in temp:
        m.node_tree.nodes.remove(node)
    px = np.empty(size * size * 4, np.float32)
    img.pixels.foreach_get(px)
    return img, px.reshape(size, size, 4)


def write_png_rgb(path, rgb):
    rows = np.clip(np.round(rgb * 255), 0, 255).astype(np.uint8)
    raw = b"".join(b"\x00" + r.tobytes() for r in rows)

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    h, w = rows.shape[:2]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def thumb(arr, n=256):
    s = arr.shape[0] // n
    return arr[:s * n, :s * n].reshape(n, s, n, s, *arr.shape[2:]).mean((1, 3))


def similarity_icp(src, dst, iters=25):
    """Similarity (s, R, t) taking src points onto the dst points (nearest neighbours, Umeyama), and the RMS (m)."""
    tree = kdtree.KDTree(len(src))
    for i, p in enumerate(src):
        tree.insert(p, i)
    tree.balance()
    s, R, t = 1.0, np.eye(3), np.zeros(3)
    rms = None
    for _ in range(iters):
        inv = np.linalg.inv(R) / s
        q = (dst - t) @ inv.T                      # dst into src's frame, nearest src point for each
        idx = np.array([tree.find(p)[1] for p in q])
        A, B = src[idx], dst
        ma, mb = A.mean(0), B.mean(0)
        Ac, Bc = A - ma, B - mb
        U, D, Vt = np.linalg.svd(Bc.T @ Ac / len(A))
        S = np.diag([1, 1, np.sign(np.linalg.det(U @ Vt))])
        R = U @ S @ Vt
        s = float(np.trace(np.diag(D) @ S) / (Ac ** 2).sum(1).mean())
        t = mb - s * R @ ma
        rms = float(np.sqrt((((A @ R.T) * s + t - B) ** 2).sum(1).mean()))
    return s, R, t, rms


REGIONS = {"head": ("head", "neck"), "trunk": ("pelvis", "belly", "chest"), "thigh_l": ("thigh_l",), "thigh_r": ("thigh_r",),
           "shin_l": ("shin_l",), "shin_r": ("shin_r",), "foot_l": ("foot_l",), "foot_r": ("foot_r",)}
REGION_RMS = 0.010   # m: a region whose rigid fit is worse is left out
LOCAL_FIT = 0.008    # m: scan faces further than this from the rigged surface (after their region's fit) are left out
# arms are left out: the rig raised them from the scan's A-pose (and Grandpa's hands are remodelled)


def regional_cage(mesh, hi):
    """The high-res scan cut into body regions, each moved rigidly (similarity ICP) onto the rigged mesh's same region
    (the rig re-posed the scan: one transform leaves 5-25 mm), as one mesh for a selected-to-active bake. A region is
    kept when its RMS is <= 10 mm, and of it only the faces within 8 mm of the rigged surface. Returns (object or None, per-region stats)."""
    gi = {g.index: g.name for g in mesh.vertex_groups}
    g2r = {g: r for r, gs in REGIONS.items() for g in gs}
    mw = np.array(mesh.matrix_world)
    co = np.array([v.co[:] for v in mesh.data.vertices]) @ mw[:3, :3].T + mw[:3, 3]
    reg = np.array([g2r.get(gi[max(v.groups, key=lambda g: g.weight).group], "") if v.groups else "" for v in mesh.data.vertices])
    hw = np.array(hi.matrix_world)
    hv = np.array([v.co[:] for v in hi.data.vertices]) @ hw[:3, :3].T + hw[:3, 3]
    core = co[np.isin(reg, ("head", "trunk"))]
    best = None
    for Rz in AXIS_ROTATIONS:   # where the scan's up and front are; scaled to the height, soles and centres matched
        p = hv @ Rz.T
        s0 = (co[:, 2].max() - co[:, 2].min()) / (p[:, 2].max() - p[:, 2].min())
        p = p * s0
        p = p + np.array([*(co[:, :2].mean(0) - p[:, :2].mean(0)), co[:, 2].min() - p[:, 2].min()])
        s, R, t, rms = similarity_icp(p[:: max(1, len(p) // 4000)], core[:: max(1, len(core) // 1500)], iters=6)
        if best is None or rms < best[0]:
            best = (rms, (p * s) @ R.T + t)
    p = best[1]
    s, R, t, _ = similarity_icp(p[:: max(1, len(p) // 12000)], core, iters=25)
    p = (p * s) @ R.T + t
    tree = kdtree.KDTree(len(co))
    for i, x in enumerate(co):
        tree.insert(x, i)
    tree.balance()
    near = [tree.find(x) for x in p]
    surf = bvhtree.BVHTree.FromPolygons(co.tolist(), [pl.vertices[:] for pl in mesh.data.polygons])
    preg = np.array([reg[i] if d < 0.04 else "" for _, i, d in near])
    hi.data.calc_loop_triangles()
    faces = np.array([t.vertices[:] for t in hi.data.loop_triangles])
    verts, tris, stats = [], [], {}
    for r in REGIONS:
        src, dst = p[preg == r], co[reg == r]
        if len(src) < 50 or len(dst) < 20:
            continue
        s, R, t, rms = similarity_icp(src, dst, iters=25)
        stats[r] = dict(rms_mm=round(rms * 1000, 2), scale=round(s, 4), kept=rms <= REGION_RMS)
        if rms > REGION_RMS:
            continue
        q = (p * s) @ R.T + t
        ok = np.array([surf.find_nearest(Vector(x))[3] < LOCAL_FIT if (preg[i] == r) else False for i, x in enumerate(q)])
        f = faces[(preg[faces] == r).all(1) & ok[faces].all(1)]
        stats[r]["faces_kept"] = int(len(f))
        used = np.unique(f)
        remap = -np.ones(len(p), int)
        remap[used] = np.arange(len(used)) + sum(len(v) for v in verts)
        verts.append(q[used])
        tris.append(remap[f])
    if not verts:
        return None, stats
    me = bpy.data.meshes.new("_scan_regions")
    me.from_pydata(np.concatenate(verts).tolist(), [], np.concatenate(tris).tolist())
    for pl in me.polygons:
        pl.use_smooth = True
    obj = bpy.data.objects.new("_scan_regions", me)
    bpy.context.scene.collection.objects.link(obj)
    bpy.context.view_layer.update()
    return obj, stats


def hires_source(Name, faces_lo):
    """The raw scan with >= 1.5x the rigged mesh's faces (its world-space vertices and faces), or None."""
    names = {p.name.lower(): p for p in RAW.iterdir()}          # the file's own spelling (Windows matches any case)
    for cand in (f"{Name}_raw.blend", f"{Name}.glb"):
        path = names.get(cand.lower())
        if path is None:
            continue
        before = set(bpy.data.objects)
        if path.suffix == ".blend":
            with bpy.data.libraries.load(str(path)) as (src, dst):
                dst.objects = list(src.objects)
            for o in dst.objects:
                if o is not None:
                    bpy.context.scene.collection.objects.link(o)
        else:
            bpy.ops.import_scene.gltf(filepath=str(path))
        new = [o for o in bpy.data.objects if o not in before]
        meshes = [o for o in new if o.type == "MESH"]
        best = max(meshes, key=lambda o: len(o.data.polygons), default=None)
        if best is not None and len(best.data.polygons) >= 1.5 * faces_lo:
            for o in new:
                if o is not best:
                    bpy.data.objects.remove(o)
            best.modifiers.clear()
            best.parent = None
            return best, path
        for o in new:
            bpy.data.objects.remove(o)
    return None, None


# ------------------------------------------------------------------------------------------------------- per asset
def file_info(path):
    """Bytes, triangles and texture sizes of an exported file (GLB: from its JSON + image headers)."""
    out = dict(bytes=path.stat().st_size)
    if path.suffix == ".glb":
        b = path.read_bytes()
        n = struct.unpack("<I", b[12:16])[0]
        j = json.loads(b[20:20 + n])
        tris = 0
        for m in j.get("meshes", []):
            for p in m["primitives"]:
                if "indices" in p:
                    tris += j["accessors"][p["indices"]]["count"] // 3
                else:
                    tris += j["accessors"][p["attributes"]["POSITION"]]["count"] // 3
        out["tris"] = tris
        bin0 = 20 + n + 8
        texs = []
        for im in j.get("images", []):
            if "bufferView" not in im:
                continue
            bv = j["bufferViews"][im["bufferView"]]
            data = b[bin0 + bv.get("byteOffset", 0): bin0 + bv.get("byteOffset", 0) + 64]
            texs.append(list(image_size(data)) + [im.get("mimeType")])
        out["textures"] = texs
    return out


def image_size(h):
    if h[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", h[16:24])
    if h[:4] == b"RIFF" and h[8:12] == b"WEBP":
        if h[12:16] == b"VP8 ":
            w, hh = struct.unpack("<HH", h[26:30])
            return w & 0x3FFF, hh & 0x3FFF
        if h[12:16] == b"VP8L":
            bits = int.from_bytes(h[21:25], "little")
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        if h[12:16] == b"VP8X":
            return int.from_bytes(h[24:27], "little") + 1, int.from_bytes(h[27:30], "little") + 1
    if h[:2] == b"\xff\xd8":
        return (0, 0)
    return (0, 0)


def tris_of(objs):
    n = 0
    for o in objs:
        o.data.calc_loop_triangles()
        n += len(o.data.loop_triangles)
    return n


def mat_textures(objs):
    seen = {}
    for o in objs:
        for m in o.data.materials:
            if m and m.use_nodes:
                for nd in m.node_tree.nodes:
                    if nd.type == "TEX_IMAGE" and nd.image and nd.image.size[0]:
                        seen[nd.image.name] = list(nd.image.size)
    return seen


def run_athlete(name, steps, size, rep_dir):
    Name = name.capitalize()
    src = RAW / f"{Name}_rigged.blend"
    out = dict(kind="athlete", source=src.relative_to(ROOT).as_posix())
    bpy.ops.wm.open_mainfile(filepath=str(src))
    bpy.context.preferences.filepaths.save_version = 0
    arm, mesh = rig_and_mesh()
    bones0 = bone_state(arm)
    if "clean" in steps:
        c = clean_mesh(mesh, fill_holes=True)
        if c["max_move_mm"] > MOVE_TOL * 1000:
            raise RuntimeError(f"{name}: cleanup moved a rest vertex {c['max_move_mm']} mm (> 0.1): not saved")
        if not same_bones(bones0, bone_state(arm)):
            raise RuntimeError(f"{name}: the armature changed")
        out["clean"] = c
        if c["changed"]:
            bpy.ops.wm.save_mainfile(filepath=str(src))
        log(f"{name} clean {json.dumps(c)}")
    out["rig"] = dict(bones=len(bones0), unchanged=same_bones(bones0, bone_state(arm)))
    out["mesh"] = dict(verts=len(mesh.data.vertices), tris=tris_of([mesh]),
                       shape_keys=[k.name for k in mesh.data.shape_keys.key_blocks] if mesh.data.shape_keys else [],
                       textures=mat_textures([mesh]))
    if "export" in steps:
        had_glb = (EXP / f"{Name}_rigged.glb").exists()
        export_athlete(name, arm, mesh, web=True)
        ex = {}
        for p in (EXP / f"{Name}_rigged.fbx", EXP / f"{Name}_rigged.glb", WEB / f"{name}.glb"):
            if p.exists():
                ex[p.relative_to(ROOT).as_posix()] = file_info(p)
        ex[f"Content/Art/Exports/{Name}_rigged.fbx"]["tris"] = out["mesh"]["tris"]
        ex[f"Content/Art/Exports/{Name}_rigged.fbx"]["textures"] = out["mesh"]["textures"]
        if not had_glb:   # the full-size GLB is only the web file's source: Matt never kept one
            (EXP / f"{Name}_rigged.glb").unlink()
            ex.pop(f"Content/Art/Exports/{Name}_rigged.glb", None)
        out["exports"] = ex
        log(f"{name} export {json.dumps(ex)[:400]}")
    if "lods" in steps:
        r = subprocess.run([bpy.app.binary_path, "-b", "--factory-startup", "--python", str(ROOT / "Scripts/athlete_lods.py"),
                            "--", str(src), str(EXP), Name], capture_output=True, text=True)
        lines = [l for l in r.stdout.splitlines() if l.startswith("[athlete_lods] LOD")]
        if r.returncode or len(lines) != 2:
            raise RuntimeError(f"{name}: athlete_lods failed\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}")
        out["lods"] = {}
        for i in (1, 2):
            p = EXP / f"{Name}_LOD{i}.fbx"
            faces = int(lines[i - 1].split(": ", 1)[1].split(" faces")[0])
            out["lods"][p.relative_to(ROOT).as_posix()] = dict(bytes=p.stat().st_size, tris=faces,
                                                       ratio=round(faces / out["mesh"]["tris"], 3))
        log(f"{name} lods {out['lods']}")
    if "bake" in steps:
        out["bakes"] = bake_athlete(Name, arm, mesh, size, rep_dir)
    return out


def bake_athlete(Name, arm, mesh, size, rep_dir):
    tex = EXP / f"{Name}_tex"
    res = {}
    arm.data.pose_position = "REST"
    if mesh.data.shape_keys:
        for k in mesh.data.shape_keys.key_blocks:
            k.value = 0.0
    bpy.context.view_layer.update()
    t0 = time.time()
    setup_cycles(32, 0.3)
    _, px = bake_image(mesh, f"T_{Name}_AO", size, "AO")
    ao = px[..., 0]
    write_png_gray(tex / f"T_{Name}_AO.png", np.flipud(ao))
    res[f"T_{Name}_AO.png"] = dict(size=[size, size], seconds=round(time.time() - t0, 1), mean=round(float(ao.mean()), 3))
    t0 = time.time()
    cav = uv_raster(mesh, curvature(mesh), size)
    write_png_gray(tex / f"T_{Name}_Cavity.png", np.flipud(cav))
    res[f"T_{Name}_Cavity.png"] = dict(size=[size, size], seconds=round(time.time() - t0, 1))
    thumbs = {f"T_{Name}_AO": thumb(ao), f"T_{Name}_Cavity": thumb(cav)}
    hi, path = hires_source(Name, len(mesh.data.polygons))
    if hi is None:
        res["normal_scan"] = "skipped: no raw scan with >= 1.5x the rigged faces (the generator's normal map is the detail)"
    else:
        t0 = time.time()
        cage, stats = regional_cage(mesh, hi)
        res["normal_scan_alignment"] = dict(source=path.relative_to(ROOT).as_posix(), hires_faces=len(hi.data.polygons), regions=stats)
        if cage is None:
            res["normal_scan"] = "skipped: no body region of the scan aligns within 6 mm"
        else:
            setup_cycles(1, 0.3)
            _, px = bake_image(mesh, f"T_{Name}_NormalScan", size, "NORMAL", fill=(0.5, 0.5, 1.0, 1.0),
                               selected=(cage,), cage=0.012, reach=0.024)
            nrm = px[..., :3].copy()
            wild = nrm[..., 2] < 0.75            # tilted > 60 deg off the low-poly normal: a ray that hit the wrong surface
            nrm[wild] = (0.5, 0.5, 1.0)
            res["normal_scan_texels_flattened"] = round(float(wild.mean()), 4)
            write_png_rgb(tex / f"T_{Name}_NormalScan.png", np.flipud(nrm))
            flat = float((np.abs(nrm - np.array([0.5, 0.5, 1.0])).max(2) < 0.01).mean())
            res[f"T_{Name}_NormalScan.png"] = dict(size=[size, size], seconds=round(time.time() - t0, 1), flat_texels=round(flat, 3))
            thumbs[f"T_{Name}_NormalScan"] = thumb(nrm)
            bpy.data.objects.remove(cage)
        bpy.data.objects.remove(hi)
    for k, v in thumbs.items():
        if rep_dir:
            (write_png_gray if v.ndim == 2 else write_png_rgb)(rep_dir / f"{k}.png", np.flipud(v))
    for f in list(res):
        if f.endswith(".png"):
            res[f]["bytes"] = (tex / f).stat().st_size
    log(f"{Name} bakes {json.dumps(res)}")
    return res


def run_arena(key, steps, size, rep_dir):
    src = RAW / ARENAS[key]
    out = dict(kind="arena", source=src.relative_to(ROOT).as_posix())
    bpy.ops.wm.open_mainfile(filepath=str(src))
    bpy.context.preferences.filepaths.save_version = 0
    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    if "clean" in steps:
        stats, worst, uvs = {}, 0.0, 0
        totals = {k: [0, 0] for k in ("verts", "faces", "nonmanifold_edges", "boundary_edges", "loose_verts", "loose_edges")}
        for me in {o.data for o in meshes}:
            o = next(x for x in meshes if x.data is me)
            c = clean_mesh(o, fill_holes=False)
            worst = max(worst, c["max_move_mm"])
            for k in totals:
                totals[k][0] += c["before"][k]
                totals[k][1] += c["after"][k]
            if c["changed"]:
                stats[o.name] = c["ops"]
        if worst > MOVE_TOL * 1000:
            raise RuntimeError(f"{key}: cleanup moved a vertex {worst} mm (> 0.1): not saved")
        if key == "arena":
            for o in meshes:
                uvs += add_bake_uv(o)
        out["clean"] = dict(meshes=len(meshes), changed=stats, max_move_mm=worst, bake_uv_added=uvs, totals=totals)
        bpy.ops.wm.save_mainfile(filepath=str(src))
        log(f"{key} clean {json.dumps(out['clean'])[:600]}")
    out["mesh"] = dict(objects=len(meshes), tris=tris_of(meshes), textures=mat_textures(meshes))
    if key != "arena":
        out["exports"] = "none: the first arena is kept for reference; Arena_v2.blend is the live one"
        return out
    if "export" in steps:
        for o in bpy.context.view_layer.objects:
            o.select_set(False)
        glb, fbx = EXP / "Arena.glb", EXP / "Arena.fbx"
        bpy.ops.export_scene.gltf(filepath=str(glb), export_format="GLB", export_yup=True, export_apply=True,
                                  export_tangents=True)   # as the arena was first exported: bevels applied, MikkTSpace tangents
        bpy.ops.export_scene.fbx(filepath=str(fbx), add_leaf_bones=False, bake_anim=False, mesh_smooth_type="FACE",
                                 path_mode="COPY", embed_textures=True)
        ex = {glb.relative_to(ROOT).as_posix(): file_info(glb), fbx.relative_to(ROOT).as_posix(): dict(bytes=fbx.stat().st_size, tris=out["mesh"]["tris"],
                                                                                         textures=out["mesh"]["textures"])}
        if GLTF_TRANSFORM.exists():
            web = WEB / "arena.glb"
            subprocess.run([str(GLTF_TRANSFORM), "optimize", str(glb), str(web), "--compress", "meshopt",
                            "--texture-compress", "webp", "--texture-size", "2048", "--simplify", "false"],
                           check=True, capture_output=True)
            ex[web.relative_to(ROOT).as_posix()] = file_info(web)
        out["exports"] = ex
        log(f"{key} export {json.dumps(ex)[:600]}")
    if "bake" in steps:
        out["bakes"] = bake_arena(meshes, size, rep_dir)
    if "export" in steps:   # the phone arena (Scripts/arena_mobile.py), from a copy without BakeUV (phones skip the maps)
        for o in meshes:
            if "BakeUV" in o.data.uv_layers:
                o.data.uv_layers.remove(o.data.uv_layers["BakeUV"])
        tmp = RAW / "_Arena_v2_mobile.blend"   # arena_mobile.py finds the repo from the blend's own folder
        bpy.ops.wm.save_as_mainfile(filepath=str(tmp), copy=True)
        try:
            r = subprocess.run([bpy.app.binary_path, "-b", str(tmp), "--python", str(ROOT / "Scripts/arena_mobile.py")],
                               capture_output=True, text=True)
        finally:
            tmp.unlink(missing_ok=True)
        if r.returncode:
            raise RuntimeError(f"arena_mobile failed: {r.stdout[-2000:]}")
        mob = EXP / "Arena_Mobile.glb"
        out["exports"][mob.relative_to(ROOT).as_posix()] = file_info(mob)
        log(f"{key} mobile {out['exports'][mob.relative_to(ROOT).as_posix()]}")
    return out


def bake_arena(meshes, size, rep_dir):
    bake_dir = EXP / "Textures/Bake"
    res = {}
    setup_cycles(16, 1.0)
    for o in sorted(meshes, key=lambda o: o.name):
        me = o.data
        if not me.polygons or "BakeUV" not in me.uv_layers:
            continue
        area = sum(p.area for p in me.polygons) * float(np.prod(o.matrix_world.to_scale()) ** (2 / 3))
        px = int(np.clip(2 ** round(math.log2(max(math.sqrt(area) * 96, 1))), 64, min(1024, size)))
        t0 = time.time()
        render = me.uv_layers.active_index
        _, img = bake_image(o, f"T_{o.name}_AO", px, "AO", uv_name="BakeUV")
        me.uv_layers.active_index = render
        ao = img[..., 0]
        name = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in o.name)
        write_png_gray(bake_dir / f"T_{name}_AO.png", np.flipud(ao))
        cav = uv_raster(o, curvature(o), px, uv_name="BakeUV")
        write_png_gray(bake_dir / f"T_{name}_Cavity.png", np.flipud(cav))
        res[name] = dict(size=[px, px], seconds=round(time.time() - t0, 1), ao_mean=round(float(ao.mean()), 3),
                         bytes=(bake_dir / f"T_{name}_AO.png").stat().st_size + (bake_dir / f"T_{name}_Cavity.png").stat().st_size)
        if rep_dir and px >= 256:
            write_png_gray(rep_dir / f"T_{name}_AO.png", np.flipud(thumb(ao, 128)))
        log(f"arena bake {name} {res[name]}")
    return res


def report_html(rep_dir, manifest):
    rows = []
    for k, a in manifest["assets"].items():
        if "error" in a:
            rows.append(f"<tr><td>{k}</td><td colspan=6>ERROR {a['error'][:300]}</td></tr>")
            continue
        c = a.get("clean", {})
        ops = c.get("ops") or c.get("changed") or {}
        ex = a.get("exports", {})
        exs = "<br>".join(f"{p}: {v['bytes']:,} B, {v.get('tris', '?'):,} tris" for p, v in ex.items()) if isinstance(ex, dict) else ex
        lods = "<br>".join(f"{p}: {v['tris']:,} tris ({v['ratio']})" for p, v in a.get("lods", {}).items())
        bakes = "<br>".join(f"{p}: {v['size'][0]}&sup2;" if isinstance(v, dict) and "size" in v else f"{p}: {v}" for p, v in a.get("bakes", {}).items()
                            if not isinstance(v, dict) or "size" in v or True)[:3000]
        rows.append(f"<tr><td>{k}</td><td>{a['source']}</td><td><pre>{json.dumps(ops, indent=0)[:800]}</pre>max move {c.get('max_move_mm')} mm</td>"
                    f"<td>{a.get('mesh', {}).get('tris', '')}</td><td>{lods}</td><td>{exs}</td><td>{bakes}</td></tr>")
    imgs = "".join(f"<figure><img src='{p.name}' width=256><figcaption>{p.stem}</figcaption></figure>" for p in sorted(rep_dir.glob("T_*.png")))
    (rep_dir / "batch.html").write_text(f"""<!doctype html><meta charset="utf-8"><title>Asset batch {manifest['generated']}</title>
<style>body{{font:13px system-ui;background:#111;color:#ddd;padding:20px}}table{{border-collapse:collapse}}td,th{{border:1px solid #444;padding:3px 6px;vertical-align:top}}
pre{{margin:0;font-size:11px}}figure{{display:inline-block;margin:4px}}img{{border:1px solid #333}}</style>
<h1>Asset batch (Scripts/blender_batch.py), {manifest['generated']}</h1><p>Blender {manifest['blender']}, {manifest['seconds']} s. Manifest: <code>Content/Art/Exports/asset_manifest.json</code>.</p>
<table><tr><th>asset</th><th>source</th><th>cleanup</th><th>tris</th><th>LODs</th><th>exports</th><th>bakes</th></tr>{''.join(rows)}</table>
<h2>Baked maps (thumbnails, linear)</h2>{imgs}""", encoding="utf-8")


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    opts = {a: argv[i + 1] for i, a in enumerate(argv) if a.startswith("--") and i + 1 < len(argv)}
    skip = set(opts.get("--skip", "").split(",")) - {""}
    steps = {"clean", "lods", "bake", "export"} - skip
    size = int(opts.get("--size", 2048))
    rep_dir = Path(opts["--report"]) if "--report" in opts else None
    if rep_dir:
        rep_dir.mkdir(parents=True, exist_ok=True)
    flags = {v for k, v in opts.items()}
    names = [a for a in argv if not a.startswith("--") and a not in flags] or [*ATHLETES, *ARENAS]
    t0 = time.time()
    mpath = EXP / "asset_manifest.json"
    manifest = json.loads(mpath.read_text()) if mpath.exists() else {"assets": {}}
    manifest.update(generated=datetime.datetime.now().isoformat(timespec="seconds"), blender=bpy.app.version_string,
                    command="blender -b --factory-startup --python Scripts/blender_batch.py -- " + " ".join(argv), steps=sorted(steps))
    failed = []
    for n in names:
        log(f"=== {n}")
        try:
            res = run_athlete(n, steps, size, rep_dir) if n in ATHLETES else run_arena(n, steps, size, rep_dir)
            entry = manifest["assets"].setdefault(n, {})
            entry.pop("error", None)
            entry.update(res)          # a partial run (--skip) keeps the other steps' last results
        except Exception as e:   # one bad asset must not stop the others; the manifest says what failed
            import traceback
            traceback.print_exc()
            manifest["assets"].setdefault(n, {})["error"] = f"{type(e).__name__}: {e}"
            failed.append(n)
    manifest["seconds"] = round(time.time() - t0)
    manifest.setdefault("runs", []).append(dict(at=manifest["generated"], command=manifest["command"], seconds=manifest["seconds"],
                                                failed=failed))
    mpath.write_text(json.dumps(manifest, indent=1))
    if rep_dir:
        report_html(rep_dir, manifest)
    log(f"manifest {mpath} ({manifest['seconds']} s); failed: {failed or 'none'}")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
