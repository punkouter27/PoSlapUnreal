"""Unreal Editor Python: assets + Arena level for the Phase B dry run. Idempotent.

    UnrealEditor-Cmd.exe PoSlapUnreal.uproject -run=pythonscript -script="<abs path>/Scripts/build_scene.py" -unattended

Chaos stays out: no physics asset, no collision on anything we place. MuJoCo (URLab) owns all physics.
"""
import math
import os
import unreal

PROJ = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir())
ASSETS = unreal.EditorAssetLibrary
tools = unreal.AssetToolsHelpers.get_asset_tools()


def log(msg):
    unreal.log(f"[build_scene] {msg}")


def import_file(src, dest, options=None, name=None):
    t = unreal.AssetImportTask()
    t.filename = os.path.join(PROJ, src)
    t.destination_path = dest
    t.destination_name = name or ""
    t.automated = True
    t.replace_existing = True
    t.save = True
    if options:
        t.options = options
    tools.import_asset_tasks([t])
    log(f"imported {src} -> {list(t.imported_object_paths)}")
    return [ASSETS.load_asset(p) for p in t.imported_object_paths]


# ---- assets
fbx = unreal.FbxImportUI()
for k, v in (("import_mesh", True), ("import_as_skeletal", True), ("import_materials", True),
             ("import_textures", True), ("create_physics_asset", False),   # no Chaos ragdoll
             ("mesh_type_to_import", unreal.FBXImportType.FBXIT_SKELETAL_MESH)):
    fbx.set_editor_property(k, v)
grandpa = next(a for a in import_file("Content/Art/Exports/Grandpa_rigged.fbx", "/Game/Athletes/Grandpa", fbx)
               if isinstance(a, unreal.SkeletalMesh))
# skin material from the scan's PBR textures (Interchange drops the FBX's texture links)
MEL = unreal.MaterialEditingLibrary
tex = {}
for key, fname in (("color", "texture_pbr_20250901"), ("normal", "texture_pbr_20250901_normal"),
                   ("mr", "texture_pbr_20250901_metallic-texture_pbr_20250901_roughness")):
    t = import_file(f"Content/Art/Exports/Grandpa_tex/{fname}.png", "/Game/Athletes/Grandpa/Textures", name=f"T_Grandpa_{key}")[0]
    if key == "normal":
        t.set_editor_property("compression_settings", unreal.TextureCompressionSettings.TC_NORMALMAP)
    if key == "mr":
        t.set_editor_property("srgb", False)
        t.set_editor_property("compression_settings", unreal.TextureCompressionSettings.TC_MASKS)
    ASSETS.save_loaded_asset(t)
    tex[key] = t
skin_path = "/Game/Athletes/Grandpa/M_Grandpa"
if ASSETS.does_asset_exist(skin_path):
    ASSETS.delete_asset(skin_path)
skin = tools.create_asset("M_Grandpa", "/Game/Athletes/Grandpa", unreal.Material, unreal.MaterialFactoryNew())
skin.set_editor_property("used_with_skeletal_mesh", True)
for i, (key, prop, out) in enumerate((("color", unreal.MaterialProperty.MP_BASE_COLOR, "RGB"),
                                      ("normal", unreal.MaterialProperty.MP_NORMAL, "RGB"),
                                      ("mr", unreal.MaterialProperty.MP_ROUGHNESS, "G"))):
    s = MEL.create_material_expression(skin, unreal.MaterialExpressionTextureSample, -400, i * 250)
    s.set_editor_property("texture", tex[key])
    if key == "normal":
        s.set_editor_property("sampler_type", unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL)
    if key == "mr":
        s.set_editor_property("sampler_type", unreal.MaterialSamplerType.SAMPLERTYPE_MASKS)
        MEL.connect_material_property(s, "B", unreal.MaterialProperty.MP_METALLIC)  # glTF: G roughness, B metallic
    MEL.connect_material_property(s, out, prop)
