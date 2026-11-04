r"""The skin's micro-relief, baked in Blender (headless, Cycles on the CPU): a tileable 512 x 512 detail map.

    "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" -b --factory-startup --python Scripts/blender_skin_detail.py

R: height - pore pits (a Voronoi field, ~0.6 mm cells at the browser's 4.5 cm tile), fine creases (ridged noise) and
   a soft unevenness; G: sweat beads (sparse round drops, 2-4 mm) the browser's skin shader (Web/fx.js) wets with the
   lost stamina. Tileable by construction: the UV square is wrapped onto a torus in 4D, (cos u, sin u, cos v, sin v),
   and every texture is sampled there (Noise and Voronoi take 4D), so the left edge meets the right and the top the
   bottom. Linear data (import with sRGB off).
Writes Web/assets/tex/skin_detail.png and Content/Art/Exports/Textures/T_SkinDetail.png (the same map for Unreal's
M_<athlete>, Scripts/broadcast_fx.py: a detail normal from the height at ~22 tiles per metre). Touches no athlete.
"""
import math
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[1]
OUTS = (ROOT / "Web/assets/tex/skin_detail.png", ROOT / "Content/Art/Exports/Textures/T_SkinDetail.png")
SIZE = 512


def node(nt, kind, **kw):
    n = nt.nodes.new(kind)
    for k, v in kw.items():
        if k == "inputs":
            for name, val in v.items():
                n.inputs[name].default_value = val
        else:
            setattr(n, k, v)
    return n


def math_node(nt, op, a, b=None, clamp=False):
    n = node(nt, "ShaderNodeMath", operation=op, use_clamp=clamp)
    for i, x in enumerate((a, b)):
        if x is None:
            continue
        if isinstance(x, (int, float)):
            n.inputs[i].default_value = x
        else:
            nt.links.new(x, n.inputs[i])
    return n.outputs[0]


def build():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.samples = 1
    bpy.ops.mesh.primitive_plane_add(size=2)
    plane = bpy.context.active_object
    mat = bpy.data.materials.new("SkinDetail")
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    L = nt.links.new
    tc = node(nt, "ShaderNodeTexCoord")
    sep = node(nt, "ShaderNodeSeparateXYZ")
    L(tc.outputs["UV"], sep.inputs[0])
    tau = 2 * math.pi
    u = math_node(nt, "MULTIPLY", sep.outputs[0], tau)
    v = math_node(nt, "MULTIPLY", sep.outputs[1], tau)

    def torus(R):   # (cos u, sin u, cos v) * R and W = sin v * R: the UV square on a 4D torus (tileable)
        c = node(nt, "ShaderNodeCombineXYZ")
        L(math_node(nt, "MULTIPLY", math_node(nt, "COSINE", u), R), c.inputs[0])
        L(math_node(nt, "MULTIPLY", math_node(nt, "SINE", u), R), c.inputs[1])
        L(math_node(nt, "MULTIPLY", math_node(nt, "COSINE", v), R), c.inputs[2])
        return c.outputs[0], math_node(nt, "MULTIPLY", math_node(nt, "SINE", v), R)

    def voronoi(R, scale, rnd=1.0):
        vec, w = torus(R)
        n = node(nt, "ShaderNodeTexVoronoi", voronoi_dimensions="4D", feature="F1", inputs={"Scale": scale, "Randomness": rnd})
        L(vec, n.inputs["Vector"]); L(w, n.inputs["W"])
        return n

    def noise(R, scale, detail=4.0, rough=0.55):
        vec, w = torus(R)
        n = node(nt, "ShaderNodeTexNoise", noise_dimensions="4D", inputs={"Scale": scale, "Detail": detail, "Roughness": rough})
        L(vec, n.inputs["Vector"]); L(w, n.inputs["W"])
        return n.outputs["Fac"]

    # pores: a pit in each Voronoi cell (~37 cells across the tile)
    pores = voronoi(1.0, 14.0, 0.9)
    pit = node(nt, "ShaderNodeMapRange", clamp=True, inputs={"From Min": 0.0, "From Max": 0.5, "To Min": 1.0, "To Max": 0.0})
    L(pores.outputs["Distance"], pit.inputs["Value"])
    # creases: ridged noise (1 - |2n - 1|, sharpened), and a soft unevenness
    rn = math_node(nt, "ABSOLUTE", math_node(nt, "SUBTRACT", math_node(nt, "MULTIPLY", noise(1.0, 1.4, 3.0, 0.5), 2.0), 1.0))
    ridge = math_node(nt, "POWER", math_node(nt, "SUBTRACT", 1.0, rn), 6.0)
    soft = noise(1.0, 0.8, 2.0, 0.4)
    h = math_node(nt, "ADD", 0.55, math_node(nt, "MULTIPLY", pit.outputs[0], -0.35))
    h = math_node(nt, "ADD", h, math_node(nt, "MULTIPLY", ridge, -0.25))
    h = math_node(nt, "ADD", h, math_node(nt, "MULTIPLY", math_node(nt, "SUBTRACT", soft, 0.5), 0.35), clamp=True)
    # sweat beads: round drops in a sparse share of larger cells
    beads = voronoi(1.0, 3.2, 1.0)
    drop = node(nt, "ShaderNodeMapRange", clamp=True, inputs={"From Min": 0.0, "From Max": 0.42, "To Min": 1.0, "To Max": 0.0})
    L(beads.outputs["Distance"], drop.inputs["Value"])
    rcol = node(nt, "ShaderNodeSeparateColor")
    L(beads.outputs["Color"], rcol.inputs[0])
    keep = math_node(nt, "GREATER_THAN", rcol.outputs[0], 0.35)
    g = math_node(nt, "MULTIPLY", math_node(nt, "POWER", drop.outputs[0], 0.6), keep)
    comb = node(nt, "ShaderNodeCombineColor")
    L(h, comb.inputs[0]); L(g, comb.inputs[1])
    em = node(nt, "ShaderNodeEmission")
    L(comb.outputs[0], em.inputs["Color"])
    out = node(nt, "ShaderNodeOutputMaterial")
    L(em.outputs[0], out.inputs["Surface"])
    img = bpy.data.images.new("skin_detail", SIZE, SIZE, float_buffer=False)
    img.colorspace_settings.name = "Non-Color"
    tex = node(nt, "ShaderNodeTexImage")
    tex.image = img
    nt.nodes.active = tex
    plane.data.materials.append(mat)
    bpy.context.view_layer.objects.active = plane
    plane.select_set(True)
    bpy.ops.object.bake(type="EMIT", margin=0)
    for p in OUTS:
        p.parent.mkdir(parents=True, exist_ok=True)
        img.filepath_raw = str(p)
        img.file_format = "PNG"
        img.save()
        print("wrote", p, p.stat().st_size, "bytes")


if __name__ == "__main__":
    build()
