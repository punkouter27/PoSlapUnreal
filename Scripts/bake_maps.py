"""Unreal Editor Python: the assets stream's baked maps (Scripts/blender_batch.py) wired into the athlete and arena
materials. Idempotent.

    UnrealEditor-Cmd.exe PoSlapUnreal.uproject -run=pythonscript -script="<abs path>/Scripts/bake_maps.py" -unattended

Athletes (UV0, the scan's own layout): Content/Art/Exports/<Name>_tex/T_<Name>_AO.png on M_<Name>'s Ambient Occlusion
input; T_<Name>_Cavity.png (0.5 = flat, below it a crease) darkens the base colour by up to 12 % and roughens it by up
to 0.1 in the creases. Arena props (BakeUV = UV1): Content/Art/Exports/Textures/Bake/T_<Prop>_AO / _Cavity on a copy of
the glTF master, /Game/Arena/M_ArenaBaked (BakeAO, BakeCavity texture parameters sampled on UV1), one material instance
per prop and slot (MI_<material>_<Prop>, the original's parameters kept) on the prop's mesh. The crowd keeps
M_CrowdLive (its own copy of the master). Cavity is desktop only (a Feature Level Switch passes ES3.1 through); a phone
keeps the AO. The phone arena (Arena_Mobile.glb) is untouched: palette atlas, no BakeUV.
Display only.
"""
import os

import unreal

PROJ = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir())
ASSETS = unreal.EditorAssetLibrary
MEL = unreal.MaterialEditingLibrary
TOOLS = unreal.AssetToolsHelpers.get_asset_tools()
ENV = "/Game/Arena/Env/Arena"
TAG = "bake_maps"   # the description on every node this script adds: a material that has one is done


def log(msg):
    unreal.log_warning(f"[bake_maps] {msg}")


def texture(src, folder, name, group):
    path = f"{folder}/{name}"
    if not ASSETS.does_asset_exist(path):
        if not os.path.isfile(src):
            return None
        t = unreal.AssetImportTask()
        t.filename, t.destination_path, t.destination_name = src, folder, name
        t.automated, t.replace_existing, t.save = True, True, True
        TOOLS.import_asset_tasks([t])
    tex = ASSETS.load_asset(path)
    tex.set_editor_property("srgb", False)
    tex.set_editor_property("compression_settings", unreal.TextureCompressionSettings.TC_MASKS)
    tex.set_editor_property("lod_group", group)
    ASSETS.save_loaded_asset(tex)
    return tex


def node(mat, cls, x, y):
    n = MEL.create_material_expression(mat, cls, x, y)
    n.set_editor_property("desc", TAG)
    return n


def custom(mat, code, inputs, x, y):
    c = node(mat, unreal.MaterialExpressionCustom, x, y)
    c.set_editor_property("code", code)
    c.set_editor_property("output_type", unreal.CustomMaterialOutputType.CMOT_FLOAT3)
    ins = []
    for n in inputs:
        ci = unreal.CustomInput()
        ci.set_editor_property("input_name", n)
        ins.append(ci)
    c.set_editor_property("inputs", ins)
    return c


CAV_BASE = "float crease = saturate((0.5 - K) * 2.0);\nreturn C * (1.0 - 0.12 * crease);"
CAV_ROUGH = "float crease = saturate((0.5 - K) * 2.0);\nreturn saturate(R + 0.1 * crease);"


def desktop_only(mat, full, src, src_out, x, y):
    """A Feature Level Switch: the full expression by default, the plain input on ES3.1 (phones)."""
    sw = node(mat, unreal.MaterialExpressionFeatureLevelSwitch, x, y)
    MEL.connect_material_expressions(full, "", sw, "Default")
    MEL.connect_material_expressions(src, src_out, sw, "ES3_1")
    return sw


def done(mat):
    for prop in (unreal.MaterialProperty.MP_AMBIENT_OCCLUSION, unreal.MaterialProperty.MP_BASE_COLOR):
        n = MEL.get_material_property_input_node(mat, prop)
        if n is not None and n.get_editor_property("desc") == TAG:
            return True
    return False


