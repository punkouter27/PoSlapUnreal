// Writes index.html from the ladder JSONs, the shots and the audio render next to this file.
//   node SCREENSHOTS/261104_web_max/make_report.mjs
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const J = f => { try { return JSON.parse(fs.readFileSync(path.join(HERE, f), "utf8")); } catch { return null; } };
const esc = s => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;");
const cls = v => v == null ? "" : v >= 45 ? "ok" : v >= 30 ? "warn" : "bad";
const audioMod = await import(new URL("../../Web/audio.js", import.meta.url)).catch(() => null);   // (needs a DOM: falls back below)

function ladderTable(file, title, note) {
  const L = J(file);
  if (!L) return "";
  const sizes = Object.keys(L.sizes), rows = L.sizes[sizes[0]].rows.map(r => r.step);
  let h = `<h3>${esc(title)}</h3><p class="note">${note} GPU: <code>${esc(L.sizes[sizes[0]].gpu)}</code>. ${esc(L.when)}.</p><table><tr><th>Step (cumulative)</th>`;
  for (const s of sizes) h += `<th>${s} fps</th><th>median fps</th><th>1% low</th><th>GPU ms</th>`;
  h += "</tr>";
  rows.forEach((name, i) => {
    h += `<tr><td>${esc(name)}</td>`;
    for (const s of sizes) {
      const r = L.sizes[s].rows[i] || {};
      h += `<td class="${cls(r.fps)}">${r.fps ?? ""}</td><td>${r.median_fps ?? ""}</td><td class="${cls(r.low1_fps)}">${r.low1_fps ?? ""}</td><td>${r.gpu_ms ?? "n/a"}</td>`;
    }
    h += "</tr>";
  });
  return h + "</table>";
}
const pair = (a, b, la, lb) => `<div class="pair"><figure><img src="shots/${a}.jpg" loading="lazy"><figcaption>${la}</figcaption></figure><figure><img src="shots/${b}.jpg" loading="lazy"><figcaption>${lb}</figcaption></figure></div>`;
const has = f => fs.existsSync(path.join(HERE, "shots", `${f}.jpg`));
const EFFECTS = [
  ["Whole tier: cinema (11-03 top) vs max", "cinema", "max", "Same frozen frame (the strike's contact + 90 ms). Max drops GTAO and the planar mirror; adds everything below."],
  ["Screen-space GI, multi-bounce, and its AO", "max_nogi", "max", "Half-res cosine rays against the depth, gathering last frame's finished image (each frame one more bounce), reprojected history. Off: no bounce light, no AO (GTAO is off on max)."],
  ["Screen-space reflections", "max_nossr", "max", "Half-res mirror rays on the mat, deck and podium top, blurred by roughness, Schlick Fresnel. Only on-screen content reflects (the planar mirror also caught the off-screen lights)."],
  ["Contact shadows", "max_nocsh", "max", "16 steps toward the key light in screen space: under the chin, between the fingers, soles on the mat."],
  ["Skin: separable six-Gaussian profile SSS + rim translucency vs the 11-03 disk SSS", "max_sssdisk", "max", "d'Eon/Luebke profile as two 25-tap passes (red scatters ~2.7 mm, blue ~0.1 mm); thin skin (ears, nose: curvature) lets the rim lights through, red."],
  ["Procedural micro-relief (skin pores, mat grain, deck boards, podium vinyl)", "max_nodetail", "max", "World/bind-space noise bump and roughness, faded out where a pixel cannot resolve it."],
  ["GPU spray: refracting, dispersive droplets + mist", "max_nopart", "max", "Off: the 11-03 CPU spray is not drawn on max either, so off = no spray. On: 6.1k drops + 24.6k mist in the pool, a burst's count from the strike energy."],
  ["Wear decals from the sim's contacts", "max_nowear", "max", "After ~20 s of match: rubber where soles skidded, sweat where palms braced on the podium, chalk where bodies landed."],
];
let shots = "";
for (const cam of ["close", "side", "wide"]) {
  shots += `<h3>${cam} shot</h3>`;
  for (const [t, a, b, n] of EFFECTS) if (has(`${cam}_${a}`) && has(`${cam}_${b}`)) shots += `<h4>${esc(t)}</h4><p class="note">${esc(n)}</p>` + pair(`${cam}_${a}`, `${cam}_${b}`, `before: ${a}`, `after: ${b}`);
}
const A = J("audio/audio.json");
const MATS = audioMod?.MATERIALS;
let matrix = "";
if (MATS) {
  matrix = "<table><tr><th>pair</th><th>modes (Hz / T60 s / amp)</th><th>contact noise band, decay</th><th>contact time at v0</th><th>reference energy</th></tr>";
  for (const [k, m] of Object.entries(MATS)) matrix += `<tr><td>${k}</td><td>${m.modes.map(x => `${x[0]} / ${x[1]} / ${x[2]}`).join("<br>")}</td><td>${m.noise[0]}-${m.noise[1]} Hz, ${m.noise[2] * 1000} ms</td><td>${m.tau * 1000} ms at ${m.v0} m/s</td><td>${m.eref} J</td></tr>`;
  matrix += "</table>";
}
const html = `<!doctype html><html><head><meta charset="utf-8"><title>PoSlap web max tier, 2026-11-04</title><style>
body { margin: 0; padding: 24px; background: #0e0e12; color: #eee; font: 15px/1.5 system-ui, sans-serif; max-width: 1400px; }
h2 { margin: 36px 0 8px; border-bottom: 1px solid #333; } h3 { margin: 22px 0 4px; color: #f1c24b; } h4 { margin: 16px 0 2px; }
.pair { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; max-width: 900px; } figure { margin: 0; }
img { width: 100%; border: 1px solid #333; border-radius: 6px; background: #000; } figcaption { font: 13px ui-monospace, monospace; color: #aaa; }
.note { color: #bbb; max-width: 900px; margin: 0 0 6px; } table { border-collapse: collapse; margin: 8px 0; font-size: 14px; }
td, th { border: 1px solid #333; padding: 3px 7px; text-align: left; vertical-align: top; } th { background: #1a1a22; }
.ok { color: #6fce8f; } .warn { color: #f1c24b; } .bad { color: #ff6b5e; } code { color: #f1c24b; }
.banner { background: #2a1414; border: 1px solid #ff6b5e; padding: 10px 14px; border-radius: 6px; max-width: 900px; } .spec { max-width: 1400px; }
</style></head><body>
<h1>Browser max tier: GI, reflections, refraction, procedural sound &middot; 2026-11-04</h1>
<p class="note">three r170 on WebGL2 (the page's renderer and every pass are WebGL; three's WebGPU/TSL SSGI nodes arrived after r170, and
moving the page to WebGPURenderer is a larger change than this pass). Everything is display or audio only: MuJoCo owns every contact;
<code>Web/contacts.js</code> reads them once a frame and never writes back. Checks: <code>node Web/check.mjs gg|kk|gk|kg</code>,
<code>live_check</code>, <code>api_check</code> pass; <code>python Web/build.py</code> builds with no LFS pointers; the built dist loads at max and low
with no console errors.</p>
<div class="banner"><b>Measurement conditions: under load.</b> Every frame rate here was taken while other jobs held the same GPU (RTX 5070 Ti laptop):
<code>spirula.exe</code> at 30-97 % SM, the <code>exam_ab.py</code> python exam, and from 09:31 the 14 h physics-3 retrain; the GPU sat at 86-89 &deg;C with
graphics clocks of 530-930 MHz (max 3090, thermal slowdown flagged). The GPU is time-sliced, so the mean fps and the 1 % lows are dominated by
preemption stalls (single frames of 0.3-7 s) and the "GPU ms" timer includes time spent waiting behind the other contexts. The median frame
rate is the closest to the drawing cost. Re-run <code>node Web/fps_ladder.mjs</code> on an idle GPU (after the retrain, ETA ~23:40) before trusting
the floor. <b>Also:</b> the 11-03 ladder (and the first ladder of this pass, <code>ladder_before.json</code>) ran on the laptop's <i>Intel</i> iGPU: headless
Chromium picks it unless launched with <code>--force_high_performance_gpu</code> (now in fps_ladder.mjs and the shot scripts).</div>

<h2>Effects (max tier; each switchable with ?fx=)</h2>
<table><tr><th>Effect</th><th>?fx key (max default)</th><th>Cost at 1080 x 1920, ladder step (under load)</th><th>Where</th></tr>
<tr><td>Normal buffer rebuilt from the linear depth; GTAO's scene re-render dropped</td><td>gtao 0</td><td>max base 228 fps / 5.7 GPU ms vs cinema 16 fps / 112 ms (same run)</td><td>fx.js MaxPass</td></tr>
<tr><td>SSGI multi-bounce + AO, half res, temporal</td><td>gi 4 (rays), gis 12 (steps)</td><td>+1.5 GPU ms (228 &rarr; 134 fps)</td><td>fx.js GI_FS, ACC_FS</td></tr>
<tr><td>SSR, half res, roughness blur, Fresnel</td><td>ssr 32</td><td>+2.5 GPU ms</td><td>fx.js SSR_FS, COMP_FS</td></tr>
<tr><td>Contact shadows (screen-space, to the key)</td><td>csh 16</td><td>noisy (+0..7 ms)</td><td>fx.js COMP_FS</td></tr>
<tr><td>Separable skin-profile SSS + rim translucency + pore noise</td><td>sss2 12 (2 x 25 taps)</td><td>+0.7 GPU ms</td><td>fx.js sssSep, skin()</td></tr>
<tr><td>GPU spray (stateless, energy-sized) with refraction + dispersion</td><td>part 24576</td><td>~+0.6 GPU ms while a spray flies; 0 otherwise</td><td>fx.js Bursts, RefractPass</td></tr>
<tr><td>Wear decals (contacts &rarr; 1024&sup2; map) + surface micro-relief</td><td>wear 1, detail 2</td><td>+3.5 GPU ms (noisy)</td><td>contacts.js, fx.js Wear, surface()</td></tr>
<tr><td>Floor/depth buffers in 32-bit float (half floats stepped 2 mm at 4 m and tilted the rebuilt normals)</td><td>-</td><td>~0</td><td>fx.js DepthCopyPass</td></tr>
</table>
<p class="note"><b>Defaults.</b> Max = the row above (gi 4 x 12, ssr 32, csh 16, sss2 12, part 24.6k, wear, detail 2, vol 48, DoF 32, 4K shadows).
Auto picks max only on a fine-pointer device whose WebGL renderer names a high-end GPU (RTX 2060+/30x0/40x0/50x0, RX 6700+, Apple M Pro/Max/Ultra,
Arc A7) and at most 2.1 Mpx of drawing buffer (1080 x 1920 or 1920 x 1080 at 1x); above that the 11-03 rule (ultra &le; 1 Mpx, else high).
Phones and the four existing tiers are unchanged; the 30 fps / 4 s step-down now walks max &rarr; cinema &rarr; ultra &rarr; high &rarr; low.
The "max raised" steps (GI 8 x 16, SSR 48, csh 24, SSS 2 x 33, spray 98k) did not hold 30 fps under this load and stay opt-in via ?fx=.</p>

<h2>fps ladder</h2>
${ladderTable("ladder_max_1.json", "Full ladder, both desktop sizes (2.5 s warm, 3.5 s measured, physics paused)", "Under load (see the banner); the 1440 x 2560 half of this run collapsed into multi-second stalls.")}
${ladderTable("ladder_max_1440_2.json", "1440 x 2560 again (4 s warm, 4 s measured)", "Under load.")}
${ladderTable("ladder_sustained.json", "Sustained: 6 s warm, 12 s measured per tier", "Under load (retrain + exam + spirula). Median fps is high for every tier, 1 % lows are near 0 for every tier above high: the stalls are the other jobs, not the effects.")}
${ladderTable("ladder_before.json", "Before this pass, as the 11-03 ladder ran it (Intel iGPU)", "For reference: this is the iGPU, which is why ultra/cinema read 2-24 fps at desktop sizes.")}

<h2>Before / after per effect</h2>
<p class="note">1080 x 1920, one page per shot, the match frozen and every variant drawn on the same frame (<code>shots.mjs</code>); late-match sweat forced (wet:1).</p>
${shots}

<h2>Sound</h2>
<p class="note"><b>Engine.</b> <code>Web/audio.js</code> has two engines behind the same cues. <i>Synth</i> (default on desktop above the low tier): every impact is
synthesised when it happens. A material pair's resonator bank (modes: frequency, T60, amplitude) and contact noise are excited by a half-sine force
pulse whose length follows Hertz contact (&tau; ~ v<sup>-1/5</sup>: a harder hit is shorter, so each mode, weighted by the pulse's spectrum, comes
out brighter) and whose amplitude follows the collision energy &frac12; m v&sup2; (strike: the card's strike mass x palm speed; the rest from the contact
probe's approach speed and effective mass). Flesh pairs start sharp and glide down (a soft contact stiffens as it compresses). Sliding soles drive a
continuous friction voice per athlete: band noise whose level follows the shear speed x &radic;(normal force) and whose centre rises with the speed,
plus an intermittent stick-slip squeak on the deck. <b>Spatial:</b> every source is a PannerNode with panningModel HRTF at its place in the arena,
the listener is the camera (impacts at the contact point, friction at the sliding sole, breath and grunts at each head). <b>Geometric reverb:</b>
image sources off the floor (mat 0.35 / deck 0.8 by where the reflection lands), the podium's two faces (only if the path really meets the face), the
bowl's four walls (0.3, people absorb) and the rig at 12 m, each delayed by its extra path, low-passed and HRTF/equal-power placed; ArenaIR.wav is the
late tail. The crowd loop, its grains, the PA and the bell are unchanged. <i>Samples</i> (phones, the low tier, or View &rarr; Audio: samples;
?audio=samples|synth) is the 11-03 engine. Note: MuJoCo's <code>mj_contactForce</code> binding returns zeros into a JS array, so the probe decodes
<code>efc_force</code> itself (pyramid edges summed).</p>
<h3>Material matrix</h3>
${matrix || '<p class="note">(see MATERIALS in Web/audio.js)</p>'}
<figure class="spec"><img src="audio/matrix.png"><figcaption>Each pair at its reference energy, then 4x (dry). <a href="audio/matrix.wav">matrix.wav</a></figcaption></figure>
<h3>A match, rendered offline (OfflineAudioContext)</h3>
<p class="note">${A ? `${A.seconds} s of a real match recorded from the page (${A.cues} cues: ${A.counts.strike} strikes, ${A.counts.step} steps, ${A.counts.skid} skids, ${A.contact_impacts} contact impacts), then replayed into an OfflineAudioContext through each engine with the same camera path. Samples: peak ${A.samples.peak}, RMS ${A.samples.rms_db} dB; synth: peak ${A.synth.peak}, RMS ${A.synth.rms_db} dB.` : ""} Red ticks: strikes. Headphones for the HRTF.</p>
<figure class="spec"><img src="audio/samples.png"><figcaption>before: samples engine. <a href="audio/samples.wav">samples.wav</a></figcaption></figure>
<figure class="spec"><img src="audio/synth.png"><figcaption>after: synth engine (modal impacts, friction, HRTF, image sources). <a href="audio/synth.wav">synth.wav</a></figcaption></figure>

<h2>Needs a human decision</h2>
<ul>
<li>A clean frame-rate floor: re-run <code>node Web/fps_ladder.mjs</code> on an idle GPU, then move <code>DEFAULT_TIER.maxMpx</code> (2.1 now) up to 3.7 if max holds &ge; 45 fps at 1440 x 2560.</li>
<li>Hybrid laptops: desktop Chrome on Windows draws on the integrated GPU unless Windows' Graphics settings put Chrome on the discrete one; max's GPU check then falls back to the 11-03 rule (as it should).</li>
<li>The droplets' dispersion is 6x water's (so a 3-15 px drop shows a fringe); 1 = physical (<code>Bursts</code> uniform <code>disp</code>).</li>
<li>SSR has 2x gain over Fresnel x the surface's weight so the lacquer reads without the planar mirror; the mirror (cinema) also caught off-screen lights.</li>
</ul>
</body></html>`;
fs.writeFileSync(path.join(HERE, "index.html"), html);
console.log("index.html written", MATS ? "(with the material matrix)" : "(matrix: import failed)");
