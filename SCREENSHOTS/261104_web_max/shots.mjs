// The report's before/after shots: one page per camera, the match frozen on the first strike's contact (+90 ms of
// spray), then every variant (a tier and ?fx= overrides) drawn on that same frozen frame, so each pair differs only by
// the effect. Run from the repo root with the dev server up:
//   python Web/serve.py --port 8093 &
//   node SCREENSHOTS/261104_web_max/shots.mjs [--base http://127.0.0.1:8093/Web/] [--size 1080x1920] [--cams close,side,wide]
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url)), ROOT = path.resolve(HERE, "../..");
const { chromium } = createRequire(path.join(ROOT, "Web", "package.json"))("playwright-core");
const arg = (k, d) => { const i = process.argv.indexOf(`--${k}`); return i > 0 ? process.argv[i + 1] : d; };
const BASE = arg("base", "http://127.0.0.1:8093/Web/"), [W, H] = arg("size", "1080x1920").split("x").map(Number);
const CAMS = arg("cams", "close,side,wide").split(",");
const OUT = path.join(HERE, "shots"); fs.mkdirSync(OUT, { recursive: true });

// name, tier, fx overrides (each also forces the late-match sweat, wet:1)
const VARIANTS = [
  ["low", "low", {}], ["cinema", "cinema", {}], ["max", "max", {}],
  ["max_nogi", "max", { gi: 0 }], ["max_nossr", "max", { ssr: 0 }], ["max_nocsh", "max", { csh: 0 }],
  ["max_nopart", "max", { part: 0 }], ["max_sssdisk", "max", { sss2: 0, sss: 32 }], ["max_nodetail", "max", { detail: 1 }],
  ["max_nowear", "max", { wear: 0 }],
];
const ONLY = arg("only", null)?.split(",");
if (ONLY) VARIANTS.splice(0, VARIANTS.length, ...VARIANTS.filter(v => ONLY.includes(v[0])));
for (const v of arg("extra", "").split(";").filter(Boolean)) {   // --extra "name=tier:fx,fx;..." for a one-off
  const [n, rest] = v.split("="), [t, fx] = rest.split(":");
  VARIANTS.push([n, t, Object.fromEntries((fx || "").split(",").filter(Boolean).map(kv => { const [k, x] = kv.split("/"); return [k, +x]; }))]);
}

const local = process.env.LOCALAPPDATA ? path.join(process.env.LOCALAPPDATA, "ms-playwright") : null;
const exe = process.env.CHROME || (local && fs.existsSync(local) ? fs.readdirSync(local).filter(d => /^chromium-\d+$/.test(d)).sort().reverse()
  .map(d => path.join(local, d, "chrome-win64", "chrome.exe")).find(f => fs.existsSync(f)) : undefined);
// --force_high_performance_gpu: without it ANGLE takes the laptop's integrated GPU (the 11-03 ladder did)
const browser = await chromium.launch({ executablePath: exe, args: ["--use-angle=d3d11", "--enable-gpu", "--ignore-gpu-blocklist", "--force_high_performance_gpu", "--autoplay-policy=no-user-gesture-required"] });
const log = {};
for (const cam of CAMS) {
  const page = await browser.newPage({ viewport: { width: W, height: H }, deviceScaleFactor: 1 });
  const errors = [];
  page.on("pageerror", e => errors.push(e.message));
  page.on("console", m => { if (m.type() === "error" || m.type() === "warning") errors.push(`${m.type()}: ${m.text()}`); });
  await page.goto(`${BASE}?go&gfx=max&fx=wet:1`, { waitUntil: "load" });
  await page.waitForSelector("#loading", { state: "detached", timeout: 120000 });
  await page.waitForFunction(() => window.__poslapLook, null, { timeout: 30000 });
  await page.waitForTimeout(5000);
  const strike = cam !== "wide";
  await page.evaluate(([c, s]) => { window.__poslapLook.cam(c); window.__poslapLook.pauseOnStrike = s; }, [cam, strike]);
  let struck = false;
  if (strike) {   // the first strike's frame (the match runs until a palm lands, up to 60 s), then 90 ms of spray
    const t0 = Date.now();
    while (Date.now() - t0 < 60000 && !(struck = await page.evaluate(() => window.__poslapLook.look.lens.uniforms.age.value < 1))) await page.waitForTimeout(200);
    await page.evaluate(() => { window.__poslapLook.pauseOnStrike = false; window.__poslapLook.pause(false); });
    await page.waitForTimeout(90);
  } else await page.waitForTimeout(15000);   // the wide shot: 15 s more of the match (the wear decals build up)
  await page.evaluate(() => { window.__poslapLook.pause(true); window.__poslapLook.cam("free"); });
  for (const [name, tier, fx] of VARIANTS) {
    await page.evaluate(([t, f]) => window.__poslapLook.set(t, f), [tier, { ...fx, wet: 1 }]);
    await page.waitForTimeout(1800);   // shaders, and the GI's history settles on the frozen frame
    await page.screenshot({ path: path.join(OUT, `${cam}_${name}.png`) });
  }
  const gpu = await page.evaluate(() => window.__poslapLook.look.gpuName());
  log[cam] = { struck, gpu, errors: [...new Set(errors)].slice(0, 12) };
  console.log(`${cam}: struck ${struck}, ${errors.length} console errors/warnings${errors.length ? ": " + errors[0].slice(0, 300) : ""}`);
  await page.close();
}
await browser.close();
fs.writeFileSync(path.join(OUT, "shots.json"), JSON.stringify(log, null, 1));