def athlete(name):
    folder = f"/Game/Athletes/{name}"
    skin = ASSETS.load_asset(f"{folder}/M_{name}") if ASSETS.does_asset_exist(f"{folder}/M_{name}") else None
    if skin is None:
        return
    if done(skin):
        log(f"M_{name}: already wired")
        return
    src = os.path.join(PROJ, "Content/Art/Exports", f"{name}_tex")
    ao = texture(os.path.join(src, f"T_{name}_AO.png"), f"{folder}/Textures", f"T_{name}_AO", unreal.TextureGroup.TEXTUREGROUP_CHARACTER)
    cav = texture(os.path.join(src, f"T_{name}_Cavity.png"), f"{folder}/Textures", f"T_{name}_Cavity", unreal.TextureGroup.TEXTUREGROUP_CHARACTER)
    if ao is None:
        log(f"M_{name}: no T_{name}_AO.png")
        return
    a = node(skin, unreal.MaterialExpressionTextureSample, -900, 2400)
    a.set_editor_property("texture", ao)
    a.set_editor_property("sampler_type", unreal.MaterialSamplerType.SAMPLERTYPE_MASKS)
    MEL.connect_material_property(a, "R", unreal.MaterialProperty.MP_AMBIENT_OCCLUSION)
    if cav is not None:
        k = node(skin, unreal.MaterialExpressionTextureSample, -900, 2600)
        k.set_editor_property("texture", cav)
        k.set_editor_property("sampler_type", unreal.MaterialSamplerType.SAMPLERTYPE_MASKS)
        for prop, code, x in ((unreal.MaterialProperty.MP_BASE_COLOR, CAV_BASE, 150), (unreal.MaterialProperty.MP_ROUGHNESS, CAV_ROUGH, 150)):
            src_node = MEL.get_material_property_input_node(skin, prop)
            if src_node is None:
                continue
            out = MEL.get_material_property_input_node_output_name(skin, prop)
            c = custom(skin, code, ("C", "K") if prop == unreal.MaterialProperty.MP_BASE_COLOR else ("R", "K"), x, 2400 if prop == unreal.MaterialProperty.MP_BASE_COLOR else 2600)
            if prop == unreal.MaterialProperty.MP_ROUGHNESS:
                c.set_editor_property("output_type", unreal.CustomMaterialOutputType.CMOT_FLOAT1)
            MEL.connect_material_expressions(src_node, out, c, "C" if prop == unreal.MaterialProperty.MP_BASE_COLOR else "R")
            MEL.connect_material_expressions(k, "R", c, "K")
            MEL.connect_material_property(desktop_only(skin, c, src_node, out, x + 250, 2500), "", prop)
    MEL.recompile_material(skin)
    ASSETS.save_loaded_asset(skin)
    log(f"M_{name}: AO on Ambient Occlusion, cavity on base colour and roughness (desktop)")


def master_of(mi):
    m = mi
    while isinstance(m, unreal.MaterialInstance):
        m = m.get_editor_property("parent")
    return m


