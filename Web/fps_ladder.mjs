// The fps ladder: each graphics effect switched on in turn (Web/fx.js TIER_FX), the frame rate measured at each step,
// at phone size (412 x 915) and at a phone's device pixels (1080 x 2400). Headless Chromium on the real GPU (ANGLE
// D3D11, vsync and the frame cap off, so a step can read above 60), one page per size, the match running.
//
//   cd Web && npm i --no-save playwright-core     (once; not a dependency of the site)
//   python Web/serve.py --port 8093 &
//   node Web/fps_ladder.mjs [--base http://127.0.0.1:8093/Web/] [--warm 1] [--measure 3] [--out ladder.json]
//                           [--sizes 412x915,1080x2400] [--cam side|wide|close] [--shots <dir>]   (a screenshot per step)
//
// Each size is one run of about 10 s loading + steps x (warm + measure) s of rendering (~45 s with the defaults), so
// a laptop that is also training stays inside a minute of GPU load per run. fps = frames delivered / time, uncapped,
// physics paused (--live: running); --ss adds a 1.5x supersampling step past cinema. Numbers taken while another job loads the
// GPU understate the headroom: say so next to them.
import fs from "node:fs";
import path from "node:path";

const arg = (k, d) => { const i = process.argv.indexOf(`--${k}`); return i > 0 ? process.argv[i + 1] : d; };
const BASE = arg("base", "http://127.0.0.1:8093/Web/"), WARM = +arg("warm", 2.5), MEASURE = +arg("measure", 3.5);
const SIZES = arg("sizes", "412x915,1080x2400").split(",").map(s => s.split("x").map(Number));
const SHOTS = arg("shots", null), OUT = arg("out", null), CAM = arg("cam", "side");

// the ladder: cumulative, cheapest first; each step names the tier and the overrides on top of it
const STEPS = [
  ["low", "low", {}],
  ["high", "high", {}],
  ["ultra (11-03 set: full GTAO, 4K shadows)", "ultra", { sss: 0, vol: 0, dof: 0 }],
  ["+ skin SSS (16 taps)", "ultra", { vol: 0, dof: 0 }],
  ["+ volumetric key light (24 steps)", "ultra", { dof: 0 }],
  ["+ depth of field (24 taps) = ultra", "ultra", {}],
  ["cinema: SSS 32, volume 64, DoF 48", "cinema", { refl: 0 }],
  ["+ floor reflection = cinema", "cinema", {}],
];
if (process.argv.includes("--ss")) STEPS.push(["+ 1.5x supersampling", "cinema", { ss: 1.5, pr: 3 }]);   // past the floor

let chromium;
try { ({ chromium } = await import("playwright-core")); }
catch { console.error("playwright-core is missing: cd Web && npm i --no-save playwright-core"); process.exit(2); }
const local = process.env.LOCALAPPDATA ? path.join(process.env.LOCALAPPDATA, "ms-playwright") : null;
const exe = process.env.CHROME || (local && fs.existsSync(local) ? fs.readdirSync(local).filter(d => /^chromium-\d+$/.test(d)).sort().reverse()
  .map(d => path.join(local, d, "chrome-win64", "chrome.exe")).find(f => fs.existsSync(f)) : undefined);
const browser = await chromium.launch({ executablePath: exe, args: ["--use-angle=d3d11", "--enable-gpu", "--ignore-gpu-blocklist",
  "--disable-gpu-vsync", "--disable-frame-rate-limit", "--autoplay-policy=no-user-gesture-required"] });
const result = { when: new Date().toISOString(), base: BASE, warm: WARM, measure: MEASURE, sizes: {} };
for (const [w, h] of SIZES) {
  const page = await browser.newPage({ viewport: { width: w, height: h }, deviceScaleFactor: 1 });
  const errors = [];
  page.on("pageerror", e => errors.push(e.message));
  page.on("console", m => { if (m.type() === "error") errors.push(m.text()); });
  await page.goto(`${BASE}?go&gfx=low`, { waitUntil: "load" });
  await page.waitForSelector("#loading", { state: "detached", timeout: 120000 });
  await page.waitForFunction(() => window.__poslapLook, null, { timeout: 30000 });
  await page.waitForTimeout(4000);   // the athletes' art
  await page.evaluate(cam => window.__poslapLook.cam(cam), CAM);   // one shot for every step (the auto-director cuts)
  await page.waitForTimeout(1200);
  // the physics paused: the ladder measures what each effect costs to draw (the brains' inference and the physics
  // catch-up bursts would otherwise swamp a few-second window); --live keeps the match running
  if (!process.argv.includes("--live")) await page.evaluate(() => window.__poslapLook.pause(true));
  const gpu = await page.evaluate(() => {
    const gl = document.createElement("canvas").getContext("webgl2"), x = gl?.getExtension("WEBGL_debug_renderer_info");
    return x ? gl.getParameter(x.UNMASKED_RENDERER_WEBGL) : "unknown";
  });
  const rows = [];
  for (const [i, [name, tier, fx]] of STEPS.entries()) {
    await page.evaluate(([t, f]) => window.__poslapLook.set(t, f), [tier, fx]);
    await page.waitForTimeout(WARM * 1000);   // shaders compile, the auto step-down is off (a named tier)
    const r = await page.evaluate(ms => new Promise(ok => {
      const ts = []; const t0 = performance.now();
      const f = t => { ts.push(t); if (t - t0 < ms) requestAnimationFrame(f); else ok(ts); };
      requestAnimationFrame(f);
    }), MEASURE * 1000);
    // frames delivered over the window: with vsync off the GPU's backpressure sets the pace, so this is the step's
    // throughput; the median rAF interval is the main thread's submit time only (logged, not used)
    const dts = r.slice(1).map((t, k) => t - r[k]).sort((a, b) => a - b);
    const med = dts[dts.length >> 1] || 1, p95 = dts[Math.floor(0.95 * dts.length)] || 0;
    const fps = (r.length - 1) / ((r.at(-1) - r[0]) / 1000);
    rows.push({ step: name, tier, fx, fps: +fps.toFixed(1), frame_ms: +(1000 / fps).toFixed(2), p95_ms: +p95.toFixed(1), submit_median_ms: +med.toFixed(2) });
    console.log(`${w}x${h}  ${name.padEnd(44)} ${fps.toFixed(1).padStart(6)} fps  (${(1000 / fps).toFixed(1)} ms/frame, p95 ${p95.toFixed(1)} ms)`);
    if (SHOTS) {
      fs.mkdirSync(SHOTS, { recursive: true });
      await page.screenshot({ path: path.join(SHOTS, `ladder_${w}x${h}_${i}.png`) });
    }
  }
  result.sizes[`${w}x${h}`] = { gpu, rows, errors: errors.slice(0, 10) };
  await page.close();
}
await browser.close();
if (OUT) fs.writeFileSync(OUT, JSON.stringify(result, null, 1));
// the default tier: the highest whose full step holds >= 45 fps at the device-pixel size (headroom over the 30 fps floor)
const big = result.sizes[SIZES.map(s => s.join("x")).at(-1)]?.rows || [];
const full = { low: 0, high: 1, ultra: 5, cinema: 7 };   // the step index where each tier is complete
const pick = Object.entries(full).filter(([, i]) => big[i] && big[i].fps >= 45).map(([t]) => t).at(-1) || "low";
console.log(`highest tier >= 45 fps at ${SIZES.at(-1).join("x")}: ${pick}  (Web/fx.js DEFAULT_TIER picks per drawing-buffer size)`);
