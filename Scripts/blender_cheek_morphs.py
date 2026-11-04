r"""Deformable cheek: CheekDent_L / CheekDent_R shape keys (morph targets) and a cheek mask, on every rigged athlete.

    "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" -b --factory-startup --python Scripts/blender_cheek_morphs.py -- [grandpa kim matt] [--no-save] [--no-web]

Per athlete (Content/Art/Raw/<Name>_rigged.blend):
  - shape keys Basis, CheekDent_L, CheekDent_R: a 1.8 cm inward dent on the cheek/jaw (BLUEPRINT 2.1's face zone): centred
    on the widest point of the face 3.5 cm below the nose tip and within 7.5 cm behind it, 4.5 cm radius, smooth falloff
    (1 - (d/R)^2)^2. L/R = the CHARACTER's left/right (left = +X in Blender, the athletes face -Y). The dent goes in along
    the cheek's own surface normal averaged over 2 cm (leaned toward the skull), a function of position only, so
    UV-seam splits move together and nothing tears. Only head/neck-weighted vertices move;
  - a point attribute _CHEEKMASK (0..1, both cheeks, 3 cm sigma Gaussian): exported to glTF as the custom vertex
    attribute _CHEEKMASK (three.js: geometry.attributes._cheekmask), so the browser needs no extra texture;
  - Content/Art/Exports/<Name>_tex/T_<Name>_CheekMask.png: the same mask baked into UV channel 0 (the scan's only UV,
    the one the BaseColor uses), 1024 x 1024, 8-bit grayscale PNG, LINEAR data (import with sRGB off, sample .r),
    8 px gutter. Material: Flush = CheekMask.r * CheekFlush (0..1); lerp the base colour toward a flush red by it.
Then re-exports, with the rig, bone names, rest frames and vertex positions untouched (shape keys only ADD data):
  Content/Art/Exports/<Name>_rigged.fbx (rig_mixamo.py's settings; use_mesh_modifiers off so the blend shapes survive:
    the only modifier is the armature, which an FBX skin never applies anyway),
  Content/Art/Exports/<Name>_rigged.glb (Blender glTF: morph targets + morph normals + _CHEEKMASK),
  Web/assets/<name>.glb (gltf-transform optimize: meshopt, WebP textures <= 2048, no simplify, attributes kept).
The dent is a pure function of the mesh, so Scripts/athlete_lods.py re-runs add_cheek_morphs() on each LOD copy.
"""
import json
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import bpy
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
RAW, EXP, WEB = ROOT / "Content/Art/Raw", ROOT / "Content/Art/Exports", ROOT / "Web/assets"
DEPTH = 0.018          # m, the dent at 1.0
RADIUS = 0.045         # m, the dent's footprint (a palm is ~9 cm wide)
MASK_SIGMA = 0.03      # m, the flush's Gaussian
MASK_SIZE = 1024
KEYS = ("CheekDent_L", "CheekDent_R")


def rig_and_mesh():
    arm = next(o for o in bpy.data.objects if o.type == "ARMATURE")
    meshes = [o for o in bpy.data.objects if o.type == "MESH" and any(m.type == "ARMATURE" for m in o.modifiers)]
    return arm, max(meshes, key=lambda o: len(o.data.vertices))


def group_weights(obj, name):
    w = np.zeros(len(obj.data.vertices))
    if name not in obj.vertex_groups:
        return w
    gi = obj.vertex_groups[name].index
    for v in obj.data.vertices:
        for g in v.groups:
            if g.group == gi:
                w[v.index] = g.weight
    return w


