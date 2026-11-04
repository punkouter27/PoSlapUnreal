r"""The arena for phones: the flat-colour materials merged into one palette atlas, and a lighter crowd.

    "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" -b Content/Art/Raw/Arena_v2.blend --python Scripts/arena_mobile.py

Arena_v2.blend has 19 flat-colour materials (no texture: seats, truss, tables, banners, lenses, ...) beside the 6 textured
ones (podium, safety mat, stage deck, tape, LED ring, crowd palette). On a phone every material slot of a mesh is its own
draw call (the judges' table alone has 5), so this writes a mobile copy where every flat material is one 16 px cell of a
palette atlas and the faces' UVs point at their cell's centre:
  Content/Art/Exports/Textures/T_ArenaPalette_D.png    base colour, sRGB, 128 x 128 (8 x 8 cells of 16 px)
  Content/Art/Exports/Textures/T_ArenaPalette_ORM.png  R occlusion (1), G roughness, B metallic, LINEAR (sRGB off)
  Content/Art/Exports/Textures/T_ArenaPalette_E.png    emissive colour x strength / EMISSIVE_MAX, sRGB
  Content/Art/Exports/Arena_Mobile.glb                 the arena with M_ArenaPalette (emissive strength EMISSIVE_MAX) in
                                                       place of the flat materials; the crowd decimated to CROWD_RATIO
  Content/Art/Exports/Arena_Mobile.json                the palette (material -> cell, values) and the per-mesh savings
The textured materials keep their own textures (all <= 2048 already: safety mat 2048, podium/deck 1024, LED 2048 x 256,
tape 512 x 64, crowd 128): their shaders differ (normal map, emissive LED), so atlasing them would not merge a draw call.
Nothing is saved to the .blend; the desktop arena (Exports/Arena.glb) is untouched. Cells are sampled at their centre, so
mips down to 16 x 16 (cell = 1 texel) keep the exact colours: set the textures' filter to nearest or clamp mips at 3.
"""
import json
import struct
import zlib
from pathlib import Path

import bpy
import numpy as np

ROOT = Path(bpy.data.filepath).resolve().parents[3]
TEX, EXP = ROOT / "Content/Art/Exports/Textures", ROOT / "Content/Art/Exports"
CELL, GRID = 16, 8
EMISSIVE_MAX = 40.0          # the brightest flat emitter (the spot lenses)
CROWD_RATIO = 0.3            # the crowd's 100 k faces -> 30 k on a phone


def png(path, img):
    """8-bit RGB PNG, written directly (no colour management touches the values)."""
    rows = np.clip(np.round(img * 255), 0, 255).astype(np.uint8)
    h, w, _ = rows.shape
    raw = b"".join(b"\x00" + r.tobytes() for r in rows)

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def to_srgb(c):
    c = np.clip(c, 0, 1)
    return np.where(c <= 0.0031308, 12.92 * c, 1.055 * c ** (1 / 2.4) - 0.055)


def flat(mat):
    """The Principled values of a material with no texture, or None (textured / not a plain Principled)."""
    if not mat or not mat.node_tree or any(n.type == "TEX_IMAGE" for n in mat.node_tree.nodes):
        return None
    b = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if b is None or any(i.is_linked for i in b.inputs if i.name in ("Base Color", "Roughness", "Metallic", "Emission Color")):
        return None
    v = lambda k: b.inputs[k].default_value
    return {"base": list(v("Base Color"))[:3], "rough": float(v("Roughness")), "metal": float(v("Metallic")),
            "emit": [x * float(v("Emission Strength")) for x in list(v("Emission Color"))[:3]]}


meshes = [o for o in bpy.data.objects if o.type == "MESH"]
used = sorted({m.name for o in meshes for m in o.data.materials if m})
palette = {n: flat(bpy.data.materials[n]) for n in used}
palette = {n: p for n, p in palette.items() if p}
assert len(palette) <= GRID * GRID, len(palette)
S = CELL * GRID
D, ORM, E = np.zeros((S, S, 3)), np.ones((S, S, 3)), np.zeros((S, S, 3))
cells = {}
for i, (name, p) in enumerate(palette.items()):
    cx, cy = i % GRID, i // GRID
    rows, cols = slice(cy * CELL, (cy + 1) * CELL), slice(cx * CELL, (cx + 1) * CELL)   # image row 0 = top = v 1
    D[rows, cols] = to_srgb(np.array(p["base"]))
    ORM[rows, cols] = [1.0, p["rough"], p["metal"]]
    E[rows, cols] = to_srgb(np.array(p["emit"]) / EMISSIVE_MAX)
    cells[name] = {"cell": [cx, cy], "uv": [(cx + 0.5) / GRID, 1 - (cy + 0.5) / GRID], **{k: np.round(p[k], 4).tolist() for k in p}}
