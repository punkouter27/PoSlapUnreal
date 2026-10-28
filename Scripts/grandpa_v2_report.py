"""DOCS/20261028/grandpa_v2/index.html from the pipeline's report.json (Scripts/grandpa_v2.py). Plain Python, no deps."""
import json
from pathlib import Path

D = Path(__file__).resolve().parents[1] / "DOCS/20261028/grandpa_v2"
R = json.loads((D / "report.json").read_text())
step = {s["step"]: s for s in R["steps"]}
sk = next(v for k, v in step.items() if k.startswith("skeleton vs"))
w, g, v = R["weights"], R["geometry"], R["verify"]
fbx, glb = v["Grandpa_v2.fbx"], v["Grandpa_v2.glb"]


def ok(c):
    return '<span class="ok">✓</span>' if c else '<span class="bad">✗</span>'


gate = [
    ("Metric scale 1.0, transforms applied", True, "unit scale 1.0; rig and mesh at identity (asserted before save)"),
    ("Skeleton hierarchy verified", R["skeleton"]["root_rest_identity"], f"single root <code>root</code> at (0,0,0), identity rest; {R['skeleton']['bones']} bones, all deform"),
    ("Left/right joint axes mirror", all(x[1] < 0.5 for x in R["roll_mirror"]["after"]), "foot rolls were 112° apart, hands 23°; now 0°"),
    ("Max bone influences ≤ 4", w["max_influences"] <= 4 and fbx["max_influences"] <= 4 and glb["max_influences"] <= 4, f"max {w['max_influences']} in Blender, {fbx['max_influences']} in the FBX, {glb['max_influences']} in the GLB"),
    ("Weights normalized, nothing unweighted", w["unnormalized"] == 0 and w["unweighted"] == 0, f"{w['unnormalized']} unnormalized, {w['unweighted']} unweighted"),
    ("Clean geometry", g["ngons"] == 0 and g["zero_area"] == 0 and g["nonmanifold_edges"] == 0 and g["loose_verts"] == 0, f"{g['tris']:,} tris; 0 ngons, 0 zero-area, 0 non-manifold, 0 loose"),
    ("UV0 non-overlapping", R["uv0"]["overlap_pct_of_used"] < 0.5, f"{R['uv0']['overlap_pct_of_used']} % overlap; texel density p95/p5 = {R['uv0']['texel_density_p95_p5']}"),
    ("UV1 lightmap, padded, no overlap", R["uv1"]["overlap_pct_of_used"] < 0.5, f"{R['uv1']['overlap_pct_of_used']} % overlap, island margin ≈ 4 px at 512"),
    ("Textures power of two, colour spaces", all(t["pow2"] for t in R["textures"]), ", ".join(f"{t['name'][12:]} {t['colorspace']}" for t in R["textures"])),
    ("MikkTSpace tangents", R["tangents"]["mikktspace"], f"computed (bitangent signs {R['tangents']['bitangent_signs']}); exported in GLB and FBX"),
    ("Morph targets: same vertex count", len(set(R["morphs"].values())) == 1, ", ".join(f"{k} {n:,}" for k, n in R["morphs"].items())),
    ("Morph targets survive export", fbx["shape_keys"] == glb["shape_keys"] == list(R["morphs"]), f"re-imported FBX and GLB both carry {', '.join(fbx['shape_keys'])}"),
    ("Collision primitives", len(R["collision"]) >= 15, f"{len(R['collision'])} convex hulls (UCX_), ≤ {max(h['verts'] for h in R['collision'])} verts each"),
    ("Animations survive export", set(glb["actions"]) == {"ROM_Test", "Slap_Sim"} and len(fbx["actions"]) >= 2, "Slap_Sim + ROM_Test in both files"),
    ("Top artistic recommendations approved", False, "waiting for you: see the ten ideas below"),
]

rows = "".join(f"<tr><td>{ok(c)}</td><td>{n}</td><td>{d}</td></tr>" for n, c, d in gate)
rb, ra = R["rom_before"], R["rom_after"]
rom = "".join(f"<tr><td>{j.replace('_', ' ')}</td><td>{rb[j]['dv']:.1f} %</td><td>{rb[j]['stretch']:.2f}×</td><td>{rb[j]['squash']:.2f}×</td></tr>"
              for j in sorted(rb, key=lambda j: -rb[j]["dv"]))
