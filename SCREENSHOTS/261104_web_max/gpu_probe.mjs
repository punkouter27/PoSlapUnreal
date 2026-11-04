// Which GPU does headless Chromium's WebGL land on, per launch flag set and WebGL powerPreference?
//   node SCREENSHOTS/261104_web_max/gpu_probe.mjs
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url)), ROOT = path.resolve(HERE, "../..");
const { chromium } = createRequire(path.join(ROOT, "Web", "package.json"))("playwright-core");
const local = process.env.LOCALAPPDATA ? path.join(process.env.LOCALAPPDATA, "ms-playwright") : null;
const exe = process.env.CHROME || (local && fs.existsSync(local) ? fs.readdirSync(local).filter(d => /^chromium-\d+$/.test(d)).sort().reverse()
  .map(d => path.join(local, d, "chrome-win64", "chrome.exe")).find(f => fs.existsSync(f)) : undefined);
const base = ["--use-angle=d3d11", "--enable-gpu", "--ignore-gpu-blocklist"];
for (const extra of [[], ["--force_high_performance_gpu"], ["--force_high_performance_gpu", "--disable-features=UseDirectComposition"]]) {
  const b = await chromium.launch({ executablePath: exe, args: [...base, ...extra] });
  const p = await b.newPage();
  const r = await p.evaluate(() => ["default", "high-performance"].map(pp => {
    const gl = document.createElement("canvas").getContext("webgl2", { powerPreference: pp }), x = gl?.getExtension("WEBGL_debug_renderer_info");
    return `${pp}: ${x ? gl.getParameter(x.UNMASKED_RENDERER_WEBGL) : "?"}`;
  }));
  console.log(extra.join(" ") || "(no extra)", "\n  ", r.join("\n   "));
  await b.close();
}
