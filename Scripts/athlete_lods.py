r"""The athlete's LOD meshes, made headlessly: the rigged mesh decimated to half and a quarter of its faces.

    "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" -b --factory-startup --python Scripts/athlete_lods.py -- <in.blend> <out_dir> <name>

The rig is untouched: every LOD copy keeps the armature modifier and the vertex groups, so it poses exactly like the
original. Unreal's engine-reduced LODs (Scripts/broadcast_fx.py's add_lods) are what the game ships today; these files
are the authored ones, imported as LOD 1 and LOD 2 (broadcast_fx.py's import_lods), for the wide and crane shots on a
phone. Any subdivision is switched off on the copies (a LOD is the base mesh), the decimation collapses the faces and
keeps the border, so the hands and the face stay whole.
"""
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
ARGV = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
if len(ARGV) < 2:
    raise SystemExit("athlete_lods.py: <in.blend> <out_dir> [name]")
SRC = Path(ARGV[0])
OUT = Path(ARGV[1])
NAME = ARGV[2] if len(ARGV) > 2 else SRC.stem
LODS = ((1, 0.5), (2, 0.25))		# the LOD index and the face ratio to keep


def faces(obj):
    return sum(len(p.vertices) - 2 for p in obj.data.polygons)


bpy.ops.wm.open_mainfile(filepath=str(SRC))
meshes = [o for o in bpy.data.objects if o.type == "MESH"]
if not meshes:
    raise SystemExit(f"athlete_lods.py: no mesh in {SRC}")
src = max(meshes, key=faces)
armatures = [o for o in bpy.data.objects if o.type == "ARMATURE"]
print(f"[athlete_lods] {SRC.name}: mesh '{src.name}' with {len(src.data.polygons)} faces, "
      f"{len(armatures)} armature(s), modifiers {[m.type for m in src.modifiers]}")

OUT.mkdir(parents=True, exist_ok=True)
for index, ratio in LODS:
    bpy.ops.object.select_all(action="DESELECT")
    src.select_set(True)
    bpy.context.view_layer.objects.active = src
    bpy.ops.object.duplicate()			# the copy keeps the rig and the vertex groups
    copy = bpy.context.view_layer.objects.active
    copy.name = f"{NAME}_LOD{index}"
    # a LOD is the base mesh: any subdivision is off on the copy
    for mod in copy.modifiers:
        if mod.type == "SUBSURF":
            mod.levels = 0
            mod.render_levels = 0
    dec = copy.modifiers.new(f"Decimate{index}", "DECIMATE")
    dec.decimate_type = "COLLAPSE"		# keeps the border: the hands and the face stay whole
    dec.ratio = ratio
    bpy.ops.object.modifier_move_to_index(modifier=dec.name, index=0)
    bpy.ops.object.modifier_apply(modifier=dec.name)
    path = OUT / f"{copy.name}.fbx"
    bpy.ops.object.select_all(action="DESELECT")
    copy.select_set(True)
    for arm in armatures:
        arm.select_set(True)			# the rig travels with the LOD
    bpy.context.view_layer.objects.active = copy
    bpy.ops.export_scene.fbx(filepath=str(path), use_selection=True, apply_unit_scale=True,
                             global_scale=1.0, apply_scale_options="FBX_SCALE_NONE", use_mesh_modifiers=True,
                             add_leaf_bones=False, bake_anim=False, path_mode="COPY")
    print(f"[athlete_lods] LOD{index}: {len(copy.data.polygons)} faces ({len(src.data.polygons)} before) -> {path}")
print("[athlete_lods] done")
