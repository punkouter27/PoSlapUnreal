"""Unreal Editor Python: re-import the athletes' meshes in place (with their morph targets) and refresh their skins, without
touching any level. Idempotent.

    UnrealEditor-Cmd.exe PoSlapUnreal.uproject -run=pythonscript -script="<abs path>/Scripts/reimport_athletes.py" -unattended

SLAP_CARDS = comma-separated character cards (default: every athlete in /Game/Athletes). Per athlete:
  - SK_<Name> from Content/Art/Exports/<Name>_rigged.fbx with morph targets on (the assets stream's CheekDent_L / _R,
    the athlete's own side; ASlapHUD drives them 0..1 at a touch), its material slot kept on M_<Name>, LODs 1-2;
  - T_<Name>_CheekMask (linear, TC_Masks, the character texture group) into M_<Name>'s wear node (broadcast_fx.add_wear:
    base colour lerps toward flush red by mask.r x CheekFlush);
  - the scan textures into the character texture group (Android caps it at 2048: Config/DefaultDeviceProfiles.ini).
Then the decal textures and M_DecalTex (broadcast_fx.add_decals). build_scene.py's own SLAP_REIMPORT=1 rebuilds a level too;
this script leaves every level as it is (their components reference SK_<Name> / M_<Name> by path, which stay put).
"""
import os
import sys
import unreal

PROJ = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir())
sys.path.insert(0, os.path.join(PROJ, "Scripts"))
import broadcast_fx  # noqa: E402

ASSETS = unreal.EditorAssetLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()


def log(msg):
    unreal.log_warning(f"[reimport_athletes] {msg}")   # a warning: commandlets print nothing below Display


def task(src, dest, name, options=None):
    t = unreal.AssetImportTask()
    t.filename = os.path.join(PROJ, src)
    if not os.path.isfile(t.filename):
        raise FileNotFoundError(t.filename)
    t.destination_path, t.destination_name = dest, name
    t.automated, t.replace_existing, t.save = True, True, True
    if options:
        t.options = options
    tools.import_asset_tasks([t])
    return [ASSETS.load_asset(p) for p in t.imported_object_paths]


def athlete(name):
    Name = name.capitalize()
    folder = f"/Game/Athletes/{Name}"
    skin_path, mesh_path = f"{folder}/M_{Name}", f"{folder}/SK_{Name}"
    fbx_src = f"Content/Art/Exports/{Name}_rigged.fbx"
    if not ASSETS.does_asset_exist(skin_path) or not os.path.isfile(os.path.join(PROJ, fbx_src)):
        log(f"{Name}: no M_{Name} or no {fbx_src}: skipped (build_scene.py imports a new athlete)")
        return
    fbx = unreal.FbxImportUI()
    for k, v in (("import_mesh", True), ("import_as_skeletal", True), ("import_materials", False), ("import_textures", False),
                 ("create_physics_asset", False), ("mesh_type_to_import", unreal.FBXImportType.FBXIT_SKELETAL_MESH)):
        fbx.set_editor_property(k, v)
    skel = ASSETS.load_asset(f"{folder}/SKEL_{Name}")
    if skel:
        fbx.set_editor_property("skeleton", skel)
    fbx.skeletal_mesh_import_data.set_editor_property("import_morph_targets", True)
    mesh = next((a for a in task(fbx_src, folder, f"SK_{Name}", fbx) if isinstance(a, unreal.SkeletalMesh)), None)
    if mesh is None:
        mesh = ASSETS.load_asset(mesh_path)
    skin = ASSETS.load_asset(skin_path)
    mats = mesh.get_editor_property("materials")
    if mats:
        mats[0].set_editor_property("material_interface", skin)
        mesh.set_editor_property("materials", mats)
    broadcast_fx.add_lods(mesh)
    ASSETS.save_loaded_asset(mesh)
    try:
        morphs = [m.get_name() for m in mesh.get_editor_property("morph_targets")]
    except Exception:   # not exposed to Python in every engine build
        morphs = "(not readable from Python; the HUD logs them at launch)"
    log(f"SK_{Name}: morph targets {morphs}")
    for t in ASSETS.list_assets(f"{folder}/Textures", recursive=False):
        tex = ASSETS.load_asset(t)
        if isinstance(tex, unreal.Texture2D) and tex.get_editor_property("lod_group") != unreal.TextureGroup.TEXTUREGROUP_CHARACTER:
            tex.set_editor_property("lod_group", unreal.TextureGroup.TEXTUREGROUP_CHARACTER)
            ASSETS.save_loaded_asset(tex)
    mask = None
    mask_src = f"Content/Art/Exports/{Name}_tex/T_{Name}_CheekMask.png"
    if os.path.isfile(os.path.join(PROJ, mask_src)):
        mask = task(mask_src, f"{folder}/Textures", f"T_{Name}_CheekMask")[0]
        mask.set_editor_property("srgb", False)
        mask.set_editor_property("compression_settings", unreal.TextureCompressionSettings.TC_MASKS)
        mask.set_editor_property("lod_group", unreal.TextureGroup.TEXTUREGROUP_CHARACTER)
        ASSETS.save_loaded_asset(mask)
    if broadcast_fx.add_wear(skin, mask):
        broadcast_fx.MEL.recompile_material(skin)
        ASSETS.save_loaded_asset(skin)
    log(f"M_{Name}: wear node with the cheek mask {'T_' + Name + '_CheekMask' if mask else '(none: CheekFlush off)'}")


cards = [c for c in os.environ.get("SLAP_CARDS", "").split(",") if c] or sorted(n.lower() for n in broadcast_fx.athlete_names() if n[:1].isupper())
for c in cards:
    athlete(c)
broadcast_fx.add_decals()
log("done")