def cheek_frame(obj):
    """World-space vertices, head weights, the head centre and each cheek's point + inward direction."""
    me = obj.data
    mw = np.array(obj.matrix_world)
    co = np.array([v.co[:] for v in me.vertices])
    W = co @ mw[:3, :3].T + mw[:3, 3]
    nrm = np.array([v.normal[:] for v in me.vertices]) @ mw[:3, :3].T
    nrm /= np.linalg.norm(nrm, axis=1, keepdims=True) + 1e-12
    hw = group_weights(obj, "head")
    head = W[hw > 0.5]
    c = (head.min(0) + head.max(0)) / 2
    cand = np.where(hw > 0.5)[0]
    H = W[cand]
    z0, z1 = H[:, 2].min(), H[:, 2].max()
    face = cand[(H[:, 2] > z0 + 0.15 * (z1 - z0)) & (H[:, 2] < z0 + 0.55 * (z1 - z0))]
    nose = W[face[np.argmin(W[face, 1])]]                     # the most forward point of the face (athletes face -Y)
    band = cand[(np.abs(W[cand, 2] - (nose[2] - 0.035)) < 0.015) & (W[cand, 1] < nose[1] + 0.075)]
    cheeks = {}
    for side, key in ((1, "CheekDent_L"), (-1, "CheekDent_R")):
        p = W[band[np.argmax(side * (W[band, 0] - nose[0]))]]  # the widest point of the face below the cheekbone: the cheek
        near = (np.linalg.norm(W - p, axis=1) < 0.02) & (hw > 0.3)
        n = nrm[near].mean(0)
        n /= np.linalg.norm(n)
        to_skull = (c - p) * np.array([1, 1, 0])
        inward = -n * np.array([1, 1, 0.3]) + 0.5 * to_skull / np.linalg.norm(to_skull)   # a slap pushes sideways:
        # the surface normal, flattened toward horizontal and leaned a little toward the skull
        cheeks[key] = (p, inward / np.linalg.norm(inward))
    return W, hw, c, cheeks, mw


def add_cheek_morphs(obj):
    """Basis + CheekDent_L/R shape keys and the _CHEEKMASK attribute on obj (replaces earlier ones). Returns stats, mask."""
    W, hw, c, cheeks, mw = cheek_frame(obj)
    me = obj.data
    allowed = (hw + group_weights(obj, "neck")) > 0.3         # head and jaw only: never a shoulder or a hand
    if me.shape_keys is None:
        obj.shape_key_add(name="Basis", from_mix=False)
    basis = np.array([v.co[:] for v in me.shape_keys.key_blocks["Basis"].data])
    inv = np.linalg.inv(mw[:3, :3])
    stats = {"head_centre": c.round(4).tolist()}
    mask = np.zeros(len(W))
    for key, (p, inward) in cheeks.items():
        d = np.linalg.norm(W - p, axis=1)
        f = np.where((d < RADIUS) & allowed, (1 - (d / RADIUS) ** 2) ** 2, 0.0)
        disp = (DEPTH * f[:, None] * inward[None, :]) @ inv.T
        kb = me.shape_keys.key_blocks.get(key) or obj.shape_key_add(name=key, from_mix=False)
        kb.data.foreach_set("co", (basis + disp).astype(np.float32).ravel())
        kb.value, kb.slider_min, kb.slider_max = 0.0, 0.0, 1.0
        mask = np.maximum(mask, np.exp(-0.5 * (d / MASK_SIGMA) ** 2) * allowed)
        stats[key] = {"point": p.round(4).tolist(), "inward": inward.round(3).tolist(), "verts": int((f > 0).sum()),
                      "max_mm": round(float(np.linalg.norm(disp, axis=1).max() * 1000), 2)}
    mask[mask < 0.02] = 0
    attr = me.attributes.get("_CHEEKMASK") or me.attributes.new("_CHEEKMASK", "FLOAT", "POINT")
    attr.data.foreach_set("value", mask.astype(np.float32))
    stats["mask_verts"] = int((mask > 0).sum())
    return stats, mask


