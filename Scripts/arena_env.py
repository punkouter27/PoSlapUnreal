"""Unreal Editor Python: the arena's look. Visual only; MuJoCo owns every collision (Chaos stays off).

    UnrealEditor-Cmd.exe PoSlapUnreal.uproject -run=pythonscript -script="<abs path>/Scripts/arena_env.py" -unattended

Standalone it refreshes the Arena level in place (env meshes, lights, haze, camera grade); build_scene.py calls build().
Source: Content/Art/Raw/Arena_v2.blend -> Content/Art/Exports/Arena.glb, one node per asset, positions baked in MuJoCo
coordinates (floor z = 0), so every mesh actor sits at the origin. Nanite on every mesh (no hand-made LODs).
"""
import math
import os

import unreal

PROJ = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir())
ASSETS = unreal.EditorAssetLibrary
ENV = "/Game/Arena/Env"
LABELS = ("Env_", "KeyLight", "TrussSpot", "HouseLight", "RimRed", "RimBlue", "ArenaHaze", "ArenaEnvironment")


def log(msg):
    unreal.log(f"[arena_env] {msg}")


def import_env(reimport=True):
    if not reimport and ASSETS.does_directory_exist(ENV):   # every arena level shares these: reuse them (build_scene.py)
        meshes = [m for m in (ASSETS.load_asset(p) for p in ASSETS.list_assets(ENV, recursive=True)) if isinstance(m, unreal.StaticMesh)]
        if meshes:
            log(f"reusing {len(meshes)} meshes in {ENV}")
            return meshes
    if ASSETS.does_directory_exist(ENV):   # the previous export's assets (names changed with the v2 scene)
        ASSETS.delete_directory(ENV)
    t = unreal.AssetImportTask()
    t.filename, t.destination_path, t.automated, t.replace_existing, t.save = os.path.join(PROJ, "Content/Art/Exports/Arena.glb"), ENV, True, True, True
    unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([t])
    meshes = [m for m in (ASSETS.load_asset(p) for p in t.imported_object_paths) if isinstance(m, unreal.StaticMesh)]
    for m in meshes:
        ns = m.get_editor_property("nanite_settings")
        ns.enabled = True
        m.set_editor_property("nanite_settings", ns)
        ASSETS.save_loaded_asset(m)
    log(f"imported {len(meshes)} meshes (Nanite on)")
    return meshes


def aim(frm, to):  # (pitch, yaw, roll) pointing a light's +X from frm to to (UE cm)
    d = [b - a for a, b in zip(frm, to)]
    return (math.degrees(math.atan2(d[2], math.hypot(d[0], d[1]))), math.degrees(math.atan2(d[1], d[0])), 0)


