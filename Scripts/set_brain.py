"""Unreal Editor Python: put a trained brain on the arena's SlapController, nothing else (the scene and art stay).

    set SLAP_BRAIN=training/models/brains/grandpa_L4.onnx
    UnrealEditor-Cmd.exe PoSlapUnreal.uproject -run=pythonscript -script="<abs path>/Scripts/set_brain.py" -unattended

SLAP_DEFENDER_ROLE: -1 (default; brains from L3 on) or 0 (guard brains before L3).
"""
import os
import unreal

PROJ = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir())
ASSETS = unreal.EditorAssetLibrary
src = os.environ["SLAP_BRAIN"]

t = unreal.AssetImportTask()
t.filename, t.destination_path, t.automated, t.replace_existing, t.save = os.path.join(PROJ, src), "/Game/Brains", True, True, True
unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([t])
brain = ASSETS.load_asset(t.imported_object_paths[0])

arena_bp = ASSETS.load_asset("/Game/Arena/grandpa_vs_grandpa_arena")
sub = unreal.get_engine_subsystem(unreal.SubobjectDataSubsystem)
n = 0
for h in sub.k2_gather_subobject_data_for_blueprint(arena_bp):
    obj = unreal.SubobjectDataBlueprintFunctionLibrary.get_associated_object(unreal.SubobjectDataBlueprintFunctionLibrary.get_data(h))
    if isinstance(obj, unreal.SlapController):
        obj.set_editor_property("brain", brain)
        obj.set_editor_property("defender_role", int(os.environ.get("SLAP_DEFENDER_ROLE", "-1")))
        n += 1
unreal.BlueprintEditorLibrary.compile_blueprint(arena_bp)
ASSETS.save_loaded_asset(arena_bp)
unreal.log(f"[set_brain] {src} -> {brain.get_path_name()} on {n} SlapController(s)")