import sys
sys.path.insert(0, os.path.join(PROJ, "Scripts"))
import broadcast_fx
broadcast_fx.add_ripple(skin)   # the cheek ripple on a strike (display only)
broadcast_fx.add_wear(skin)     # flush + fatigue on the skin colour (display only)
MEL.recompile_material(skin)
ASSETS.save_loaded_asset(skin)
mats = grandpa.get_editor_property("materials")
mats[0].set_editor_property("material_interface", skin)
grandpa.set_editor_property("materials", mats)
ASSETS.save_loaded_asset(grandpa)

brain = import_file(os.environ.get("SLAP_BRAIN", "training/models/random_brain.onnx"), "/Game/Brains")[0]  # SLAP_BRAIN=training/models/brains/<char>_<level>.onnx
if ASSETS.does_asset_exist("/Game/Arena/grandpa_vs_grandpa_arena"):   # URLab won't overwrite; the MJCF may have changed
    ASSETS.delete_asset("/Game/Arena/grandpa_vs_grandpa_arena")
arena_bp = next(a for a in import_file("training/models/grandpa_vs_grandpa_arena.xml", "/Game/Arena")
                if isinstance(a, unreal.Blueprint))

# controller component on the arena Blueprint (URLab binds it after compile)
sub = unreal.get_engine_subsystem(unreal.SubobjectDataSubsystem)
handles = sub.k2_gather_subobject_data_for_blueprint(arena_bp)
names = [unreal.SubobjectDataBlueprintFunctionLibrary.get_display_name(
    unreal.SubobjectDataBlueprintFunctionLibrary.get_data(h)) for h in handles]
if not any("SlapController" in str(n) for n in names):
    p = unreal.AddNewSubobjectParams(parent_handle=handles[0], new_class=unreal.SlapController, blueprint_context=arena_bp)
    _, fail = sub.add_new_subobject(p)
    log(f"added SlapController ({fail or 'ok'})")
    handles = sub.k2_gather_subobject_data_for_blueprint(arena_bp)
for h in handles:
    obj = unreal.SubobjectDataBlueprintFunctionLibrary.get_associated_object(unreal.SubobjectDataBlueprintFunctionLibrary.get_data(h))
    if isinstance(obj, unreal.SlapController):
        obj.set_editor_property("brain", brain)
        obj.set_editor_property("mode", unreal.SlapControlMode.POLICY)
unreal.BlueprintEditorLibrary.compile_blueprint(arena_bp)
ASSETS.save_loaded_asset(arena_bp)

# team overlay: translucent fresnel rim in the team colour (one mesh, two tints)
mat_path = "/Game/Athletes/M_TeamOverlay"
if ASSETS.does_asset_exist(mat_path):  # rebuilt each run so its flags stay current
    ASSETS.delete_asset(mat_path)