def baked_master(template):
    """/Game/Arena/M_ArenaBaked: the glTF master with BakeAO x its occlusion, BakeCavity on its base colour (UV1)."""
    path = "/Game/Arena/M_ArenaBaked"
    if ASSETS.does_asset_exist(path):
        return ASSETS.load_asset(path)
    mat = TOOLS.duplicate_asset("M_ArenaBaked", "/Game/Arena", template)
    uv = node(mat, unreal.MaterialExpressionTextureCoordinate, -1600, 2400)
    uv.set_editor_property("coordinate_index", 1)
    white = unreal.load_asset("/Engine/EngineResources/WhiteSquareTexture")
    grey = unreal.load_asset("/Engine/EngineMaterials/Grey") or white   # a flat cavity (0.5) by default
    samples = {}
    for name, default, y in (("BakeAO", white, 2400), ("BakeCavity", grey, 2600)):
        s = node(mat, unreal.MaterialExpressionTextureSampleParameter2D, -1300, y)
        s.set_editor_property("parameter_name", name)
        s.set_editor_property("texture", default)
        s.set_editor_property("sampler_type", unreal.MaterialSamplerType.SAMPLERTYPE_MASKS)
        MEL.connect_material_expressions(uv, "", s, "UVs")
        samples[name] = s
    front = MEL.get_material_property_input_node(mat, unreal.MaterialProperty.MP_FRONT_MATERIAL)
    names = [str(n) for n in MEL.get_material_expression_input_names(front)] if front else []
    log(f"M_ArenaBaked: front material inputs {names}")
    ins = MEL.get_inputs_for_material_expression(mat, front) if front else []

    def wrap(input_name, code, ins_names):
        if input_name not in names:
            return False
        i = names.index(input_name)
        src = ins[i]
        out = MEL.get_input_node_output_name_for_material_expression(front, src) if src else ""
        c = custom(mat, code, ins_names, -400, 2400 + 200 * len(names) % 3)
        if src is None:
            one = node(mat, unreal.MaterialExpressionConstant, -700, 2500)
            one.set_editor_property("r", 1.0)
            src, out = one, ""
        MEL.connect_material_expressions(src, out, c, ins_names[0])
        MEL.connect_material_expressions(samples["BakeAO" if "A" in ins_names else "BakeCavity"], "R", c, ins_names[1])
        full = c
        if ins_names[1] == "K":
            full = desktop_only(mat, c, src, out, -150, 2500)
        MEL.connect_material_expressions(full, "", front, input_name)
        return True

    occ = next((n for n in names if n.lower() in ("occlusion", "ambient_occlusion", "ambientocclusion", "ao")), None)
    base = next((n for n in names if n.lower() in ("base_color", "basecolor", "diffuse_albedo", "albedo")), None)
    ok_ao = occ is not None and wrap(occ, "return O * A;", ("O", "A"))
    ok_cav = base is not None and wrap(base, CAV_BASE, ("C", "K"))
    log(f"M_ArenaBaked: AO {'on ' + occ if ok_ao else 'not wired (no occlusion input)'}, cavity {'on ' + base if ok_cav else 'not wired'}")
    MEL.recompile_material(mat)
    ASSETS.save_loaded_asset(mat)
    return mat


def arena():
    bake = os.path.join(PROJ, "Content/Art/Exports/Textures/Bake")
    meshes = [m for m in (ASSETS.load_asset(p.split(".")[0]) for p in ASSETS.list_assets(f"{ENV}/StaticMeshes", recursive=False))
              if isinstance(m, unreal.StaticMesh)]
    master = None
    wired = 0
    for mesh in meshes:
        prop = mesh.get_name().removeprefix("SM_")
        if prop == "Crowd":
            continue   # M_CrowdLive (broadcast_fx.add_crowd) drives the crowd
        ao = texture(os.path.join(bake, f"T_{prop}_AO.png"), f"{ENV}/Bake", f"T_{prop}_AO", unreal.TextureGroup.TEXTUREGROUP_WORLD)
        cav = texture(os.path.join(bake, f"T_{prop}_Cavity.png"), f"{ENV}/Bake", f"T_{prop}_Cavity", unreal.TextureGroup.TEXTUREGROUP_WORLD)
        if ao is None:
            continue
        slots = mesh.get_editor_property("static_materials")
        changed = False
        for slot in slots:
            mi = slot.get_editor_property("material_interface")
            if not isinstance(mi, unreal.MaterialInstanceConstant):
                continue
            base_name = mi.get_name()
            if base_name.endswith(f"_{prop}"):   # already this prop's own instance
                own = mi
            else:
                if master is None:
                    master = baked_master(master_of(mi))
                path = f"{ENV}/Materials/{base_name}_{prop}"
                own = ASSETS.load_asset(path) if ASSETS.does_asset_exist(path) else TOOLS.duplicate_asset(f"{base_name}_{prop}", f"{ENV}/Materials", mi)
                MEL.set_material_instance_parent(own, master)
                slot.set_editor_property("material_interface", own)
                changed = True
            MEL.set_material_instance_texture_parameter_value(own, "BakeAO", ao)
            if cav is not None:
                MEL.set_material_instance_texture_parameter_value(own, "BakeCavity", cav)
            MEL.update_material_instance(own)
            ASSETS.save_loaded_asset(own)
            wired += 1
        if changed:
            mesh.set_editor_property("static_materials", slots)
            ASSETS.save_loaded_asset(mesh)
    log(f"arena: {wired} prop material slots carry their BakeUV AO and cavity")


def main():
    for name in ("Grandpa", "Kim", "Matt"):
        athlete(name)
    if os.environ.get("SLAP_BAKE_ARENA", "1") != "0":
        arena()


if __name__ == "__main__":
    main()
