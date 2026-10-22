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

# arena (Blender: Content/Art/Raw/Arena.blend -> Exports/Arena.glb): stage, safety mat, podium shell, seating, crowd,
# truss, LED ring, roof. Built in MuJoCo coordinates (floor z = 0), so it sits at the origin. Visual only.
env_mesh = next(a for a in import_file("Content/Art/Exports/Arena.glb", "/Game/Arena/Env") if isinstance(a, unreal.StaticMesh))
env = spawn(unreal.StaticMeshActor, "ArenaEnvironment")
env.static_mesh_component.set_static_mesh(env_mesh)
env.static_mesh_component.set_mobility(unreal.ComponentMobility.STATIC)


def aim(frm, to):  # (pitch, yaw, roll) pointing a light's +X from frm to to (UE cm)
    d = [b - a for a, b in zip(frm, to)]
    return (math.degrees(math.atan2(d[2], math.hypot(d[0], d[1]))), math.degrees(math.atan2(d[1], d[0])), 0)


def spot(label, loc, lumens, cone, to=(0, 0, 100)):
    s = spawn(unreal.SpotLight, label, loc, aim(loc, to))
    c = s.spot_light_component
    c.set_mobility(unreal.ComponentMobility.MOVABLE)
    c.set_editor_property("intensity", lumens)
    c.set_editor_property("outer_cone_angle", cone)
    c.set_editor_property("inner_cone_angle", cone * 0.6)
    c.set_editor_property("attenuation_radius", 1600.0)
    c.set_editor_property("volumetric_scattering_intensity", 0.4)
    return s


# indoor arena light: a hot key on the stage, rim spots from the truss (Blender's 16 cans), dim house light, haze
spot("KeyLight", (0, 0, 680), 4500.0, 26.0)
for k in range(16):
    t = k / 16 * 4; side, f = int(t), t - int(t)
    cs = [(-350, -350), (350, -350), (350, 350), (-350, 350)]
    (x0, y0), (x1, y1) = cs[side], cs[(side + 1) % 4]
    spot(f"TrussSpot{k}", (x0 + (x1 - x0) * f, y0 + (y1 - y0) * f, 675), 1500.0, 20.0).spot_light_component.set_editor_property("cast_shadows", False)
for k in range(8):   # house lights over the crowd
    a = k / 8 * 2 * math.pi
    p = spawn(unreal.PointLight, f"HouseLight{k}", (1100 * math.cos(a), 1100 * math.sin(a), 1150))
    pc = p.point_light_component
    pc.set_mobility(unreal.ComponentMobility.MOVABLE)
    pc.set_editor_property("intensity", 2500.0)
    pc.set_editor_property("cast_shadows", False)
    pc.set_editor_property("attenuation_radius", 1400.0)
    pc.set_editor_property("light_color", unreal.Color(180, 190, 255, 255))
fog = spawn(unreal.ExponentialHeightFog, "ArenaHaze", (0, 0, -60)).component
fog.set_editor_property("fog_density", 0.002)
fog.set_editor_property("enable_volumetric_fog", True)
for prop in ("fog_inscattering_luminance", "fog_inscattering_color"):   # renamed across UE versions; cosmetic
    try:
        fog.set_editor_property(prop, unreal.LinearColor(0.01, 0.01, 0.015, 1))
        break
    except Exception:
        pass

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
pp.set_editor_property("override_auto_exposure_bias", True)
pp.set_editor_property("auto_exposure_bias", -1.5)   # dark arena, hot stage
cc.set_editor_property("post_process_settings", pp)

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