def write_png_gray(path, img):
    """8-bit grayscale PNG (colour type 0), written directly: no colour management touches the data."""
    rows = np.clip(np.round(img * 255), 0, 255).astype(np.uint8)
    raw = b"".join(b"\x00" + r.tobytes() for r in rows)

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    h, w = rows.shape
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def bake_mask(obj, mask, path, size=MASK_SIZE):
    """The per-vertex mask rasterised into UV channel 0, with an 8 px gutter grown only into empty UV space (never
    over another island), so mips and bilinear filtering never bleed. Returns the painted fraction of the texture."""
    me = obj.data
    me.calc_loop_triangles()
    uv = np.zeros(len(me.loops) * 2)
    me.uv_layers[0].data.foreach_get("uv", uv)
    uv = uv.reshape(-1, 2)
    img = np.zeros((size, size))                               # row 0 = v 0 (bottom); flipped when written
    cov = np.zeros((size, size), bool)
    for t in me.loop_triangles:
        vals = mask[list(t.vertices)]
        P = uv[list(t.loops)] * size - 0.5                     # pixel centres
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
        cov[y0:y1 + 1, x0:x1 + 1] |= inside
        if vals.max() > 0:
            v = l1 * vals[0] + l2 * vals[1] + l3 * vals[2]
            sub = img[y0:y1 + 1, x0:x1 + 1]
            sub[inside] = np.maximum(sub[inside], v[inside])
    for _ in range(8):                                           # gutter
        grow = np.zeros_like(img)
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            grow = np.maximum(grow, np.roll(np.roll(img, dy, 0), dx, 1))
        img = np.where(cov, img, np.maximum(img, grow))
    img = np.clip(img, 0, 1)
    write_png_gray(path, np.flipud(img))
    return round(float((img > 0.02).mean()), 4)


FBX = dict(use_selection=True, add_leaf_bones=False, bake_anim=False, mesh_smooth_type="FACE",
           use_mesh_modifiers=False, path_mode="AUTO", embed_textures=False)


def select(*objs):
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]


def export(name, arm, mesh, web=True):
    Name = name.capitalize()
    for k in mesh.data.shape_keys.key_blocks:
        k.value = 0.0
    select(arm, mesh)
    fbx, glb = EXP / f"{Name}_rigged.fbx", EXP / f"{Name}_rigged.glb"
    packed = any(im.packed_file for im in bpy.data.images if im.users)   # Kim, Matt: textures live only in the blend,
    bpy.ops.export_scene.fbx(filepath=str(fbx), **{**FBX, **({"path_mode": "COPY", "embed_textures": True} if packed else {})})
    # so their FBX keeps embedding them, as rig_mixamo.py did; Grandpa's sit in Grandpa_tex/
    bpy.ops.export_scene.gltf(filepath=str(glb), export_format="GLB", use_selection=True, export_yup=True,
                              export_apply=False, export_skins=True, export_morph=True, export_morph_normal=True,
                              export_attributes=True, export_animations=False)
    out = {"fbx_MB": round(fbx.stat().st_size / 1e6, 2), "glb_MB": round(glb.stat().st_size / 1e6, 2)}
    tool = ROOT / "Web/node_modules/.bin/gltf-transform.cmd"
    if web and tool.exists():
        dst = WEB / f"{name}.glb"
        subprocess.run([str(tool), "optimize", str(glb), str(dst), "--compress", "meshopt", "--texture-compress", "webp",
                        "--texture-size", "2048", "--simplify", "false", "--prune-attributes", "false"], check=True)
        out["web_MB"] = round(dst.stat().st_size / 1e6, 3)
    return out


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    names = [a for a in argv if not a.startswith("--")] or ["grandpa", "kim", "matt"]
    report = {}
    for name in names:
        Name = name.capitalize()
        src = RAW / f"{Name}_rigged.blend"
        if not src.exists():
            print(f"[cheek] {name}: no {src.name}, skipped")
            continue
        bpy.ops.wm.open_mainfile(filepath=str(src))
        bpy.context.preferences.filepaths.save_version = 0       # no .blend1 next to the source
        arm, mesh = rig_and_mesh()
        stats, mask = add_cheek_morphs(mesh)
        stats["mask_cover"] = bake_mask(mesh, mask, EXP / f"{Name}_tex" / f"T_{Name}_CheekMask.png")
        if "--no-save" not in argv:
            bpy.ops.wm.save_mainfile(filepath=str(src))
        stats["exports"] = export(name, arm, mesh, web="--no-web" not in argv)
        report[name] = stats
        print(f"[cheek] {name}: {json.dumps(stats)}", flush=True)
    print("[cheek] REPORT " + json.dumps(report))


if __name__ == "__main__":
    main()