TEX.mkdir(parents=True, exist_ok=True)
for suffix, img in (("D", D), ("ORM", ORM), ("E", E)):
    png(TEX / f"T_ArenaPalette_{suffix}.png", img)

# the palette material: base colour, ORM (G roughness, B metallic: glTF's metallicRoughness layout), emissive
mat = bpy.data.materials.new("M_ArenaPalette")
mat.use_nodes = True
nt = mat.node_tree
bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")


def tex_node(suffix, colorspace, y):
    n = nt.nodes.new("ShaderNodeTexImage")
    n.image = bpy.data.images.load(str(TEX / f"T_ArenaPalette_{suffix}.png"))
    n.image.colorspace_settings.name = colorspace
    n.interpolation = "Closest"
    n.location = (-600, y)
    return n


d, orm, e = tex_node("D", "sRGB", 300), tex_node("ORM", "Non-Color", 0), tex_node("E", "sRGB", -300)
sep = nt.nodes.new("ShaderNodeSeparateColor")
nt.links.new(d.outputs["Color"], bsdf.inputs["Base Color"])
nt.links.new(orm.outputs["Color"], sep.inputs["Color"])
nt.links.new(sep.outputs["Green"], bsdf.inputs["Roughness"])
nt.links.new(sep.outputs["Blue"], bsdf.inputs["Metallic"])
nt.links.new(e.outputs["Color"], bsdf.inputs["Emission Color"])
bsdf.inputs["Emission Strength"].default_value = EMISSIVE_MAX

report = {"palette": cells, "meshes": {}}
for o in meshes:
    me = o.data
    if me.users > 1:
        o.data = me = me.copy()
    old = [m.name if m else None for m in me.materials]
    if not any(n in palette for n in old):
        report["meshes"][o.name] = {"slots": len(old), "faces": len(me.polygons)}
        continue
    uv = me.uv_layers.active or me.uv_layers.new(name="UVMap")
    idx = np.zeros(len(me.polygons), int)
    me.polygons.foreach_get("material_index", idx)
    new = []
    for n in old:
        m = "M_ArenaPalette" if n in palette else n
        if m not in new:
            new.append(m)
    remap = np.array([new.index("M_ArenaPalette" if n in palette else n) for n in old])
    loop_uv = np.zeros(len(me.loops) * 2)
    uv.data.foreach_get("uv", loop_uv)
    loop_uv = loop_uv.reshape(-1, 2)
    for poly in me.polygons:
        n = old[poly.material_index] if poly.material_index < len(old) else None
        if n in palette:
            loop_uv[poly.loop_start:poly.loop_start + poly.loop_total] = cells[n]["uv"]
    uv.data.foreach_set("uv", loop_uv.ravel())
    me.materials.clear()
    for m in new:
        me.materials.append(bpy.data.materials[m])
    me.polygons.foreach_set("material_index", remap[np.clip(idx, 0, len(old) - 1)])
    me.update()
    report["meshes"][o.name] = {"slots": len(old), "slots_mobile": len(new), "faces": len(me.polygons)}

crowd = bpy.data.objects.get("Crowd")
if crowd:
    dec = crowd.modifiers.new("MobileDecimate", "DECIMATE")
    dec.decimate_type, dec.ratio = "COLLAPSE", CROWD_RATIO
    report["crowd_ratio"] = CROWD_RATIO

for x in bpy.context.view_layer.objects:
    x.select_set(x.type == "MESH")
out = EXP / "Arena_Mobile.glb"
bpy.ops.export_scene.gltf(filepath=str(out), export_format="GLB", use_selection=True, export_apply=True, export_yup=True,
                          export_lights=False, export_cameras=False, export_animations=False)
dg = bpy.context.evaluated_depsgraph_get()
if crowd:
    report["meshes"]["Crowd"]["faces_mobile"] = len(crowd.evaluated_get(dg).to_mesh().polygons)
report["slots_total"] = sum(m["slots"] for m in report["meshes"].values())
report["slots_total_mobile"] = sum(m.get("slots_mobile", m["slots"]) for m in report["meshes"].values())
report["glb_MB"] = round(out.stat().st_size / 1e6, 2)
(EXP / "Arena_Mobile.json").write_text(json.dumps(report, indent=1))
print(f"[arena_mobile] {len(palette)} flat materials -> 1 palette; slots {report['slots_total']} -> "
      f"{report['slots_total_mobile']}; crowd {report['meshes'].get('Crowd')}; {out} {report['glb_MB']} MB")
