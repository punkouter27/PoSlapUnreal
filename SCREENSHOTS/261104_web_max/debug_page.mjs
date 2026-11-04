// A debugging harness: open the page at a tier, run JS in it after the first strike, print the result.
//   node SCREENSHOTS/261104_web_max/debug_page.mjs "<js expression>" [--gfx max] [--size 1080x1920] [--shot file.png] [--q extra=query]
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url)), ROOT = path.resolve(HERE, "../..");
const { chromium } = createRequire(path.join(ROOT, "Web", "package.json"))("playwright-core");
const arg = (k, d) => { const i = process.argv.indexOf(`--${k}`); return i > 0 ? process.argv[i + 1] : d; };
const [W, H] = arg("size", "1080x1920").split("x").map(Number), EXPR = process.argv[2];
const local = process.env.LOCALAPPDATA ? path.join(process.env.LOCALAPPDATA, "ms-playwright") : null;
const exe = process.env.CHROME || (local && fs.existsSync(local) ? fs.readdirSync(local).filter(d => /^chromium-\d+$/.test(d)).sort().reverse()
  .map(d => path.join(local, d, "chrome-win64", "chrome.exe")).find(f => fs.existsSync(f)) : undefined);
const browser = await chromium.launch({ executablePath: exe, args: ["--use-angle=d3d11", "--enable-gpu", "--ignore-gpu-blocklist", "--force_high_performance_gpu", "--autoplay-policy=no-user-gesture-required"] });
const page = await browser.newPage({ viewport: { width: W, height: H }, deviceScaleFactor: 1 });
page.on("pageerror", e => console.log("pageerror:", e.message));
page.on("console", m => { if (m.type() === "error" || m.type() === "warning") console.log(`${m.type()}: ${m.text().slice(0, 400)}`); });
await page.goto(`http://127.0.0.1:8093/Web/?go&gfx=${arg("gfx", "max")}${arg("q", "") ? "&" + arg("q") : ""}`, { waitUntil: "load" });
await page.waitForSelector("#loading", { state: "detached", timeout: 120000 });
await page.waitForFunction(() => window.__poslapLook, null, { timeout: 30000 });
await page.waitForTimeout(4000);
await page.evaluate(() => { window.__poslapLook.cam("close"); window.__poslapLook.pauseOnStrike = true; });
const t0 = Date.now();
while (Date.now() - t0 < 60000 && !(await page.evaluate(() => window.__poslapLook.look.lens.uniforms.age.value < 1))) await page.waitForTimeout(200);
await page.evaluate(() => { window.__poslapLook.pauseOnStrike = false; window.__poslapLook.pause(false); });
await page.waitForTimeout(+arg("after", 90));
await page.evaluate(() => window.__poslapLook.pause(true));
await page.waitForTimeout(800);
console.log(JSON.stringify(await page.evaluate(EXPR), null, 1));
if (arg("shot", null)) await page.screenshot({ path: arg("shot") });
await browser.close();
