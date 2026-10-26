"""Unreal Editor Python: the broadcast's impact assets. Idempotent; build_scene.py calls add_ripple() on the skin it builds.

    training/.venv/Scripts/python.exe Scripts/broadcast_audio.py        (the WAVs, first)
    UnrealEditor-Cmd.exe PoSlapUnreal.uproject -run=pythonscript -script="<abs path>/Scripts/broadcast_fx.py" -unattended

/Game/Audio/S_Slap, S_CrowdReact, S_CrowdLoop (looping), S_Bell; /Game/FX/NS_SlapSpray (the engine's DirectionalBurst
template); the cheek ripple on M_Grandpa: world-position offset along the normal, a damped ring around RipplePoint driven
by RippleTime / RippleAmp (ASlapHUD sets them per athlete on a strike); wear on M_Grandpa's colour: a red flush around
FlushPoint (Flush, from neck damage) and a greyer skin (Tired, from stamina); the crowd (/Game/FX/M_CrowdLive, the parent
of the arena's M_Crowd and M_PhoneLight): Rise bounces the seats, Cheer flickers the phone lights (ASlapHUD::Crowd).
Display only: MuJoCo owns every contact.
"""
import os
import unreal

PROJ = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir())
ASSETS = unreal.EditorAssetLibrary
MEL = unreal.MaterialEditingLibrary


def log(msg):
    unreal.log(f"[broadcast_fx] {msg}")


def add_ripple(skin):
    """A damped ring of world-position offset around RipplePoint, at most 14 cm across. No-op if the skin has one."""
    if MEL.get_material_property_input_node(skin, unreal.MaterialProperty.MP_WORLD_POSITION_OFFSET):
        return False
    wp = MEL.create_material_expression(skin, unreal.MaterialExpressionWorldPosition, -900, 900)
    pt = MEL.create_material_expression(skin, unreal.MaterialExpressionVectorParameter, -900, 1000)
    pt.set_editor_property("parameter_name", "RipplePoint")
    dist = MEL.create_material_expression(skin, unreal.MaterialExpressionDistance, -650, 950)
    MEL.connect_material_expressions(wp, "", dist, "A")
    MEL.connect_material_expressions(pt, "", dist, "B")
    params = {}
    for i, (name, default) in enumerate((("RippleTime", 100.0), ("RippleAmp", 0.0))):
        p = MEL.create_material_expression(skin, unreal.MaterialExpressionScalarParameter, -650, 1050 + 100 * i)
        p.set_editor_property("parameter_name", name)
        p.set_editor_property("default_value", default)
        params[name] = p
    normal = MEL.create_material_expression(skin, unreal.MaterialExpressionVertexNormalWS, -650, 1250)
    code = MEL.create_material_expression(skin, unreal.MaterialExpressionCustom, -350, 1000)
    code.set_editor_property("code", "float fall = saturate(1 - D / 14);\nreturn N * (A * exp(-T * 7) * sin(D * 0.7 - T * 45) * fall);")
    code.set_editor_property("output_type", unreal.CustomMaterialOutputType.CMOT_FLOAT3)
    ins = []
    for n in ("D", "T", "A", "N"):
        c = unreal.CustomInput()
        c.set_editor_property("input_name", n)
        ins.append(c)
    code.set_editor_property("inputs", ins)
    MEL.connect_material_expressions(dist, "", code, "D")
    MEL.connect_material_expressions(params["RippleTime"], "", code, "T")
    MEL.connect_material_expressions(params["RippleAmp"], "", code, "A")
    MEL.connect_material_expressions(normal, "", code, "N")
    MEL.connect_material_property(code, "", unreal.MaterialProperty.MP_WORLD_POSITION_OFFSET)
    MEL.recompile_material(skin)
    return True


WEAR = ("float f = A * saturate(1 - distance(P, F) / 9);\n"
        "float3 c = lerp(C, dot(C, float3(0.3, 0.59, 0.11)) * float3(0.7, 0.72, 0.75), T * 0.35);\n"
        "return lerp(c, c * float3(1.5, 0.55, 0.55), f);")


