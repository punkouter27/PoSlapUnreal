"""Unreal Editor Python: the broadcast's impact assets. Idempotent; build_scene.py calls add_ripple() on the skin it builds.

    training/.venv/Scripts/python.exe Scripts/broadcast_audio.py        (the WAVs, first)
    UnrealEditor-Cmd.exe PoSlapUnreal.uproject -run=pythonscript -script="<abs path>/Scripts/broadcast_fx.py" -unattended

/Game/Audio/S_* (broadcast_audio.py's WAVs; CrowdLoop and Breath loop), RV_Arena (the hall's reverb); /Game/FX/NS_SlapSpray (the engine's DirectionalBurst
template); the cheek ripple on M_Grandpa: world-position offset along the normal, a damped ring around RipplePoint driven
by RippleTime / RippleAmp (ASlapHUD sets them per athlete on a strike); wear on M_Grandpa's colour: a red flush around
FlushPoint (Flush, from neck damage), a palm print there (Print, along PrintU / PrintV: fingers, across the palm) and a
greyer skin (Tired, from stamina); M_Scuff, the deferred decal for sole and palm marks; LODs on every athlete; the crowd (/Game/FX/M_CrowdLive, the parent
of the arena's M_Crowd and M_PhoneLight): Rise bounces the seats, Cheer flickers the phone lights (ASlapHUD::Crowd).
Pass 3 (2026-11-04, the browser's gfx/sound pass 2): the new sounds (stage deck, PA chime, per-athlete grunts and
breaths), RV_ArenaIR (ArenaIR.wav as a convolution reverb submix), T_SkinDetail as a detail normal and a wet layer by
Tired, a Subsurface Profile (SSP_Skin) on the athletes, LOD0 CPU copies for the drawn-hand clamp. SLAP_SKIN_DETAIL=0 /
SLAP_SSS=0 leave those off.
Display only: MuJoCo owns every contact.
"""
import os
import unreal

PROJ = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir())
ASSETS = unreal.EditorAssetLibrary
MEL = unreal.MaterialEditingLibrary


def log(msg):
    unreal.log_warning(f"[broadcast_fx] {msg}")   # a warning: commandlets print nothing below Display


def add_ripple(skin):
    """A damped ring of world-position offset around RipplePoint, at most 14 cm across, PLUS a one-sided push along
    RippleDir: the cheek is deformed by the slap, not only rippled. An older ring (the first version) is rebuilt;
    a material with anything else in the offset is left alone."""
    prop = unreal.MaterialProperty.MP_WORLD_POSITION_OFFSET
    src = MEL.get_material_property_input_node(skin, prop)
    if src is not None:
        if not isinstance(src, unreal.MaterialExpressionCustom) or src.get_editor_property("code") != RIPPLE_OLD:
            return False
        old_inputs = [n for n in MEL.get_inputs_for_material_expression(skin, src) if n]
        MEL.delete_material_expression(skin, src)
        for n in old_inputs:
            MEL.delete_material_expression(skin, n)
    wp = MEL.create_material_expression(skin, unreal.MaterialExpressionWorldPosition, -900, 900)
    pt = MEL.create_material_expression(skin, unreal.MaterialExpressionVectorParameter, -900, 1000)
    pt.set_editor_property("parameter_name", "RipplePoint")
    dr = MEL.create_material_expression(skin, unreal.MaterialExpressionVectorParameter, -900, 1150)
    dr.set_editor_property("parameter_name", "RippleDir")
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
    code.set_editor_property("code", RIPPLE)
    code.set_editor_property("output_type", unreal.CustomMaterialOutputType.CMOT_FLOAT3)
    ins = []
    for n in ("D", "T", "A", "N", "W"):
        c = unreal.CustomInput()
        c.set_editor_property("input_name", n)
        ins.append(c)
    code.set_editor_property("inputs", ins)
    MEL.connect_material_expressions(dist, "", code, "D")
    MEL.connect_material_expressions(params["RippleTime"], "", code, "T")
    MEL.connect_material_expressions(params["RippleAmp"], "", code, "A")
    MEL.connect_material_expressions(normal, "", code, "N")
    MEL.connect_material_expressions(dr, "", code, "W")
    MEL.connect_material_property(code, "", prop)
    try:
        skin.set_editor_property("max_world_position_offset_displacement", 25.0)   # the cheek's own bounds
    except Exception:
        pass
    MEL.recompile_material(skin)
    return True


