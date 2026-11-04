r"""Before/after stills of each athlete's head with CheekDent_L / CheekDent_R at 1.0 (Scripts/blender_cheek_morphs.py).

    "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" -b --factory-startup --python Scripts/render_cheek_morphs.py -- <out_dir> [grandpa kim matt]

Workbench, headless, 3/4 front view of each cheek: textured and clay (clay shows the shape the texture hides).
Writes <out_dir>/<name>_<L|R>_<tex|clay>_<before|after>.png and <out_dir>/renders.json. Nothing is saved to the .blend.
"""
import json
import math
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
ARGV = sys.argv[sys.argv.index("--") + 1:]
OUT = Path(ARGV[0])
NAMES = ARGV[1:] or ["grandpa", "kim", "matt"]
OUT.mkdir(parents=True, exist_ok=True)
done = []

for name in NAMES:
    src = ROOT / "Content/Art/Raw" / f"{name.capitalize()}_rigged.blend"
    if not src.exists():
        continue
    bpy.ops.wm.open_mainfile(filepath=str(src))
    mesh = max((o for o in bpy.data.objects if o.type == "MESH"), key=lambda o: len(o.data.vertices))
    keys = mesh.data.shape_keys.key_blocks
    gi = mesh.vertex_groups["head"].index
    head = np.array([mesh.matrix_world @ v.co for v in mesh.data.vertices
                     if any(g.group == gi and g.weight > 0.5 for g in v.groups)])
    c = Vector(((head.min(0) + head.max(0)) / 2).tolist())
    size = float((head.max(0) - head.min(0)).max())
    scene = bpy.context.scene
    for o in [o for o in bpy.data.objects if o.type in ("CAMERA", "LIGHT")]:
        bpy.data.objects.remove(o)
    cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam"))
    scene.collection.objects.link(cam)
    cam.data.lens = 85
    scene.camera = cam
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.resolution_x = scene.render.resolution_y = 640
    scene.render.film_transparent = False
    sh = scene.display.shading
    sh.light = "STUDIO"
    sh.show_cavity = True
    sh.cavity_type = "BOTH"
    sh.show_specular_highlight = True
    scene.view_settings.view_transform = "Standard"
    for side in ("L", "R"):
        s = 1 if side == "L" else -1                              # character left = +X, faces -Y
        kb, basis = keys[f"CheekDent_{side}"], keys["Basis"]
        moved = [mesh.matrix_world @ basis.data[i].co for i in range(len(basis.data))
                 if (kb.data[i].co - basis.data[i].co).length > 0.004]
        tgt = sum(moved, Vector()) / len(moved)                   # the dent's centre
        dirv = Vector((1.0 * s, -0.75, 0.1)).normalized()
        cam.location = tgt + dirv * size * 3.0
        cam.rotation_euler = (tgt - cam.location).to_track_quat("-Z", "Y").to_euler()
        for look in ("tex", "clay"):
            sh.color_type = "TEXTURE" if look == "tex" else "SINGLE"
            sh.single_color = (0.8, 0.78, 0.75)
            for state in ("before", "after"):
                for k in keys:
                    k.value = 0.0
                if state == "after":
                    keys[f"CheekDent_{side}"].value = 1.0
                path = OUT / f"{name}_{side}_{look}_{state}.png"
                scene.render.filepath = str(path)
                bpy.ops.render.render(write_still=True)
                done.append(path.name)
                print("[render]", path, flush=True)
(OUT / "renders.json").write_text(json.dumps(done, indent=1))
