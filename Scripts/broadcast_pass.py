"""Unreal Editor Python: the assets of the 2026-11-12 broadcast pass. Idempotent. Display only: MuJoCo owns every contact.

    UnrealEditor-Cmd.exe PoSlapUnreal.uproject -run=pythonscript -script="<abs path>/Scripts/broadcast_pass.py" -unattended

  /Game/FX/M_Jumbotron        the arena screen (ASlapHUD::Jumbo): an unlit panel showing the texture parameter "Screen"
                              (a scene capture's render target) x Glow, with LED rows and a dark bezel
  M_<athlete>                 soft tissue (ASlapHUD::Jiggle): the cheek ripple's world-position offset gains two sprung
                              offsets - JiggleA / JiggleAV / JiggleAR (the belly: centre, offset in cm, radius) and
                              JiggleB / JiggleBV / JiggleBR (the jowl). The bones are untouched: only the skin moves.
  /Game/Cinematics/LS_*       Sequencer camera moves the director plays (ASlapHUD::SeqTick), each one a spawnable Cine
                              Camera (ASlapHUD gives it the portrait filmback and its lens), keyed in the space of a transform origin the HUD supplies:
                              LS_Intro (a crane down across both aisles as the walk-in begins), LS_KO_Red / LS_KO_Blue (from
                              the fallen athlete up to a top shot), LS_Winner_Red / LS_Winner_Blue (a low hero push on
                              the winner). SLAP_SEQ_REBUILD=1 rebuilds them.
"""
import math
import os

import unreal

ASSETS = unreal.EditorAssetLibrary
MEL = unreal.MaterialEditingLibrary
TOOLS = unreal.AssetToolsHelpers.get_asset_tools()
FPS = 30
SENSOR = (13.365, 23.76)   # mm, portrait 9:16 (a Super 35 gate turned on its side): the level camera's aspect


def log(msg):
    unreal.log_warning(f"[broadcast_pass] {msg}")   # a warning: commandlets print nothing below Display


# ---- the jumbotron ---------------------------------------------------------------------------------------------------

JUMBO = ("float2 e = min(UV, 1 - UV);\n"
         "float inside = step(0.02, e.x * 1.7778) * step(0.02, e.y);\n"                 # a 2 % bezel (the panel is 16:9)
         "float rows = 0.86 + 0.14 * sin(UV.y * 1130.0);\n"                            # LED rows
         "return C * G * rows * inside + (1 - inside) * float3(0.004, 0.004, 0.005);")


def custom(mat, code, inputs, x, y):
    node = MEL.create_material_expression(mat, unreal.MaterialExpressionCustom, x, y)
    node.set_editor_property("code", code)
    node.set_editor_property("output_type", unreal.CustomMaterialOutputType.CMOT_FLOAT3)
    ins = []
    for n in inputs:
        c = unreal.CustomInput()
        c.set_editor_property("input_name", n)
        ins.append(c)
    node.set_editor_property("inputs", ins)
    return node


