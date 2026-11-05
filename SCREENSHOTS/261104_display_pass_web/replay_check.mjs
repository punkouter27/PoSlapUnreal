// The replay never holds the match: while it plays, the physics steps keep coming at real time (200 per second), and
// a tap on the view skips it. Also: the scouting card comes up at the break after round 1 and a tap takes it down.
//   python Web/serve.py --port 8094 &   node SCREENSHOTS/261104_display_pass_web/replay_check.mjs
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
const HERE = path.dirname(fileURLToPath(import.meta.url)), ROOT = path.resolve(HERE, "../..");
const { chromium } = createRequire(path.join(ROOT, "Web", "package.json"))("playwright-core");
const BASE = process.argv[2] || "http://127.0.0.1:8094/Web/";
const local = process.env.LOCALAPPDATA ? path.join(process.env.LOCALAPPDATA, "ms-playwright") : null;
const exe = process.env.CHROME || (local && fs.existsSync(local) ? fs.readdirSync(local).filter(d => /^chromium-\d+$/.test(d)).sort().reverse()
  .map(d => path.join(local, d, "chrome-win64", "chrome.exe")).find(f => fs.existsSync(f)) : undefined);
const b = await chromium.launch({ executablePath: exe, args: ["--use-angle=d3d11", "--enable-gpu", "--ignore-gpu-blocklist"] });
const p = await b.newPage({ viewport: { width: 360, height: 800 } });
const errors = [];
p.on("pageerror", e => errors.push(e.message));
await p.goto(`${BASE}?m=gk&gfx=low&go`, { waitUntil: "load" });
await p.waitForSelector("#loading", { state: "detached", timeout: 120000 });
const st = () => p.evaluate(() => ({ on: window.__poslap.replaying, steps: window.__poslap.state.steps, scout: document.getElementById("scout").classList.contains("on") }));
let ok = true;
const check = (name, cond, info = "") => { ok &&= !!cond; console.log(`${cond ? "ok  " : "FAIL"} ${name}${info ? `: ${info}` : ""}`); };
// 1: the first replay plays to its end on its own, the steps running underneath
let t0 = Date.now(), s;
while (Date.now() - t0 < 60000 && !(s = await st()).on) await p.waitForTimeout(30);
check("a landed strike starts a replay", s.on);
const a = { s: s.steps, t: Date.now() };
let len = 0;
while ((s = await st()).on && len < 5000) { await p.waitForTimeout(30); len = Date.now() - a.t; }
const rate = (s.steps - a.s) / ((Date.now() - a.t) / 1000);
check("the replay ends by itself (~1.1 s)", len > 600 && len < 2500, `${len} ms`);
check("the match ran on underneath (physics steps per wall second)", rate > 150, rate.toFixed(0));
// 2: the next replay is skipped by a tap on the view
t0 = Date.now();
while (Date.now() - t0 < 90000 && !(s = await st()).on) await p.waitForTimeout(30);
if (s.on) {
  await p.mouse.click(180, 450);
  await p.waitForTimeout(80);
  check("a tap skips the replay", !(await st()).on);
} else console.log("skip  no second strike within 90 s");
// 3: the scouting card at the break after round 1, taken down by a tap
t0 = Date.now();
while (Date.now() - t0 < 120000 && !(s = await st()).scout) await p.waitForTimeout(50);
check("the scouting card comes up between rounds", s.scout);
if (s.scout) { await p.click("#scout"); await p.waitForTimeout(80); check("a tap closes it", !(await st()).scout); }
check("no page errors", !errors.length, errors.join(" | "));
await b.close();
console.log(ok ? "PASS" : "FAIL");
process.exit(ok ? 0 : 1);