def add_wear(skin):
    """Base colour -> red flush within 9 cm of FlushPoint (Flush 0-1), greyer, darker skin (Tired 0-1). Refreshes the code if present."""
    prop = unreal.MaterialProperty.MP_BASE_COLOR
    src = MEL.get_material_property_input_node(skin, prop)
    if isinstance(src, unreal.MaterialExpressionCustom):
        if src.get_editor_property("code") == WEAR:
            return False
        src.set_editor_property("code", WEAR)
        MEL.recompile_material(skin)
        return True
    out = MEL.get_material_property_input_node_output_name(skin, prop)
    code = MEL.create_material_expression(skin, unreal.MaterialExpressionCustom, -150, 0)
    code.set_editor_property("code", WEAR)
    code.set_editor_property("output_type", unreal.CustomMaterialOutputType.CMOT_FLOAT3)
    ins = []
    for n in ("C", "P", "F", "A", "T"):
        c = unreal.CustomInput()
        c.set_editor_property("input_name", n)
        ins.append(c)
    code.set_editor_property("inputs", ins)
    MEL.connect_material_expressions(src, out, code, "C")
    wp = MEL.create_material_expression(skin, unreal.MaterialExpressionWorldPosition, -400, -200)
    MEL.connect_material_expressions(wp, "", code, "P")
    pt = MEL.create_material_expression(skin, unreal.MaterialExpressionVectorParameter, -400, -300)
    pt.set_editor_property("parameter_name", "FlushPoint")
    MEL.connect_material_expressions(pt, "", code, "F")
    for i, name in enumerate(("Flush", "Tired")):
        p = MEL.create_material_expression(skin, unreal.MaterialExpressionScalarParameter, -400, -500 + 100 * i)
        p.set_editor_property("parameter_name", name)
        MEL.connect_material_expressions(p, "", code, "AT"[i])
    MEL.connect_material_property(code, "", prop)
    MEL.recompile_material(skin)
    return True


CROWD_WPO = ("float h = frac(sin(dot(floor(P.xy / 45.0), float2(12.9898, 78.233))) * 43758.5453);\n"   # one seat ~45 cm
             "float j = max(0, sin(T * (7 + 4 * h) + h * 6.283));\n"
             "return float3(0, 0, R * step(0.4, h) * 7.0 * j * j);")                               # 60 % jump, up to 7 cm
CROWD_GLOW = ("float h = frac(sin(dot(floor(P.xy / 45.0), float2(39.346, 11.135))) * 43758.5453);\n"
              "return E + C * G * step(0.6, h) * (0.5 + 0.5 * sin(T * 21 + h * 40)) * float3(1, 0.95, 0.85);")


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


