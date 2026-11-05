// The display pass's before/after shots and layout metrics: one Grandpa vs Kim match per size, real time, low tier
// (the GPU is training). Shots: the menu, the default HUD mid-turn, the first strike (+0.35 s: the replay in "after"),
// the TAB stats, the break after round 1 (the scouting card in "after"), the Match sheet. Metrics: the red/blue plates'
// mirror symmetry about the centre line, vertical scroll, the console errors.
//   python Web/serve.py --port 8094 &
//   node SCREENSHOTS/261104_display_pass_web/shots.mjs --out before|after [--base http://127.0.0.1:8094/Web/] [--sizes 360x800,1080x1920]
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url)), ROOT = path.resolve(HERE, "../..");
const { chromium } = createRequire(path.join(ROOT, "Web", "package.json"))("playwright-core");
const arg = (k, d) => { const i = process.argv.indexOf(`--${k}`); return i > 0 ? process.argv[i + 1] : d; };
const BASE = arg("base", "http://127.0.0.1:8094/Web/"), OUT = path.join(HERE, arg("out", "after"));
const SIZES = arg("sizes", "360x800,1080x1920").split(",").map(s => s.split("x").map(Number));
fs.mkdirSync(OUT, { recursive: true });

const local = process.env.LOCALAPPDATA ? path.join(process.env.LOCALAPPDATA, "ms-playwright") : null;
const exe = process.env.CHROME || (local && fs.existsSync(local) ? fs.readdirSync(local).filter(d => /^chromium-\d+$/.test(d)).sort().reverse()
  .map(d => path.join(local, d, "chrome-win64", "chrome.exe")).find(f => fs.existsSync(f)) : undefined);
const browser = await chromium.launch({ executablePath: exe, args: ["--use-angle=d3d11", "--enable-gpu", "--ignore-gpu-blocklist", "--autoplay-policy=no-user-gesture-required"] });

// the plates' rectangles and the centre line; mirror error = |left gap - right gap| and |width difference|
const metrics = () => {
  const r = id => { const e = document.getElementById(id); if (!e) return null; const b = e.getBoundingClientRect(); return { x: b.x, y: b.y, w: b.width, h: b.height }; };
  const W = innerWidth, red = r("plateRed"), blue = r("plateBlue"), mom = r("momentumBar");
  const inner = sel => [...document.querySelectorAll(sel)].map(e => { const b = e.getBoundingClientRect(); return { x: b.x, w: b.width, y: b.y }; });
  const mirror = red && blue ? { gapLeft: red.x, gapRight: W - (blue.x + blue.w), dWidth: red.w - blue.w, dTop: red.y - blue.y, dHeight: red.h - blue.h } : null;
  // inner mirror: each red child's left offset vs the matching blue child's right offset
  const kids = side => inner(`#plate${side} > *`);
  const rk = kids("Red"), bk = kids("Blue");
  const innerErr = red && blue ? Math.max(0, ...rk.map((k, i) => bk[i] ? Math.abs((k.x - red.x) - ((blue.x + blue.w) - (bk[i].x + bk[i].w))) + Math.abs(k.w - bk[i].w) : 0)) : null;
  return { W, H: innerHeight, scroll: document.scrollingElement.scrollHeight - innerHeight, red, blue, mom, mirror, innerErr,
    momCentre: mom ? mom.x + mom.w / 2 - W / 2 : null };
};

const report = {};
for (const [W, H] of SIZES) {
  const tag = `${W}x${H}`, page = await browser.newPage({ viewport: { width: W, height: H }, deviceScaleFactor: 1 });
  const errors = [];
  page.on("pageerror", e => errors.push(e.message));
  page.on("console", m => { if (m.type() === "error" || m.type() === "warning") errors.push(`${m.type()}: ${m.text()}`); });
  const shot = async name => { await page.screenshot({ path: path.join(OUT, `${name}_${tag}.png`) }); };
  await page.goto(`${BASE}?m=gk&gfx=low&hold=30`, { waitUntil: "load" });
  await page.waitForSelector("#loading", { state: "detached", timeout: 120000 });
  await page.waitForTimeout(1500);
  await shot("menu");
  await page.click("#practice");
  await page.waitForTimeout(6500);   // the walk-in (3 s), then the first turn
  await shot("gameplay");
  const m = { gameplay: await page.evaluate(metrics) };
  // the first strike: the lens shock's age resets on the contact (fx.js)
  let t0 = Date.now(), struck = false;
  while (Date.now() - t0 < 45000 && !(struck = await page.evaluate(() => window.__poslapLook.look.lens.uniforms.age.value < 0.3))) await page.waitForTimeout(60);
  await page.waitForTimeout(350);
  await shot("strike");
  await page.waitForTimeout(900);
  await shot("strike_late");
  await page.keyboard.press("Tab"); await page.waitForTimeout(500);
  await shot("stats");
  m.stats = await page.evaluate(metrics);
  await page.keyboard.press("Tab");
  // the break after round 1: the second "next turn" hold
  let holds = 0, inHold = false; t0 = Date.now();
  while (Date.now() - t0 < 120000 && holds < 2) {
    const h = await page.evaluate(() => /^next turn/.test(document.getElementById("clock").textContent) || !!document.querySelector(".scout.on"));
    if (h && !inHold) holds++;
    inHold = h;
    if (holds < 2) await page.waitForTimeout(100);
  }
  await page.waitForTimeout(400);
  await shot("round_break");
  m.roundBreak = await page.evaluate(metrics);
  await page.click('#tabs button[data-tab="match"]'); await page.waitForTimeout(400);
  await shot("sheet_match");
  m.sheet = await page.evaluate(metrics);
  report[tag] = { struck, holds, metrics: m, errors: [...new Set(errors)].slice(0, 20) };
  console.log(`${tag}: struck ${struck}, breaks ${holds}, mirror ${JSON.stringify(m.gameplay.mirror)}, inner ${m.gameplay.innerErr?.toFixed(1)}, scroll ${m.gameplay.scroll}, ${errors.length} errors${errors.length ? ": " + errors[0].slice(0, 200) : ""}`);
  await page.close();
}
await browser.close();
fs.writeFileSync(path.join(OUT, "metrics.json"), JSON.stringify(report, null, 1));