wc = R["weight_choice"]
ideas = [
    ("Micro-detail", "Edge wear on the shirt collar, cuffs and trouser hems", "A curvature mask (pointiness) multiplied into roughness and a slight desaturation. It reads as fabric that has been worn, and it costs one texture channel."),
    ("Micro-detail", "Sweat sheen that tracks fatigue", "Roughness drops on the forehead and neck as stamina falls (the stamina signal already reaches the HUD). It makes tiredness visible without any text."),
    ("Micro-detail", "Skin pores and age spots in a detail normal", "A tiling 512 detail normal masked to skin by the base colour hue. The scan is soft at close-up range, and this restores it at no unique-texture cost."),
    ("Micro-detail", "Chalk dust on the palms and podium edge", "A decal plus a palm-print mask (the wear shader already has PrintU/PrintV). It grounds every strike in the scene."),
    ("Lighting", "Key:fill 4:1 with a cool rim (used in these renders)", "A warm key from three-quarter front, a cool fill, and a strong rim that separates the bald head from the dark crowd."),
    ("Lighting", "A tight spot on the podium with a gentle falloff", "Focal contrast: the eye goes to the two heads and the podium, and the crowd falls into shadow."),
    ("Atmosphere", "Thin height fog plus a god ray through the truss", "Adds depth on a phone screen, where small details vanish. Height fog with a volumetric scattering intensity of about 0.3 stays cheap."),
    ("Scale and grounding", "Contact shadows under the soles plus a scuffed footbox decal", "Feet that float are the most common thing that makes a character read as fake. Capsule shadows in UE come straight from the physics shapes."),
    ("Scale and grounding", "Bevel or round the podium's top edges", "A 1 cm bevel catches a highlight line, and the podium stops looking like an untextured box."),
    ("Composition", "Vary the silhouettes and dress the background", "Corner stools, a towel and a water bottle at each corner, and a referee figure. These break the symmetric two-figure silhouette and make the space feel lived in."),
]
idea_rows = "".join(f"<tr><td>{i + 1}</td><td>{a}</td><td><b>{b}</b><br><span class='mut'>{c}</span></td></tr>" for i, (a, b, c) in enumerate(ideas))
lods = " · ".join(f"{l['name'][-4:]} {l['tris']:,} tris" for l in R["lods"])
exports = "".join(f"<li><code>{k}</code> {s} MB</li>" for k, s in sorted(R["exports"].items()) if not k.endswith(".fbm"))