def add_crowd():
    """The crowd reacts (ASlapHUD::Crowd): Rise lifts ~60 % of the seats in a bouncing jump, Cheer flickers phone lights
    (CheerGlow per material: M_PhoneLight's lights, not the people). The glTF import made M_Crowd / M_PhoneLight instances
    of the engine's glTF master, so the nodes go on a copy of that master (/Game/FX/M_CrowdLive) and both are re-parented
    onto it; their own parameter values carry over by name."""
    insts = [ASSETS.load_asset(f"/Game/Arena/Env/Arena/Materials/{n}") for n in ("M_Crowd", "M_PhoneLight")]
    live = ASSETS.load_asset("/Game/FX/M_CrowdLive") if ASSETS.does_asset_exist("/Game/FX/M_CrowdLive") else None
    if live is None:
        master = insts[0].get_editor_property("parent")
        while isinstance(master, unreal.MaterialInstance):
            master = master.get_editor_property("parent")
        live = unreal.AssetToolsHelpers.get_asset_tools().duplicate_asset("M_CrowdLive", "/Game/FX", master)
        log(f"M_CrowdLive copied from {master.get_path_name()}")
    if not MEL.get_material_property_input_node(live, unreal.MaterialProperty.MP_WORLD_POSITION_OFFSET):
        wp = MEL.create_material_expression(live, unreal.MaterialExpressionWorldPosition, -900, 1400)
        t = MEL.create_material_expression(live, unreal.MaterialExpressionTime, -900, 1500)
        params = {}
        for i, (name, default) in enumerate((("Rise", 0.0), ("Cheer", 0.0), ("CheerGlow", 0.0))):
            p = MEL.create_material_expression(live, unreal.MaterialExpressionScalarParameter, -900, 1600 + 100 * i)
            p.set_editor_property("parameter_name", name)
            p.set_editor_property("default_value", default)
            params[name] = p
        bounce = custom(live, CROWD_WPO, ("P", "T", "R"), -500, 1400)
        for n, src in (("P", wp), ("T", t), ("R", params["Rise"])):
            MEL.connect_material_expressions(src, "", bounce, n)
        MEL.connect_material_property(bounce, "", unreal.MaterialProperty.MP_WORLD_POSITION_OFFSET)
        # the glTF master is Substrate: emissive is the front material's "emissive" input (its source a named reroute)
        front = MEL.get_material_property_input_node(live, unreal.MaterialProperty.MP_FRONT_MATERIAL)
        names = [str(n) for n in MEL.get_material_expression_input_names(front)]
        src = MEL.get_inputs_for_material_expression(live, front)[names.index("emissive")]
        glow = custom(live, CROWD_GLOW, ("E", "P", "T", "C", "G"), -500, 1700)
        for n, s in (("E", src), ("P", wp), ("T", t), ("C", params["Cheer"]), ("G", params["CheerGlow"])):
            MEL.connect_material_expressions(s, "", glow, n)
        MEL.connect_material_expressions(glow, "", front, "emissive")
        live.set_editor_property("max_world_position_offset_displacement", 10.0)   # Nanite culling bounds: the jump's height
        MEL.recompile_material(live)
        ASSETS.save_loaded_asset(live)
        log("M_CrowdLive: Rise (seat bounce) and Cheer (phone-light flicker)")
    for inst in insts:
        if inst.get_editor_property("parent") != live:
            MEL.set_material_instance_parent(inst, live)
        if inst.get_name() == "M_PhoneLight":
            MEL.set_material_instance_scalar_parameter_value(inst, "CheerGlow", 4.0)
        MEL.update_material_instance(inst)
        ASSETS.save_loaded_asset(inst)
    log("M_Crowd, M_PhoneLight -> M_CrowdLive")


def main():
    tools = unreal.AssetToolsHelpers.get_asset_tools()
    for name, loop in (("Slap", False), ("CrowdReact", False), ("CrowdLoop", True), ("Bell", False)):
        t = unreal.AssetImportTask()
        t.filename = os.path.join(PROJ, "Content/Art/Exports/Audio", f"{name}.wav")
        t.destination_path, t.destination_name = "/Game/Audio", f"S_{name}"
        t.automated, t.replace_existing, t.save = True, True, True
        tools.import_asset_tasks([t])
        w = ASSETS.load_asset(f"/Game/Audio/S_{name}")
        w.set_editor_property("looping", loop)
        ASSETS.save_loaded_asset(w)
        log(f"S_{name} (looping {loop})")

    if not ASSETS.does_asset_exist("/Game/FX/NS_SlapSpray"):   # loaded by path: commandlets don't scan plugin content
        src = unreal.load_asset("/Niagara/DefaultAssets/Templates/Systems/DirectionalBurst.DirectionalBurst")
        tools.duplicate_asset("NS_SlapSpray", "/Game/FX", src)
        ASSETS.save_asset("/Game/FX/NS_SlapSpray")
    log(f"NS_SlapSpray: {ASSETS.does_asset_exist('/Game/FX/NS_SlapSpray')}")

    skin = ASSETS.load_asset("/Game/Athletes/Grandpa/M_Grandpa")
    if add_ripple(skin) | add_wear(skin):
        ASSETS.save_loaded_asset(skin)
    log("cheek ripple and wear on M_Grandpa")
    add_crowd()


if __name__ == "__main__":
    main()
