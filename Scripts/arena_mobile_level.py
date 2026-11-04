"""Unreal Editor Python: the phone arena. Every arena level gets a "<level>_Mobile" twin dressed with the assets
stream's Content/Art/Exports/Arena_Mobile.glb (Scripts/arena_mobile.py: the flat materials merged into one palette
atlas, M_ArenaPalette / T_ArenaPalette_*, and the crowd cut to 30 % of its faces). Visual only: the MuJoCo actors, the
lights, the camera and the athletes are the desktop level's own (the twin is a copy), only the Env_* meshes change.

    UnrealEditor-Cmd.exe PoSlapUnreal.uproject -run=pythonscript -script="<abs path>/Scripts/arena_mobile_level.py" -unattended

Who opens which: Config/Android/AndroidEngine.ini starts a phone on /Game/Levels/L_Arena_Mobile, and the menu lists only
the _Mobile pairings on a phone and only the full ones on a desktop (SlapHUDMenu.cpp, SlapHUDInternal.h IsMobileLevel).
Re-run after a desktop level changes (build_scene.py) or a new Arena_Mobile.glb: the twins are rebuilt from scratch.
Imported assets carry a "_Mobile" suffix (Scripts/check_naming.py: no base name twice under /Game).
"""
import os

import unreal

PROJ = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir())
ASSETS = unreal.EditorAssetLibrary
MEL = unreal.MaterialEditingLibrary
ENV = "/Game/Arena/EnvMobile"
LEVELS = "/Game/Levels"
SUFFIX = "_Mobile"


def log(msg):
    unreal.log_warning(f"[arena_mobile_level] {msg}")   # a warning: commandlets print nothing below Display


def import_mobile():
    src = os.path.join(PROJ, "Content/Art/Exports/Arena_Mobile.glb")
    if ASSETS.does_directory_exist(ENV):   # replaced in place (the twins reference these paths)
        for p in ASSETS.list_assets(ENV, recursive=True):
            ASSETS.delete_asset(p.split(".")[0])
    t = unreal.AssetImportTask()
    t.filename, t.destination_path, t.automated, t.replace_existing, t.save = src, ENV, True, True, True
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([t])
    meshes = {}
    for p in list(ASSETS.list_assets(ENV, recursive=True)):
        a = ASSETS.load_asset(p.split(".")[0])
        if a is None:
            continue
        n = a.get_name()
        if isinstance(a, unreal.StaticMesh):
            base = n[3:] if n.startswith("SM_") else n
            new = f"SM_{base}{SUFFIX}"
        elif isinstance(a, unreal.MaterialInstanceConstant):
            base = n[3:] if n.startswith("MI_") else (n[2:] if n.startswith("M_") else n)
            new = f"MI_{base}{SUFFIX}"
        elif isinstance(a, unreal.Texture2D):
            new = n if n.endswith(SUFFIX) else f"{n}{SUFFIX}"
            if "ArenaPalette" in n:   # cells sampled at their centre: nearest, no blur across cells (arena_mobile.py)
                a.set_editor_property("filter", unreal.TextureFilter.TF_NEAREST)
                a.set_editor_property("mip_gen_settings", unreal.TextureMipGenSettings.TMGS_NO_MIPMAPS)
                if n.endswith(("_ORM", "_ORM" + SUFFIX)):
                    a.set_editor_property("srgb", False)
            a.set_editor_property("lod_group", unreal.TextureGroup.TEXTUREGROUP_WORLD)
            ASSETS.save_loaded_asset(a)
        else:
            new = n if n.endswith(SUFFIX) else f"{n}{SUFFIX}"
        if new != n:
            folder = a.get_path_name().rsplit("/", 1)[0]
            ASSETS.rename_loaded_asset(a, f"{folder}/{new}")
        if isinstance(a, unreal.StaticMesh):
            ns = a.get_editor_property("nanite_settings")   # a phone has no Nanite: plain meshes, no fallback build
            ns.enabled = False
            a.set_editor_property("nanite_settings", ns)
            ASSETS.save_loaded_asset(a)
            meshes[base] = a
    # the crowd reacts on a phone too (broadcast_fx.py add_crowd): its instances go onto /Game/FX/M_CrowdLive
    live = ASSETS.load_asset("/Game/FX/M_CrowdLive") if ASSETS.does_asset_exist("/Game/FX/M_CrowdLive") else None
    for p in ASSETS.list_assets(ENV, recursive=True):
        a = ASSETS.load_asset(p.split(".")[0])
        if live and isinstance(a, unreal.MaterialInstanceConstant) and a.get_name() in (f"MI_Crowd{SUFFIX}", f"MI_PhoneLight{SUFFIX}"):
            MEL.set_material_instance_parent(a, live)
            if a.get_name().startswith("MI_PhoneLight"):
                MEL.set_material_instance_scalar_parameter_value(a, "CheerGlow", 4.0)
            MEL.update_material_instance(a)
            ASSETS.save_loaded_asset(a)
    log(f"imported {len(meshes)} meshes from Arena_Mobile.glb (Nanite off)")
    return meshes


def twin(level, meshes):
    """Load the desktop level, swap its Env_* meshes for the phone arena's, and save the world AS the twin (an existing
    twin is overwritten). The desktop level itself is never saved here: it is reloaded clean for the next twin."""
    les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    dst = f"{level}{SUFFIX}"
    if not les.load_level(level):
        log(f"{level}: could not load")
        return False
    swapped, kept = 0, []
    for a in eas.get_all_level_actors():
        label = a.get_actor_label()
        if not isinstance(a, unreal.StaticMeshActor) or not label.startswith("Env_"):
            continue
        m = meshes.get(label[4:])
        if m is None:
            kept.append(label)
            continue
        a.static_mesh_component.set_static_mesh(m)
        swapped += 1
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    if not unreal.EditorLoadingAndSavingUtils.save_map(world, dst):
        log(f"{dst}: the save failed")
        return False
    log(f"{dst}: {swapped} env meshes swapped to the phone arena" + (f", kept {kept}" if kept else ""))
    return True


def main():
    meshes = import_mobile()
    if not meshes:
        log("no meshes imported: stop")
        return
    levels = sorted(p.split(".")[0] for p in ASSETS.list_assets(LEVELS, recursive=False)
                    if p.rsplit("/", 1)[-1].startswith("L_Arena") and not p.split(".")[0].endswith(SUFFIX))
    for lv in levels:
        twin(lv, meshes)


if __name__ == "__main__":
    main()