RIPPLE_OLD = "float fall = saturate(1 - D / 14);\nreturn N * (A * exp(-T * 7) * sin(D * 0.7 - T * 45) * fall);"
RIPPLE = ("float fall = saturate(1 - D / 14);\n"                                     # the ring, 14 cm across
          "float ring = A * exp(-T * 7) * sin(D * 0.7 - T * 45) * fall;\n"
          "float3 dir = normalize(W + float3(0, 0, 0.001));\n"                        # the slap's direction
          "float push = A * exp(-T * 9) * fall * fall * saturate(dot(N, dir));\n"
          "return N * ring + dir * push * 4.0;")                                       # the cheek is pushed along the slap


WEAR = ("float f = A * saturate(1 - distance(P, F) / 9);\n"
        "float3 d = P - F;\n"                                                          # the print, cm: x along the fingers
        "float x = dot(d, U), y = dot(d, V), z = dot(d, cross(U, V));\n"
        "float palm = 1 - smoothstep(0.8, 1.0, length(float2((x + 1.5) / 4.5, y / 4.0)));\n"
        "float fy = abs(frac((y + 4.0) / 2.0) - 0.5) * 2.0;\n"                          # four fingers, 2 cm apart
        "float fing = (1 - smoothstep(0.35, 0.6, fy)) * step(abs(y), 4.0) * step(1.5, x) * (1 - smoothstep(8.0, 9.5, x));\n"
        "float h = K * max(palm, fing) * saturate(1 - abs(z) / 3);\n"
        "float3 c = lerp(C, dot(C, float3(0.3, 0.59, 0.11)) * float3(0.7, 0.72, 0.75), T * 0.35);\n"
        "float3 w = lerp(c, c * float3(1.5, 0.5, 0.5), saturate(max(f, 0.85 * h)));\n"
        "return lerp(w, w * float3(1.45, 0.45, 0.45), saturate(M * Q));")              # the whole cheek (mask) reddens with damage
WEAR_IN = ("C", "P", "F", "A", "T", "U", "V", "K", "M", "Q")


def add_wear(skin, mask=None):
    """Base colour -> red flush within 9 cm of FlushPoint (Flush 0-1), a palm print there (Print 0-1), greyer, darker skin
    (Tired 0-1), and the whole cheek reddening by the assets stream's cheek mask (T_<Name>_CheekMask, red channel, UV0) x
    CheekFlush 0-1 (ASlapHUD: neck damage + bruise). No mask: a constant 0 (the term is off). An older wear node is rebuilt
    with its inputs kept; an up-to-date one is left alone."""
    prop = unreal.MaterialProperty.MP_BASE_COLOR
    src = MEL.get_material_property_input_node(skin, prop)
    old = {}
    if isinstance(src, unreal.MaterialExpressionCustom):
        if src.get_editor_property("code") == WEAR:
            return False
        names = [str(c.get_editor_property("input_name")) for c in src.get_editor_property("inputs")]
        for n, node in zip(names, MEL.get_inputs_for_material_expression(skin, src)):
            if node:
                old[n] = (node, MEL.get_input_node_output_name_for_material_expression(src, node))
        MEL.delete_material_expression(skin, src)
    else:
        old["C"] = (src, MEL.get_material_property_input_node_output_name(skin, prop))
    code = custom(skin, WEAR, WEAR_IN, -150, 0)
    if "P" not in old:
        old["P"] = (MEL.create_material_expression(skin, unreal.MaterialExpressionWorldPosition, -400, -200), "")
    for i, (n, name, kind) in enumerate((("F", "FlushPoint", unreal.MaterialExpressionVectorParameter),
                                         ("A", "Flush", unreal.MaterialExpressionScalarParameter),
                                         ("T", "Tired", unreal.MaterialExpressionScalarParameter),
                                         ("U", "PrintU", unreal.MaterialExpressionVectorParameter),
                                         ("V", "PrintV", unreal.MaterialExpressionVectorParameter),
                                         ("K", "Print", unreal.MaterialExpressionScalarParameter))):
        if n not in old:
            p = MEL.create_material_expression(skin, kind, -400, -800 + 100 * i)
            p.set_editor_property("parameter_name", name)
            old[n] = (p, "")
    if "M" not in old or mask is not None:
        if mask is not None:
            smp = MEL.create_material_expression(skin, unreal.MaterialExpressionTextureSample, -700, -1500)
            smp.set_editor_property("texture", mask)
            smp.set_editor_property("sampler_type", unreal.MaterialSamplerType.SAMPLERTYPE_MASKS)
            old["M"] = (smp, "R")
        else:
            zero = MEL.create_material_expression(skin, unreal.MaterialExpressionConstant, -400, -1500)
            old["M"] = (zero, "")
    if "Q" not in old:
        q = MEL.create_material_expression(skin, unreal.MaterialExpressionScalarParameter, -400, -1400)
        q.set_editor_property("parameter_name", "CheekFlush")
        old["Q"] = (q, "")
    for n in WEAR_IN:
        MEL.connect_material_expressions(old[n][0], old[n][1], code, n)
    MEL.connect_material_property(code, "", prop)
    MEL.recompile_material(skin)
    return True