def build(spawn, reimport=True):
    for m in import_env(reimport):
        a = spawn(unreal.StaticMeshActor, f"Env_{m.get_name()}")
        a.static_mesh_component.set_static_mesh(m)
        a.static_mesh_component.set_mobility(unreal.ComponentMobility.STATIC)
        a.static_mesh_component.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
        a.set_folder_path("ArenaEnv")

    def spot(label, loc, lumens, cone, to=(0, 0, 100), color=None, beams=0.6, shadows=True):
        s = spawn(unreal.SpotLight, label, loc, aim(loc, to))
        c = s.spot_light_component
        c.set_mobility(unreal.ComponentMobility.MOVABLE)
        c.set_editor_property("intensity", lumens)
        c.set_editor_property("outer_cone_angle", cone)
        c.set_editor_property("inner_cone_angle", cone * 0.6)
        c.set_editor_property("attenuation_radius", 1600.0)
        c.set_editor_property("volumetric_scattering_intensity", beams)   # visible beams in the haze
        c.set_editor_property("cast_shadows", shadows)
        if color:
            c.set_editor_property("light_color", unreal.Color(*color, 255))
        s.set_folder_path("ArenaLights")
        return s

    # the stage leads (~6:1 over the crowd): hot key, truss spots, team-colour rims from behind, dim house light
    spot("KeyLight", (0, 0, 680), 4500.0, 26.0, beams=1.5)
    cs = [(-350, -350), (350, -350), (350, 350), (-350, 350)]
    for k in range(16):
        t = k / 16 * 4; side, f = int(t), t - int(t)
        (x0, y0), (x1, y1) = cs[side], cs[(side + 1) % 4]
        spot(f"TrussSpot{k}", (x0 + (x1 - x0) * f, y0 + (y1 - y0) * f, 675), 1500.0, 20.0, beams=0.8, shadows=False)
    # rims: behind the athletes from the camera (camera at UE y = -520, so behind = +y), warm red corner, cool blue corner
    spot("RimRed", (-180, 320, 320), 2500.0, 22.0, to=(-43, 0, 130), color=(255, 140, 90), beams=0.5)
    spot("RimBlue", (180, 320, 320), 2500.0, 22.0, to=(43, 0, 130), color=(115, 150, 255), beams=0.5)
    for k in range(8):
        a = k / 8 * 2 * math.pi
        p = spawn(unreal.PointLight, f"HouseLight{k}", (1100 * math.cos(a), 1100 * math.sin(a), 1150))
        pc = p.point_light_component
        pc.set_mobility(unreal.ComponentMobility.MOVABLE)
        pc.set_editor_property("intensity", 1000.0)
        pc.set_editor_property("cast_shadows", False)
        pc.set_editor_property("attenuation_radius", 1400.0)
        pc.set_editor_property("light_color", unreal.Color(180, 190, 255, 255))
        p.set_folder_path("ArenaLights")
    fog = spawn(unreal.ExponentialHeightFog, "ArenaHaze", (0, 0, -60)).component
    fog.set_editor_property("fog_density", 0.003)
    fog.set_editor_property("enable_volumetric_fog", True)
    fog.set_editor_property("volumetric_fog_scattering_distribution", 0.6)   # forward scattering: beams toward the camera
    for prop in ("fog_inscattering_luminance", "fog_inscattering_color"):   # renamed across UE versions; cosmetic
        try:
            fog.set_editor_property(prop, unreal.LinearColor(0.01, 0.01, 0.015, 1))
            break
        except Exception:
            pass


def grade(pp):
    """Camera post: histogram exposure (as calibrated), slower adaptation (no pumping), crushed blacks, warm highlights."""
    for k, v in (("auto_exposure_method", unreal.AutoExposureMethod.AEM_HISTOGRAM), ("auto_exposure_bias", -1.5),
                 ("auto_exposure_speed_up", 1.0), ("auto_exposure_speed_down", 0.5), ("bloom_intensity", 0.6),
                 ("film_toe", 0.62), ("vignette_intensity", 0.45),
                 ("color_gain_highlights", unreal.Vector4(1.05, 1.0, 0.93, 1.0)), ("color_saturation", unreal.Vector4(1.05, 1.05, 1.05, 1.0))):
        pp.set_editor_property("override_" + k, True)
        pp.set_editor_property(k, v)
    return pp


if __name__ == "__main__":
    les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    les.load_level("/Game/Levels/Arena")
    for a in eas.get_all_level_actors():
        if a.get_actor_label().startswith(LABELS):
            eas.destroy_actor(a)

    def spawn(cls, label, loc=(0, 0, 0), rot=(0, 0, 0)):
        a = eas.spawn_actor_from_class(cls, unreal.Vector(*loc), unreal.Rotator(pitch=rot[0], yaw=rot[1], roll=rot[2]))
        a.set_actor_label(label)
        return a

    build(spawn)
    for a in eas.get_all_level_actors():
        if isinstance(a, unreal.CineCameraActor):
            cc = a.get_cine_camera_component()
            cc.set_editor_property("post_process_settings", grade(cc.get_editor_property("post_process_settings")))
    les.save_current_level()
    unreal.get_editor_subsystem(unreal.EditorAssetSubsystem).save_directory("/Game", only_if_is_dirty=True, recursive=True)
    log("level refreshed")