if True:
    MEL = unreal.MaterialEditingLibrary
    m = tools.create_asset("M_TeamOverlay", "/Game/Athletes", unreal.Material, unreal.MaterialFactoryNew())
    m.set_editor_property("blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT)
    m.set_editor_property("used_with_skeletal_mesh", True)
    m.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_UNLIT)
    col = MEL.create_material_expression(m, unreal.MaterialExpressionVectorParameter, -400, 0)
    col.set_editor_property("parameter_name", "TeamColor")
    fres = MEL.create_material_expression(m, unreal.MaterialExpressionFresnel, -400, 200)
    mul = MEL.create_material_expression(m, unreal.MaterialExpressionMultiply, -200, 200)
    mul.set_editor_property("const_b", 0.7)
    MEL.connect_material_expressions(fres, "", mul, "A")
    MEL.connect_material_property(col, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    MEL.connect_material_property(mul, "", unreal.MaterialProperty.MP_OPACITY)
    MEL.recompile_material(m)
    ASSETS.save_loaded_asset(m)
overlay = {}
for team, rgb in (("Red", (0.9, 0.08, 0.05)), ("Blue", (0.05, 0.2, 0.9))):
    path = f"/Game/Athletes/MI_Team{team}"
    mi = ASSETS.load_asset(path) if ASSETS.does_asset_exist(path) else tools.create_asset(
        f"MI_Team{team}", "/Game/Athletes", unreal.MaterialInstanceConstant, unreal.MaterialInstanceConstantFactoryNew())
    mi.set_editor_property("parent", ASSETS.load_asset(mat_path))
    unreal.MaterialEditingLibrary.set_material_instance_vector_parameter_value(mi, "TeamColor", unreal.LinearColor(*rgb, 1))
    ASSETS.save_loaded_asset(mi)
    overlay[team] = mi

# ---- level
LEVEL = "/Game/Levels/Arena"
les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
if ASSETS.does_asset_exist(LEVEL):
    les.load_level(LEVEL)
    for a in eas.get_all_level_actors():
        eas.destroy_actor(a)
else:
    les.new_level(LEVEL)


def spawn(cls, label, loc=(0, 0, 0), rot=(0, 0, 0)):
    # rot = (pitch, yaw, roll); unreal.Rotator's positional order is (roll, pitch, yaw)
    a = eas.spawn_actor_from_class(cls, unreal.Vector(*loc), unreal.Rotator(pitch=rot[0], yaw=rot[1], roll=rot[2]))
    a.set_actor_label(label)
    return a


mgr = spawn(unreal.AMjManager, "MjManager")
engine = mgr.get_component_by_class(unreal.MjPhysicsEngine)
engine.set_editor_property("save_debug_xml", True)  # Saved/URLab/scene_compiled.mjb for the matching check
engine.set_editor_property("is_paused", False)      # run on Play
mgr.set_editor_property("auto_create_simulate_widget", False)  # URLab debug panel covers the portrait view (Tab still toggles it)
arena = spawn(arena_bp.generated_class(), "Arena")
for team, prefix in (("Red", "red_"), ("Blue", "blue_")):
    v = spawn(unreal.SlapAthleteVisual, f"Athlete{team}")
    v.set_editor_property("body_prefix", prefix)
    mesh = v.get_component_by_class(unreal.PoseableMeshComponent)
    mesh.set_skinned_asset_and_update(grandpa)
    mesh.set_material(0, skin)   # the asset's slot keeps the FBX importer's untextured Material_001; override per component
    mesh.set_editor_property("overlay_material", overlay[team])

# arena look: env meshes (Content/Art/Raw/Arena_v2.blend -> Exports/Arena.glb), lights, haze (Scripts/arena_env.py)
import arena_env   # Scripts/ is on sys.path (broadcast_fx above)
arena_env.build(spawn)

# portrait 9:16 camera, side-on to the X axis the athletes face along (Blueprint 3)
cam = spawn(unreal.CineCameraActor, "PortraitCamera", (0, -520, 130), (0, 90, 0))
cc = cam.get_cine_camera_component()
fb = cc.get_editor_property("filmback")
fb.sensor_width, fb.sensor_height = 13.5, 24.0
cc.set_editor_property("filmback", fb)
cc.set_editor_property("current_focal_length", 16.0)
cc.set_editor_property("constrain_aspect_ratio", True)
cam.set_editor_property("auto_activate_for_player", unreal.AutoReceiveInput.PLAYER0)
pp = cc.get_editor_property("post_process_settings")   # CineCamera defaults to physical exposure: black at default light levels
pp.set_editor_property("override_auto_exposure_method", True)
pp.set_editor_property("auto_exposure_method", unreal.AutoExposureMethod.AEM_HISTOGRAM)
cc.set_editor_property("post_process_settings", arena_env.grade(pp))   # dark arena, hot stage

# Chaos audit: nothing we placed may collide or simulate
n = 0
for a in eas.get_all_level_actors():
    for c in a.get_components_by_class(unreal.PrimitiveComponent):
        c.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
        c.set_simulate_physics(False)
        n += 1
log(f"Chaos off on {n} primitive components")

les.save_current_level()
unreal.get_editor_subsystem(unreal.EditorAssetSubsystem).save_directory("/Game", only_if_is_dirty=True, recursive=True)
log("done")