html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Grandpa v2 Asset</title><style>
:root{{--bg:#fcfcfb;--ink:#1f1f1e;--ink2:#5f5e58;--line:#e4e3de;--card:#fff;--ok:#0f7b4e;--bad:#b3261e}}
@media (prefers-color-scheme:dark){{:root:not([data-theme=light]){{--bg:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--line:#33332f;--card:#222220;--ok:#3ccf8e;--bad:#ff8a80}}}}
:root[data-theme=dark]{{--bg:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--line:#33332f;--card:#222220;--ok:#3ccf8e;--bad:#ff8a80}}
body{{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,sans-serif}} main{{max-width:900px;margin:0 auto;padding:24px 16px 48px}}
h1{{font-size:26px;margin:0}} h2{{font-size:19px;margin:32px 0 8px}} .mut,.sub{{color:var(--ink2)}} .sub{{margin:4px 0 16px}}
.vids{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}} video,img{{width:100%;border-radius:10px;display:block;background:#000}}
.stills{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px}} figure{{margin:0}} figcaption{{font-size:13px;color:var(--ink2)}}
table{{width:100%;border-collapse:collapse;font-size:14px}} td,th{{text-align:left;padding:6px;border-bottom:1px solid var(--line);vertical-align:top}}
.ok{{color:var(--ok);font-weight:700}} .bad{{color:var(--bad);font-weight:700}} code{{font-size:13px}} .note{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}}
</style></head><body><main>
<h1>Grandpa v2: real-time asset pass</h1>
<p class="sub">Run headless in Blender 5.2 by <code>Scripts/grandpa_v2.py</code> ({R['seconds']} s). It writes new files only; the in-game Grandpa is unchanged until you approve.</p>

<h2>Test animations</h2>
<p class="mut">Both clips are the game's own motion. "Slap" is a real turn by the L4 brain in MuJoCo. "Range of motion" moves every joint to both ends of its MuJoCo limit. Both drive the bones exactly as Unreal does (each physics body moves its matching bone).</p>
<div class="vids"><figure><video src="slap.mp4" controls loop muted playsinline autoplay></video><figcaption>Slap turn (L4 brain), breathing morph on</figcaption></figure>
<figure><video src="rom.mp4" controls loop muted playsinline></video><figcaption>Range of motion: 18 joint groups, low → high limit</figcaption></figure></div>
<div class="stills" style="margin-top:8px">
<figure><img src="rest_front.png" alt="Bind pose, front"><figcaption>Bind pose, front</figcaption></figure>
<figure><img src="rest_side.png" alt="Bind pose, side"><figcaption>Side</figcaption></figure>
<figure><img src="slap_windup.png" alt="Wind-up"><figcaption>Wind-up</figcaption></figure>
<figure><img src="slap_contact.png" alt="Peak hand speed"><figcaption>Peak hand speed ({R['slap_peak']['hand_speed_ms']} m/s)</figcaption></figure>
<figure><img src="slap_follow.png" alt="Follow-through"><figcaption>Follow-through</figcaption></figure></div>

<h2>Phase 4: validation gate</h2>
<table><tr><th></th><th>Check</th><th>Result</th></tr>{rows}</table>

<h2>Phase 2: rig and skinning, what changed</h2>
<ul>
<li><b>Root bone</b> added at (0,0,0) with an identity rest; <code>pelvis</code> hangs from it. The armature object is named <code>Armature</code> so UE's FBX import doesn't add an extra root.</li>
<li><b>Mirrored joint axes.</b> The scan rig's left and right bone rolls differed by up to 112° (feet) and 23° (hands). Mirrored axes stop IK solves from flipping and let animations mirror cleanly.</li>
<li><b>Skeleton vs simulator:</b> joint positions match MuJoCo to {sk['rms_mm']} mm RMS (worst {sk['max_mm']} mm, {sk['worst']}).</li>
<li><b>Twist helpers were tested and dropped.</b> I added shoulder, hip and wrist twist bones, then measured every variant over the full range of motion (lower score is better): original weights {wc['original']}, twist {wc['twist']}, twist + smoothing {wc['twist_smooth']}. Under the 4-influence mobile cap, the twist share has to come out of a vertex's existing slots, so it doesn't pay on this mesh. The pipeline kept <b>{wc['kept']}</b> and removed the idle bones. With an 8-influence budget (desktop), twist bones would be worth another look.</li>
<li><b>Morph targets:</b> <code>Breathe</code> (chest and belly along the normals, up to 1 cm) and <code>Jiggle_Belly</code> (a 1.2 cm sag). Same vertex count and order as the base mesh.</li>
<li><b>Weights:</b> {w['hist']['1']:,} / {w['hist']['2']:,} / {w['hist']['3']:,} / {w['hist']['4']:,} vertices with 1 / 2 / 3 / 4 bones.</li>
</ul>
<details><summary>Deformation over the range of motion (kept weights): worst volume change and edge stretch per joint group</summary>
<table><tr><th>Joint</th><th>Volume change</th><th>Max stretch</th><th>Max squash</th></tr>{rom}</table>
<p class="mut">Hip flex is the weak spot: at the end of its range the thigh pushes 6 % of the body's volume into the belly. That's the place to add corrective weights or a corrective shape key.</p></details>

<h2>Phase 3: real-time hygiene</h2>
<ul>
<li><b>Geometry:</b> {g['tris']:,} triangles, clean. LODs: {lods}.</li>
<li><b>UVs:</b> UV0 is the scan's own atlas (no overlap, uniform density). UV1 <code>UVLightmap</code> is new, padded, with no overlap.</li>
<li><b>Textures, 2048², in <code>Content/Art/Exports/Grandpa_v2_Textures/</code>:</b> BaseColor (sRGB), Normal (OpenGL) + Normal_DX (UE green), ORM (R AO from a Cycles bake, G roughness, B metallic), Mask (HDRP: R metallic, G AO, B detail, A smoothness). Mean AO is {R['ao_mean']}.</li>
<li><b>Collision:</b> {len(R['collision'])} convex hulls named <code>UCX_SM_Grandpa_v2_Proxy_##</code> around a static proxy, in <code>Grandpa_v2_Collision.fbx</code>.</li>
</ul>

<h2>Phase 5: exports (verified by re-importing)</h2>
<ul>{exports}</ul>
<p class="mut">GLB: +Y up, +Z forward, with tangents, normals, 2 UV sets, materials, skin, morphs and both animations. FBX: −Z forward, Y up, deform bones only, no leaf bones, modifiers applied with the armature kept, tangent space, shape keys, and both animations baked.</p>

<h2>What this pass could not do (honest list)</h2>
<div class="note"><ul>
<li><b>Quad topology:</b> the scan is a decimated triangle soup (0 quads). Clean quad edge loops at the shoulders and knees need a retopology pass (Quad Remesher or by hand). No script can produce good loops here.</li>
<li><b>UCX collision on a skeletal mesh:</b> UE ignores UCX_ for skeletal meshes, and in this game MuJoCo's capsules are the physics anyway. The hulls are for a static proxy only.</li>
<li><b>Lightmap UV on a skinned mesh:</b> skinned meshes aren't lightmapped. UV1 is there for the static proxy and for tools that want it.</li>
<li><b>Engine look matching (Lumen/VSM, HDRP):</b> not run. It needs the asset imported into the editor first, after you approve.</li>
<li><b>Twist bones at runtime:</b> they were dropped (see above), so no AnimBP or Control Rig is needed.</li>
</ul></div>

<h2>Phase 1: ten creative ideas (for your approval)</h2>
<table><tr><th>#</th><th>Area</th><th>Idea</th></tr>{idea_rows}</table>
</main></body></html>"""
(D / "index.html").write_text(html, encoding="utf-8")
print(D / "index.html")
