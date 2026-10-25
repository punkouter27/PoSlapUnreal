"""Unreal Editor Python: set every arena Blueprint's SlapController defender_role (nothing else touched).

    UnrealEditor-Cmd.exe PoSlapUnreal.uproject -run=pythonscript -script="<abs path>/Scripts/set_defender_role.py" -unattended

SLAP_DEFENDER_ROLE: -1 (default; brains from L3 on observe the striker's head and hand) or 0 (guard brains before L3).
SLAP_ARENAS: optional ';' list of arena Blueprints (default: every /Game/Arena/*_arena).
"""
import os
import unreal

ROLE = int(os.environ.get("SLAP_DEFENDER_ROLE", "-1"))
arenas = os.environ.get("SLAP_ARENAS")
assets = arenas.split(";") if arenas else unreal.EditorAssetLibrary.list_assets("/Game/Arena", recursive=False, include_folder=False)
sub = unreal.get_engine_subsystem(unreal.SubobjectDataSubsystem)
seen = 0
for path in assets:
    bp = unreal.EditorAssetLibrary.load_asset(path)
    if not isinstance(bp, unreal.Blueprint):
        continue
    n = 0
    for h in sub.k2_gather_subobject_data_for_blueprint(bp):
        lib = unreal.SubobjectDataBlueprintFunctionLibrary
        obj = lib.get_associated_object(lib.get_data(h))
        if isinstance(obj, unreal.SlapController):
            obj.set_editor_property("defender_role", ROLE)
            n += 1
    if n:
        unreal.BlueprintEditorLibrary.compile_blueprint(bp)
        unreal.EditorAssetLibrary.save_loaded_asset(bp)
        seen += n
    unreal.log(f"[set_defender_role] {bp.get_path_name()}: {n} SlapController(s) -> {ROLE}")
unreal.log(f"[set_defender_role] done: {seen} component(s) at role {ROLE}")