def add_jumbotron():
    path = "/Game/FX/M_Jumbotron"
    if ASSETS.does_asset_exist(path):
        log("M_Jumbotron: already there")
        return
    mat = TOOLS.create_asset("M_Jumbotron", "/Game/FX", unreal.Material, unreal.MaterialFactoryNew())
    mat.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_UNLIT)
    tex = MEL.create_material_expression(mat, unreal.MaterialExpressionTextureSampleParameter2D, -700, 0)
    tex.set_editor_property("parameter_name", "Screen")
    uv = MEL.create_material_expression(mat, unreal.MaterialExpressionTextureCoordinate, -700, 250)
    glow = MEL.create_material_expression(mat, unreal.MaterialExpressionScalarParameter, -700, 350)
    glow.set_editor_property("parameter_name", "Glow")
    glow.set_editor_property("default_value", 1.4)
    node = custom(mat, JUMBO, ("C", "UV", "G"), -350, 100)
    MEL.connect_material_expressions(tex, "RGB", node, "C")
    MEL.connect_material_expressions(uv, "", node, "UV")
    MEL.connect_material_expressions(glow, "", node, "G")
    MEL.connect_material_property(node, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    MEL.recompile_material(mat)
    ASSETS.save_loaded_asset(mat)
    log("M_Jumbotron (unlit: Screen x Glow, LED rows, bezel)")


# ---- soft tissue -----------------------------------------------------------------------------------------------------

JIGGLE = ("float a = saturate(1 - distance(P, A) / max(RA, 1));\n"                    # smoothstep falloffs, cm
          "float b = saturate(1 - distance(P, B) / max(RB, 1));\n"
          "return R + VA * (a * a * (3 - 2 * a)) + VB * (b * b * (3 - 2 * b));")
JIGGLE_IN = ("R", "P", "A", "VA", "RA", "B", "VB", "RB")


def add_jiggle(skin):
    """Wraps whatever drives the skin's world-position offset (the cheek ripple) in the two sprung offsets."""
    prop = unreal.MaterialProperty.MP_WORLD_POSITION_OFFSET
    src = MEL.get_material_property_input_node(skin, prop)
    if src is None:
        log(f"{skin.get_name()}: no world-position offset to extend (run broadcast_fx.py first)")
        return False
    if isinstance(src, unreal.MaterialExpressionCustom) and src.get_editor_property("code") == JIGGLE:
        return False
    wp = MEL.create_material_expression(skin, unreal.MaterialExpressionWorldPosition, -900, 1900)
    nodes = {"R": src, "P": wp}
    for i, (name, kind, default) in enumerate((("JiggleA", "v", None), ("JiggleAV", "v", None), ("JiggleAR", "s", 24.0),
                                               ("JiggleB", "v", None), ("JiggleBV", "v", None), ("JiggleBR", "s", 9.0))):
        cls = unreal.MaterialExpressionVectorParameter if kind == "v" else unreal.MaterialExpressionScalarParameter
        p = MEL.create_material_expression(skin, cls, -900, 2000 + 130 * i)
        p.set_editor_property("parameter_name", name)
        if kind == "v":
            p.set_editor_property("default_value", unreal.LinearColor(0, 0, 0, 0))
        else:
            p.set_editor_property("default_value", default)
        nodes[{"JiggleA": "A", "JiggleAV": "VA", "JiggleAR": "RA", "JiggleB": "B", "JiggleBV": "VB", "JiggleBR": "RB"}[name]] = p
    node = custom(skin, JIGGLE, JIGGLE_IN, -350, 1900)
    for n in JIGGLE_IN:
        MEL.connect_material_expressions(nodes[n], "", node, n)
    MEL.connect_material_property(node, "", prop)
    try:
        skin.set_editor_property("max_world_position_offset_displacement", 25.0)
    except Exception:
        pass
    MEL.recompile_material(skin)
    ASSETS.save_loaded_asset(skin)
    return True


def athlete_skins():
    out = []
    for asset in ASSETS.list_assets("/Game/Athletes", recursive=True):
        path = asset.split(".")[0]
        base = path.rsplit("/", 1)[-1]
        if base.startswith("M_") and ASSETS.does_asset_exist(path.rsplit("/", 1)[0] + "/SK_" + base[2:]):
            out.append(path)
    return sorted(out)


# ---- the Sequencer shots ---------------------------------------------------------------------------------------------

def look(frm, to):
    """(roll, pitch, yaw) in degrees aiming a camera's +X from frm at to (UE cm)."""
    dx, dy, dz = (to[i] - frm[i] for i in range(3))
    return 0.0, math.degrees(math.atan2(dz, math.hypot(dx, dy))), math.degrees(math.atan2(dy, dx))


def add_shot(name, seconds, focal, keys):   # focal: for the log; SlapHUDStage.cpp SeqFocal holds the lens
    """keys: [(t seconds, camera position, look-at point)], cm in the transform origin's space."""
    path = f"/Game/Cinematics/{name}"
    if ASSETS.does_asset_exist(path):
        if os.environ.get("SLAP_SEQ_REBUILD", "0") != "1":
            log(f"{name}: already there")
            return
        ASSETS.delete_asset(path)
    seq = TOOLS.create_asset(name, "/Game/Cinematics", unreal.LevelSequence, unreal.LevelSequenceFactoryNew())
    end = int(round(seconds * FPS))
    seq.set_display_rate(unreal.FrameRate(FPS, 1))
    seq.set_playback_start(0)
    seq.set_playback_end(end)
    cam = seq.add_spawnable_from_class(unreal.CineCameraActor)
    # the lens is set at play time (ASlapHUD::SeqTick: the portrait filmback, the focal length, the focus): a spawnable's
    # template hands Python no camera component to edit
    track = cam.add_track(unreal.MovieScene3DTransformTrack)
    section = track.add_section()
    section.set_range(0, end)
    ch = section.get_all_channels()   # location x y z, rotation roll pitch yaw, scale x y z
    prev_yaw = None
    for t, pos, at in keys:
        roll, pitch, yaw = look(pos, at)
        if prev_yaw is not None:      # the short way round between keys
            while yaw - prev_yaw > 180.0:
                yaw -= 360.0
            while yaw - prev_yaw < -180.0:
                yaw += 360.0
        prev_yaw = yaw
        frame = unreal.FrameNumber(int(round(t * FPS)))
        for k, v in enumerate((pos[0], pos[1], pos[2], roll, pitch, yaw)):
            ch[k].add_key(frame, float(v), interpolation=unreal.MovieSceneKeyInterpolation.AUTO)

    cut = seq.add_track(unreal.MovieSceneCameraCutTrack)
    cs = cut.add_section()
    cs.set_range(0, end)
    cs.set_camera_binding_id(seq.get_binding_id(cam))
    ASSETS.save_loaded_asset(seq)
    log(f"{name}: {seconds:.1f} s, {focal:.0f} mm, {len(keys)} keys")


def add_shots():
    # the origin is the floor under the middle of the podium; the broadcast side is +Y, red stands at -X, blue at +X.
    # Every key stays inside 2.0 m of the centre at lens height: Arena.glb's camera jibs start 1.8 m out and the press
    # tables 2.9 m (SlapHUDCamera.cpp MaxWideDist); the crane's high keys look down over them.
    # the walk-in: both come down their aisles (2.4 m out along X), so the crane finds red, swings across to blue and
    # settles on the podium as they arrive
    add_shot("LS_Intro", 3.4, 16.0, [
        (0.0, (-30, 195, 215), (-250, 0, 105)),
        (1.3, (10, 195, 170), (40, 0, 112)),
        (2.2, (30, 190, 135), (190, 0, 108)),
        (3.4, (0, 175, 96), (0, 0, 116)),
    ])
    for side, sx in (("Red", -1.0), ("Blue", 1.0)):   # who fell
        add_shot(f"LS_KO_{side}", 4.0, 18.0, [
            (0.0, (sx * 95, 150, 42), (sx * 45, 0, 40)),
            (1.8, (sx * -55, 190, 150), (0, 0, 55)),
            (4.0, (0, 95, 330), (0, 0, 20)),
        ])
    for side, f in (("Red", 1.0), ("Blue", -1.0)):    # the origin is the winner's own spot; he faces f along X
        add_shot(f"LS_Winner_{side}", 5.0, 24.0, [
            (0.0, (f * 60, 190, 62), (0, 0, 150)),
            (2.5, (f * 95, 150, 86), (0, 0, 156)),
            (5.0, (f * 110, 105, 112), (0, 0, 160)),
        ])


def main():
    add_jumbotron()
    for path in athlete_skins():
        skin = ASSETS.load_asset(path)
        log(f"{skin.get_name()}: soft tissue {'added' if add_jiggle(skin) else 'unchanged'}")
    add_shots()


if __name__ == "__main__":
    main()