SCUFF = ("float2 u = UV - 0.5;\n"
         "float n = frac(sin(dot(floor(UV * float2(5, 36)), float2(12.9898, 78.233))) * 43758.5453);\n"   # streaks along U
         "return saturate(1 - 2.2 * length(u * float2(1, 2.2))) * (0.3 + 0.7 * n) * 0.6;")


def add_scuff():
    """/Game/FX/M_Scuff: a dark rubber streak, deferred decal (ASlapHUD lays it where a sole skids or a palm braces)."""
    if ASSETS.does_asset_exist("/Game/FX/M_Scuff"):
        return
    m = unreal.AssetToolsHelpers.get_asset_tools().create_asset("M_Scuff", "/Game/FX", unreal.Material, unreal.MaterialFactoryNew())
    m.set_editor_property("material_domain", unreal.MaterialDomain.MD_DEFERRED_DECAL)
    m.set_editor_property("blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT)
    col = MEL.create_material_expression(m, unreal.MaterialExpressionConstant3Vector, -400, 0)
    col.set_editor_property("constant", unreal.LinearColor(0.025, 0.022, 0.02, 1))
    MEL.connect_material_property(col, "", unreal.MaterialProperty.MP_BASE_COLOR)
    uv = MEL.create_material_expression(m, unreal.MaterialExpressionTextureCoordinate, -600, 200)
    op = custom(m, SCUFF, ("UV",), -350, 200)
    op.set_editor_property("output_type", unreal.CustomMaterialOutputType.CMOT_FLOAT1)
    MEL.connect_material_expressions(uv, "", op, "UV")
    MEL.connect_material_property(op, "", unreal.MaterialProperty.MP_OPACITY)
    MEL.recompile_material(m)
    ASSETS.save_loaded_asset(m)
    log("M_Scuff (deferred decal)")


DECALS = ("T_Decal_SoleSkid_01", "T_Decal_SoleSkid_02", "T_Decal_PalmSmudge_01", "T_Decal_PodiumChip_01", "T_Decal_FloorScratch_01")


def add_decals():
    """/Game/FX/Decals: the assets stream's decal textures (Content/Art/Exports/Decals, straight alpha) and M_DecalTex, a
    deferred decal (Decal texture, Opacity and Roughness parameters). ASlapHUD lays them with a dynamic instance per mark on
    desktop; a phone (no DBuffer) skips decals."""
    tools = unreal.AssetToolsHelpers.get_asset_tools()
    for name in DECALS:
        src = os.path.join(PROJ, "Content/Art/Exports/Decals", f"{name}.png")
        if not os.path.isfile(src) or ASSETS.does_asset_exist(f"/Game/FX/Decals/{name}"):
            continue
        t = unreal.AssetImportTask()
        t.filename, t.destination_path, t.destination_name = src, "/Game/FX/Decals", name
        t.automated, t.replace_existing, t.save = True, True, True
        tools.import_asset_tasks([t])
        log(f"{name} imported")
    if ASSETS.does_asset_exist("/Game/FX/M_DecalTex") or not ASSETS.does_asset_exist(f"/Game/FX/Decals/{DECALS[0]}"):
        return
    m = tools.create_asset("M_DecalTex", "/Game/FX", unreal.Material, unreal.MaterialFactoryNew())
    m.set_editor_property("material_domain", unreal.MaterialDomain.MD_DEFERRED_DECAL)
    m.set_editor_property("blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT)
    tex = MEL.create_material_expression(m, unreal.MaterialExpressionTextureSampleParameter2D, -600, 0)
    tex.set_editor_property("parameter_name", "Decal")
    tex.set_editor_property("texture", ASSETS.load_asset(f"/Game/FX/Decals/{DECALS[0]}"))
    op = MEL.create_material_expression(m, unreal.MaterialExpressionScalarParameter, -600, 250)
    op.set_editor_property("parameter_name", "Opacity")
    op.set_editor_property("default_value", 1.0)
    rough = MEL.create_material_expression(m, unreal.MaterialExpressionScalarParameter, -300, 350)
    rough.set_editor_property("parameter_name", "Roughness")
    rough.set_editor_property("default_value", 0.8)
    mul = MEL.create_material_expression(m, unreal.MaterialExpressionMultiply, -300, 200)
    MEL.connect_material_expressions(tex, "A", mul, "A")
    MEL.connect_material_expressions(op, "", mul, "B")
    MEL.connect_material_property(tex, "RGB", unreal.MaterialProperty.MP_BASE_COLOR)
    MEL.connect_material_property(mul, "", unreal.MaterialProperty.MP_OPACITY)
    MEL.connect_material_property(rough, "", unreal.MaterialProperty.MP_ROUGHNESS)
    MEL.recompile_material(m)
    ASSETS.save_loaded_asset(m)
    log("M_DecalTex (deferred decal, textured)")


def add_reverb():
    """/Game/Audio/RV_Arena: the big hall (wide shots) and /Game/Audio/RV_Ringside: a tight, dry room (close-ups).
    ASlapHUD activates both and blends them by the camera's distance to the contact."""
    for name, settings in (
        ("RV_Arena", (("density", 1.0), ("diffusion", 1.0), ("gain", 0.4), ("gain_hf", 0.6), ("decay_time", 1.8),
                      ("decay_hf_ratio", 0.7), ("reflections_gain", 0.3), ("reflections_delay", 0.02), ("late_gain", 1.4), ("late_delay", 0.035))),
        ("RV_Ringside", (("density", 1.0), ("diffusion", 0.55), ("gain", 0.22), ("gain_hf", 0.35), ("decay_time", 0.42),
                         ("decay_hf_ratio", 0.5), ("reflections_gain", 0.12), ("reflections_delay", 0.006), ("late_gain", 0.55), ("late_delay", 0.018)))):
        path = f"/Game/Audio/{name}"
        rv = ASSETS.load_asset(path) if ASSETS.does_asset_exist(path) else unreal.AssetToolsHelpers.get_asset_tools().create_asset(
            name, "/Game/Audio", unreal.ReverbEffect, unreal.ReverbEffectFactory())
        for k, v in settings:
            rv.set_editor_property(k, v)
        ASSETS.save_loaded_asset(rv)
        log(name)


def add_dust():
    """/Game/FX/NS_DustPuff: the puff where a body part (not a sole, not a palm) hit the ground. A copy of the
    engine's radial burst template; ASlapHUD spawns it at the contact point the controller publishes."""
    if ASSETS.does_asset_exist("/Game/FX/NS_DustPuff"):
        return
    src = unreal.load_asset("/Niagara/DefaultAssets/Templates/Systems/RadialBurst.RadialBurst")
    if not src:
        log("NS_DustPuff: the radial burst template is missing")
        return
    unreal.AssetToolsHelpers.get_asset_tools().duplicate_asset("NS_DustPuff", "/Game/FX", src)
    ASSETS.save_asset("/Game/FX/NS_DustPuff")
    log("NS_DustPuff (the dust puff)")


def add_lods(mesh):
    """Engine-reduced LODs 1-2 (half, quarter the triangles): the wide and crane shots on a phone. No-op if it has them."""
    sub = unreal.get_editor_subsystem(unreal.SkeletalMeshEditorSubsystem)
    if sub.get_lod_count(mesh) >= 3:
        return False
    sub.regenerate_lod(mesh, 3, False, False)
    n = sub.get_lod_count(mesh)
    log(f"{mesh.get_name()}: {n} LODs, verts " + ", ".join(str(sub.get_num_verts(mesh, i)) for i in range(n)))
    return True


CROWD_WPO_OLD = ("float h = frac(sin(dot(floor(P.xy / 45.0), float2(12.9898, 78.233))) * 43758.5453);\n"   # one seat ~45 cm
                  "float j = max(0, sin(T * (7 + 4 * h) + h * 6.283));\n"
                  "return float3(0, 0, R * step(0.4, h) * 7.0 * j * j);")                               # 60 % jump, up to 7 cm
CROWD_WPO = ("float h = frac(sin(dot(floor(P.xy / 45.0), float2(12.9898, 78.233))) * 43758.5453);\n"   # one seat ~45 cm
             "float j = max(0, sin(T * (7 + 4 * h) + h * 6.283));\n"
             "float cut = step(1.0 - C, h) * 400.0;\n"                                                # the seats a low tier drops
             "return float3(0, 0, R * step(0.4, h) * 7.0 * j * j - cut);")
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


def build_crowd_wpo(live):
    """The crowd's seat bounce (Rise) and the seats a low tier drops (CrowdCut): the cut seats sink under the floor."""
    wp = MEL.create_material_expression(live, unreal.MaterialExpressionWorldPosition, -900, 1400)
    t = MEL.create_material_expression(live, unreal.MaterialExpressionTime, -900, 1500)
    params = {}
    for i, (name, default) in enumerate((("Rise", 0.0), ("Cheer", 0.0), ("CheerGlow", 0.0), ("CrowdCut", 0.0))):
        p = MEL.create_material_expression(live, unreal.MaterialExpressionScalarParameter, -900, 1600 + 100 * i)
        p.set_editor_property("parameter_name", name)
        p.set_editor_property("default_value", default)
        params[name] = p
    bounce = custom(live, CROWD_WPO, ("P", "T", "R", "C"), -500, 1400)
    for n, src in (("P", wp), ("T", t), ("R", params["Rise"]), ("C", params["CrowdCut"])):
        MEL.connect_material_expressions(src, "", bounce, n)
    MEL.connect_material_property(bounce, "", unreal.MaterialProperty.MP_WORLD_POSITION_OFFSET)
    return wp, t, params


def add_crowd():
    """The crowd reacts (ASlapHUD::Crowd): Rise lifts ~60 % of the seats in a bouncing jump, Cheer flickers phone lights
    (CheerGlow per material: M_PhoneLight's lights, not the people), and CrowdCut drops a share of the seats (the low
    quality tier). The glTF import made M_Crowd / M_PhoneLight instances
    of the engine's glTF master, so the nodes go on a copy of that master (/Game/FX/M_CrowdLive) and both are re-parented
    onto it; their own parameter values carry over by name."""
    insts = [ASSETS.load_asset(f"/Game/Arena/Env/Arena/Materials/{n}") for n in ("MI_Crowd", "MI_PhoneLight")]
    live = ASSETS.load_asset("/Game/FX/M_CrowdLive") if ASSETS.does_asset_exist("/Game/FX/M_CrowdLive") else None
    if live is None:
        master = insts[0].get_editor_property("parent")
        while isinstance(master, unreal.MaterialInstance):
            master = master.get_editor_property("parent")
        live = unreal.AssetToolsHelpers.get_asset_tools().duplicate_asset("M_CrowdLive", "/Game/FX", master)
        log(f"M_CrowdLive copied from {master.get_path_name()}")
    wpo = MEL.get_material_property_input_node(live, unreal.MaterialProperty.MP_WORLD_POSITION_OFFSET)
    if wpo is None:
        wp, t, params = build_crowd_wpo(live)
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
    elif isinstance(wpo, unreal.MaterialExpressionCustom) and wpo.get_editor_property("code") == CROWD_WPO_OLD:
        # the older bounce: rebuild it with the crowd cut
        old_inputs = [n for n in MEL.get_inputs_for_material_expression(live, wpo) if n]
        MEL.delete_material_expression(live, wpo)
        for n in old_inputs:
            MEL.delete_material_expression(live, n)
        build_crowd_wpo(live)
        live.set_editor_property("max_world_position_offset_displacement", 10.0)
        MEL.recompile_material(live)
        ASSETS.save_loaded_asset(live)
        log("M_CrowdLive: rebuilt with CrowdCut (the low tier drops seats)")
    for inst in insts:
        if inst.get_editor_property("parent") != live:
            MEL.set_material_instance_parent(inst, live)
        if inst.get_name() == "M_PhoneLight":
            MEL.set_material_instance_scalar_parameter_value(inst, "CheerGlow", 4.0)
        MEL.update_material_instance(inst)
        ASSETS.save_loaded_asset(inst)
    log("M_Crowd, M_PhoneLight -> M_CrowdLive")


# sound pass 2 (2026-11-03, Web/audio.js): the hard stage deck, the PA chime, each athlete's grunt and breath
SOUNDS = ("Slap", "Slap1", "Slap2", "Slap3", "Thud", "Squeak0", "Squeak1", "Scrape", "Step", "Breath", "CrowdReact", "CrowdNear",
          "CrowdLoop", "Bell", "StepDeck", "PAChime", "Grunt_grandpa", "Grunt_kim", "Grunt_matt", "Breath_kim", "Breath_matt")


def add_arena_ir():
    """/Game/Audio/RV_ArenaIR: a submix whose one effect is a convolution reverb on broadcast_audio.py's ArenaIR.wav (the
    floor, the stands, the roof; stereo). ASlapHUD's Ringside attenuation sends every spatialised one-shot to it on
    desktop (Web/audio.js's ConvolverNode); a phone keeps RV_Arena / RV_Ringside. The impulse response's sample array is
    not an editable property, so unreal.SlapEditorLib (SlapEditorLib.cpp) fills it."""
    import array
    import wave
    src = os.path.join(PROJ, "Content/Art/Exports/Audio", "ArenaIR.wav")
    if not os.path.isfile(src):
        log("RV_ArenaIR: no ArenaIR.wav")
        return
    with wave.open(src, "rb") as w:
        ch, rate, width, n = w.getnchannels(), w.getframerate(), w.getsampwidth(), w.getnframes()
        raw = w.readframes(n)
    if width != 2:
        log(f"RV_ArenaIR: {width * 8}-bit WAV, expected 16")
        return
    samples = [x / 32768.0 for x in array.array("h", raw)]
    tools = unreal.AssetToolsHelpers.get_asset_tools()

    def make(name, cls, factory):
        path = f"/Game/Audio/{name}"
        return ASSETS.load_asset(path) if ASSETS.does_asset_exist(path) else tools.create_asset(name, "/Game/Audio", cls, factory)

    ir = make("RV_ArenaIR_Response", unreal.AudioImpulseResponse, unreal.AudioImpulseResponseFactory())
    if not unreal.SlapEditorLib.fill_impulse_response(ir, samples, ch, rate, -12.0):
        log("RV_ArenaIR: the impulse response was not filled")
        return
    ASSETS.save_loaded_asset(ir)
    preset = make("RV_ArenaIR_Convolution", unreal.SubmixEffectConvolutionReverbPreset, None)
    preset.set_editor_property("impulse_response", ir)
    st = preset.get_editor_property("settings")
    st.set_editor_property("wet_volume_db", 0.0)    # a send bus: all wet, the dry stays on the master
    st.set_editor_property("dry_volume_db", -96.0)
    preset.set_editor_property("settings", st)
    try:
        preset.set_settings(st)
    except Exception:
        pass
    ASSETS.save_loaded_asset(preset)
    sm = make("RV_ArenaIR", unreal.SoundSubmix, unreal.SoundSubmixFactory())
    sm.set_editor_property("submix_effect_chain", [preset])
    ASSETS.save_loaded_asset(sm)
    log(f"RV_ArenaIR: convolution reverb, {ch} ch, {rate} Hz, {n / rate:.2f} s")


SKIN_DETAIL_TEX = "/Game/Athletes/T_SkinDetail"


def skin_detail_texture():
    """T_SkinDetail (Scripts/blender_skin_detail.py): R height, G sweat beads; linear (sRGB off), tileable."""
    if not ASSETS.does_asset_exist(SKIN_DETAIL_TEX):
        src = os.path.join(PROJ, "Content/Art/Exports/Textures/T_SkinDetail.png")
        if not os.path.isfile(src):
            return None
        t = unreal.AssetImportTask()
        t.filename, t.destination_path, t.destination_name = src, "/Game/Athletes", "T_SkinDetail"
        t.automated, t.replace_existing, t.save = True, True, True
        unreal.AssetToolsHelpers.get_asset_tools().import_asset_tasks([t])
    tex = ASSETS.load_asset(SKIN_DETAIL_TEX)
    tex.set_editor_property("srgb", False)
    tex.set_editor_property("compression_settings", unreal.TextureCompressionSettings.TC_MASKS)
    tex.set_editor_property("address_x", unreal.TextureAddress.TA_WRAP)
    tex.set_editor_property("address_y", unreal.TextureAddress.TA_WRAP)
    ASSETS.save_loaded_asset(tex)
    return tex


# Web/fx.js's skin, in Unreal: the detail map sampled triplanar in the bind pose (pre-skinned local position, cm;
# ~22 tiles per metre = 0.22 per cm), the height turned into a normal by screen-space derivatives (surface gradient),
# and that detail put on top of the scan's own normal map (tangent space).
TRIPLANAR = ("float3 p = P * 0.22;\n"
             "float3 bw = pow(abs(L), 4.0); bw /= max(bw.x + bw.y + bw.z, 1e-4);\n"
             "float2 d = Texture2DSample(Tex, TexSampler, p.yz).rg * bw.x + Texture2DSample(Tex, TexSampler, p.zx).rg * bw.y\n"
             "         + Texture2DSample(Tex, TexSampler, p.xy).rg * bw.z;\n")
SKIN_DETAIL = (TRIPLANAR +
               "float h = d.r * 0.04;\n"                                                    # cm of relief: pores, fine creases
               "float3 n = normalize(N);\n"
               "float3 dpdx = ddx(W), dpdy = ddy(W);\n"
               "float3 r1 = cross(dpdy, n), r2 = cross(n, dpdx);\n"
               "float det = dot(dpdx, r1);\n"
               "float3 g = sign(det) * (ddx(h) * r1 + ddy(h) * r2);\n"
               "float3 nb = normalize(abs(det) * n - g);\n"
               "float3 t = mul((float3x3)Parameters.TangentToWorld, nb);\n"                   # world -> tangent (orthonormal)
               "return normalize(float3(B.xy + t.xy, B.z));")
SKIN_DETAIL_IN = ("P", "L", "Tex", "N", "W", "B")
# the sweat (Web/fx.js gSweat): wetter as the stamina goes (Tired 0-1), beading on the map's G; a wet skin is glossy
SKIN_WET = (TRIPLANAR +
            "float wet = (0.15 + 0.85 * T) * (0.25 + 0.75 * smoothstep(0.5, 0.85, d.g));\n"
            "return lerp(R, 0.12, saturate(wet) * 0.8);")
SKIN_WET_IN = ("P", "L", "Tex", "T", "R")


def expr(mat, names, x, y):
    for n in names:   # class names moved across engine versions
        cls = getattr(unreal, n, None)
        if cls:
            return MEL.create_material_expression(mat, cls, x, y)
    return None


def add_skin_detail(skin, tex):
    """Skin detail normal (T_SkinDetail R) and the wet layer (G, by Tired) on M_<athlete>. Idempotent."""
    nprop, rprop = unreal.MaterialProperty.MP_NORMAL, unreal.MaterialProperty.MP_ROUGHNESS
    nsrc = MEL.get_material_property_input_node(skin, nprop)
    if isinstance(nsrc, unreal.MaterialExpressionCustom) and nsrc.get_editor_property("code") == SKIN_DETAIL:
        return False
    nout = MEL.get_material_property_input_node_output_name(skin, nprop) if nsrc else ""
    rsrc = MEL.get_material_property_input_node(skin, rprop)
    rout = MEL.get_material_property_input_node_output_name(skin, rprop) if rsrc else ""
    pre_p = expr(skin, ("MaterialExpressionPreSkinnedPosition", "MaterialExpressionPreSkinnedLocalPosition"), -1400, 1600)
    pre_n = expr(skin, ("MaterialExpressionPreSkinnedNormal", "MaterialExpressionPreSkinnedLocalNormal"), -1400, 1700)
    if pre_p is None or pre_n is None:
        log("skin detail: no pre-skinned position node in this engine")
        return False
    vi_p = MEL.create_material_expression(skin, unreal.MaterialExpressionVertexInterpolator, -1200, 1600)   # vertex -> pixel
    vi_n = MEL.create_material_expression(skin, unreal.MaterialExpressionVertexInterpolator, -1200, 1700)
    MEL.connect_material_expressions(pre_p, "", vi_p, "VS")
    MEL.connect_material_expressions(pre_n, "", vi_n, "VS")
    to = MEL.create_material_expression(skin, unreal.MaterialExpressionTextureObject, -1200, 1800)
    to.set_editor_property("texture", tex)
    to.set_editor_property("sampler_type", unreal.MaterialSamplerType.SAMPLERTYPE_MASKS)
    wn = MEL.create_material_expression(skin, unreal.MaterialExpressionVertexNormalWS, -1200, 1900)
    wp = MEL.create_material_expression(skin, unreal.MaterialExpressionWorldPosition, -1200, 2000)
    try:   # camera-relative: the derivatives of a small number
        wp.set_editor_property("world_position_shader_offset", unreal.WorldPositionIncludedOffsets.WPT_CAMERA_RELATIVE_WORLD_POSITION)
    except Exception as e:
        log(f"skin detail: world position stays absolute ({e})")
    if nsrc is None:
        flat = MEL.create_material_expression(skin, unreal.MaterialExpressionConstant3Vector, -1200, 2100)
        flat.set_editor_property("constant", unreal.LinearColor(0, 0, 1, 1))
        nsrc, nout = flat, ""
    det = custom(skin, SKIN_DETAIL, SKIN_DETAIL_IN, -800, 1700)
    for n, (node, out) in zip(SKIN_DETAIL_IN, ((vi_p, ""), (vi_n, ""), (to, ""), (wn, ""), (wp, ""), (nsrc, nout))):
        MEL.connect_material_expressions(node, out, det, n)
    MEL.connect_material_property(det, "", nprop)
    if rsrc is not None:
        tired = None
        base = MEL.get_material_property_input_node(skin, unreal.MaterialProperty.MP_BASE_COLOR)
        for e in (MEL.get_inputs_for_material_expression(skin, base) if base else []):
            if isinstance(e, unreal.MaterialExpressionScalarParameter) and str(e.get_editor_property("parameter_name")) == "Tired":
                tired = e
        if tired is None:
            tired = MEL.create_material_expression(skin, unreal.MaterialExpressionScalarParameter, -1200, 2200)
            tired.set_editor_property("parameter_name", "Tired")
        wet = custom(skin, SKIN_WET, SKIN_WET_IN, -800, 2000)
        wet.set_editor_property("output_type", unreal.CustomMaterialOutputType.CMOT_FLOAT1)
        for n, (node, out) in zip(SKIN_WET_IN, ((vi_p, ""), (vi_n, ""), (to, ""), (tired, ""), (rsrc, rout))):
            MEL.connect_material_expressions(node, out, wet, n)
        MEL.connect_material_property(wet, "", rprop)
    return True


def add_subsurface(skin):
    """Skin subsurface (Web/fx.js sss, ultra/cinema): a Subsurface Profile on the athlete material, /Game/Athletes/SSP_Skin.
    Desktop draws it (scalability turns it down with the post quality); a phone's mobile renderer falls back to default lit."""
    path = "/Game/Athletes/SSP_Skin"
    if ASSETS.does_asset_exist(path):
        prof = ASSETS.load_asset(path)
    else:
        prof = unreal.AssetToolsHelpers.get_asset_tools().create_asset("SSP_Skin", "/Game/Athletes", unreal.SubsurfaceProfile, unreal.SubsurfaceProfileFactory())
        ASSETS.save_loaded_asset(prof)
    if skin.get_editor_property("shading_model") == unreal.MaterialShadingModel.MSM_SUBSURFACE_PROFILE and skin.get_editor_property("subsurface_profile") == prof:
        return False
    skin.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_SUBSURFACE_PROFILE)
    skin.set_editor_property("subsurface_profile", prof)
    return True


def main():
    tools = unreal.AssetToolsHelpers.get_asset_tools()
    for name in SOUNDS:
        if not os.path.isfile(os.path.join(PROJ, "Content/Art/Exports/Audio", f"{name}.wav")):
            log(f"S_{name}: no WAV (run broadcast_audio.py)")
            continue
        loop = name in ("CrowdLoop", "Breath") or name.startswith("Breath_")
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
    add_dust()
    log(f"NS_DustPuff: {ASSETS.does_asset_exist('/Game/FX/NS_DustPuff')}")

    detail = skin_detail_texture()
    for name in sorted(athlete_names()):
        skin, mesh = f"/Game/Athletes/M_{name}", f"/Game/Athletes/SK_{name}"
        if not ASSETS.does_asset_exist(skin):   # the per-athlete folder layout
            skin, mesh = f"/Game/Athletes/{name}/M_{name}", f"/Game/Athletes/{name}/SK_{name}"
        if not ASSETS.does_asset_exist(mesh):   # an overlay material (M_TeamOverlay), not an athlete
            continue
        if ASSETS.does_asset_exist(skin):
            skin = ASSETS.load_asset(skin)
            changed = add_ripple(skin) | add_wear(skin)
            if detail is not None and os.environ.get("SLAP_SKIN_DETAIL", "1") != "0":
                changed |= add_skin_detail(skin, detail)
            if os.environ.get("SLAP_SSS", "1") != "0":
                changed |= add_subsurface(skin)
            if changed:
                MEL.recompile_material(skin)
                ASSETS.save_loaded_asset(skin)
            log(f"cheek ripple, wear, print, skin detail, wet and subsurface on M_{name}")
        if ASSETS.does_asset_exist(mesh):
            mesh = ASSETS.load_asset(mesh)
            changed = add_lods(mesh)
            if unreal.SlapEditorLib.set_mesh_cpu_access(mesh, True):   # the drawn-hand clamp reads LOD0 (SlapAthleteVisual.cpp)
                changed = True
                log(f"{mesh.get_name()}: LOD0 keeps a CPU copy")
            if changed:
                ASSETS.save_loaded_asset(mesh)
    add_scuff()
    add_decals()
    add_reverb()
    add_arena_ir()
    add_crowd()


def athlete_names():
    """Every athlete in /Game/Athletes, from the M_<name> / SK_<name> pairs (the folder layout and the flat one)."""
    names = set()
    for asset in ASSETS.list_assets("/Game/Athletes", recursive=True):
        base = asset.split(".")[0].rsplit("/", 1)[-1]
        if base.startswith("M_"):
            names.add(base[2:])
        elif base.startswith("SK_"):
            names.add(base[3:])
    return names


if __name__ == "__main__":
    main()
