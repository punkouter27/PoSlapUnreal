// The audio report: a real match's sound cues, recorded from the page, rendered twice into an OfflineAudioContext (the
// samples engine and the synth engine, same cues, same camera path), plus the material matrix (each pair once at its
// reference energy and at 4x). Writes WAVs, spectrogram PNGs (log frequency, dB) and audio.json next to this file.
//   python Web/serve.py --port 8093 &
//   node SCREENSHOTS/261104_web_max/audio_render.mjs [--seconds 45]
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url)), ROOT = path.resolve(HERE, "../..");
const { chromium } = createRequire(path.join(ROOT, "Web", "package.json"))("playwright-core");
const arg = (k, d) => { const i = process.argv.indexOf(`--${k}`); return i > 0 ? process.argv[i + 1] : d; };
const SECONDS = +arg("seconds", 45), OUT = path.join(HERE, "audio"); fs.mkdirSync(OUT, { recursive: true });
const local = process.env.LOCALAPPDATA ? path.join(process.env.LOCALAPPDATA, "ms-playwright") : null;
const exe = process.env.CHROME || (local && fs.existsSync(local) ? fs.readdirSync(local).filter(d => /^chromium-\d+$/.test(d)).sort().reverse()
  .map(d => path.join(local, d, "chrome-win64", "chrome.exe")).find(f => fs.existsSync(f)) : undefined);
const browser = await chromium.launch({ executablePath: exe, args: ["--use-angle=d3d11", "--enable-gpu", "--force_high_performance_gpu", "--autoplay-policy=no-user-gesture-required"] });
const page = await browser.newPage({ viewport: { width: 540, height: 960 } });
page.on("pageerror", e => console.log("pageerror:", e.message));
await page.goto(`http://127.0.0.1:8093/Web/?go&gfx=high&audio=synth`, { waitUntil: "load" });
await page.waitForSelector("#loading", { state: "detached", timeout: 120000 });
await page.waitForFunction(() => window.__poslapSound, null, { timeout: 30000 });

// 1. record: every cue the match gives the sound engine, with its time (the contact frames deep-copied)
await page.evaluate(() => {
  const s = window.__poslapSound, log = window.__audioLog = [], t0 = performance.now();
  const rec = (name, conv = a => a) => { const f = s[name].bind(s); s[name] = (...a) => { log.push([(performance.now() - t0) / 1000, name, JSON.parse(JSON.stringify(conv(a)))]); return f(...a); }; };
  for (const n of ["strike", "reaction", "nearMiss", "knockdown", "bell", "chime", "step", "skid", "tick", "setDistance", "setVoices"]) rec(n);
  rec("contacts", a => [a[0] && { impacts: a[0].impacts, slides: a[0].slides, heads: a[0].heads, dt: a[0].dt }]);
  rec("listen", a => [a[0] && { position: { x: a[0].position.x, y: a[0].position.y, z: a[0].position.z }, matrixWorld: { elements: Array.from(a[0].matrixWorld.elements) } }]);
  s.setVoices(s.voice);
});
await page.waitForTimeout(SECONDS * 1000);
const log = await page.evaluate(() => window.__audioLog);
console.log(`recorded ${log.length} cues over ${SECONDS} s: ${["strike", "knockdown", "bell", "step", "skid"].map(n => `${n} ${log.filter(e => e[1] === n).length}`).join(", ")}, contact impacts ${log.filter(e => e[1] === "contacts").reduce((a, e) => a + (e[2][0]?.impacts.length || 0), 0)}`);

// 2. render: the same cues into an OfflineAudioContext, per engine; then WAV + spectrogram in the page
const result = await page.evaluate(async ([log, seconds]) => {
  const { Sound, MATERIALS, synthImpact } = await import("./audio.js");
  const sr = 48000;
  const wav = (chs) => {   // 16-bit PCM WAV as base64
    const n = chs[0].length, nc = chs.length, b = new DataView(new ArrayBuffer(44 + n * nc * 2));
    const w = (o, s) => [...s].forEach((c, i) => b.setUint8(o + i, c.charCodeAt(0)));
    w(0, "RIFF"); b.setUint32(4, 36 + n * nc * 2, true); w(8, "WAVEfmt "); b.setUint32(16, 16, true); b.setUint16(20, 1, true); b.setUint16(22, nc, true);
    b.setUint32(24, sr, true); b.setUint32(28, sr * nc * 2, true); b.setUint16(32, nc * 2, true); b.setUint16(34, 16, true); w(36, "data"); b.setUint32(40, n * nc * 2, true);
    for (let i = 0; i < n; i++) for (let c = 0; c < nc; c++) b.setInt16(44 + (i * nc + c) * 2, Math.max(-1, Math.min(1, chs[c][i])) * 32767, true);
    let s = ""; const u = new Uint8Array(b.buffer); for (let i = 0; i < u.length; i += 0x8000) s += String.fromCharCode(...u.subarray(i, i + 0x8000));
    return btoa(s);
  };
  const spectro = (x, marks = [], W = 1400, Hh = 360) => {   // log-frequency spectrogram, 40 Hz .. 16 kHz, -90..0 dB
    const N = 2048, hop = Math.max(256, Math.floor(x.length / W)), cols = Math.floor((x.length - N) / hop), cv = document.createElement("canvas");
    cv.width = cols; cv.height = Hh; const g = cv.getContext("2d"), img = g.createImageData(cols, Hh);
    const win = Float32Array.from({ length: N }, (_, i) => 0.5 - 0.5 * Math.cos(2 * Math.PI * i / N)), re = new Float32Array(N), im = new Float32Array(N);
    const fft = () => { for (let i = 1, j = 0; i < N; i++) { let bit = N >> 1; for (; j & bit; bit >>= 1) j ^= bit; j ^= bit; if (i < j) { [re[i], re[j]] = [re[j], re[i]]; [im[i], im[j]] = [im[j], im[i]]; } }
      for (let len = 2; len <= N; len <<= 1) { const a = -2 * Math.PI / len; for (let i = 0; i < N; i += len) for (let k = 0; k < len / 2; k++) {
        const c = Math.cos(a * k), s = Math.sin(a * k), xr = re[i + k + len / 2] * c - im[i + k + len / 2] * s, xi = re[i + k + len / 2] * s + im[i + k + len / 2] * c;
        re[i + k + len / 2] = re[i + k] - xr; im[i + k + len / 2] = im[i + k] - xi; re[i + k] += xr; im[i + k] += xi; } } };
    const col = v => [Math.min(255, 400 * v * v), Math.min(255, 300 * Math.max(0, v - 0.35)), Math.min(255, 120 + 300 * (v - 0.7)) * (v > 0.2 ? 1 : v * 5)];
    for (let c = 0; c < cols; c++) {
      for (let i = 0; i < N; i++) { re[i] = x[c * hop + i] * win[i]; im[i] = 0; }
      fft();
      for (let y = 0; y < Hh; y++) {
        const f = 40 * Math.pow(16000 / 40, 1 - y / (Hh - 1)), k = Math.round(f / sr * N), m = Math.hypot(re[k], im[k]) / (N / 4);
        const v = Math.max(0, Math.min(1, (20 * Math.log10(m + 1e-9) + 90) / 90)), [r, gg, bb] = col(v), o = 4 * (y * cols + c);
        img.data[o] = r; img.data[o + 1] = gg; img.data[o + 2] = bb; img.data[o + 3] = 255;
      }
    }
    g.putImageData(img, 0, 0);
    const out = document.createElement("canvas"); out.width = W; out.height = Hh + 18; const o = out.getContext("2d");
    o.fillStyle = "#000"; o.fillRect(0, 0, W, Hh + 18); o.drawImage(cv, 0, 0, W, Hh);
    o.font = "11px monospace"; o.fillStyle = "#aaa";
    for (const f of [100, 1000, 10000]) { const y = (1 - Math.log(f / 40) / Math.log(16000 / 40)) * (Hh - 1); o.fillRect(0, y, 6, 1); o.fillText(f >= 1000 ? `${f / 1000}k` : `${f}`, 8, y + 4); }
    for (const [t, c, label] of marks) { const xx = t / (x.length / sr) * W; o.fillStyle = c; o.fillRect(xx, Hh + 2, 2, 8); if (label) o.fillText(label, xx + 3, Hh + 16); }
    return out.toDataURL("image/png");
  };
  const render = async mode => {
    const ctx = new OfflineAudioContext(2, sr * seconds, sr), s = new Sound();
    s.mode = mode; s.coarse = false; s.muted = false;
    await s.unlock(ctx);
    const q = 128 / sr, byT = new Map();
    for (const e of log) { if (e[0] >= seconds - 0.05) continue; const k = Math.max(1, Math.round(e[0] / q)); if (!byT.has(k)) byT.set(k, []); byT.get(k).push(e); }
    for (const [k, es] of byT) ctx.suspend(k * q).then(() => { for (const [, n, a] of es) { try { s[n](...a); } catch (err) { console.warn(n, err.message); } } ctx.resume(); });
    const buf = await ctx.startRendering();
    return [buf.getChannelData(0), buf.getChannelData(1)];
  };
  const marks = log.filter(e => ["strike", "knockdown", "bell"].includes(e[1])).map(e => [e[0], e[1] === "strike" ? "#ff5050" : "#f1c24b", e[1] === "strike" ? `${e[2][0].toFixed(1)} m/s` : e[1]]);
  const out = {};
  for (const mode of ["samples", "synth"]) {
    const [l, r] = await render(mode), mono = l.map((v, i) => 0.5 * (v + r[i]));
    let peak = 0, sq = 0; for (const v of mono) { peak = Math.max(peak, Math.abs(v)); sq += v * v; }
    out[mode] = { wav: wav([l, r]), png: spectro(mono, marks), peak: +peak.toFixed(3), rms_db: +(10 * Math.log10(sq / mono.length + 1e-12)).toFixed(1) };
  }
  // the material matrix: each pair at its reference energy then 4x, dry, 0.9 s apart
  const pairs = Object.keys(MATERIALS), gap = 0.9, mx = new Float32Array(Math.ceil(sr * gap * pairs.length * 2)), mm = [];
  pairs.forEach((p, i) => [1, 4].forEach((k, j) => {
    const M = MATERIALS[p], v = M.v0 * Math.sqrt(k), x = synthImpact(sr, M, v, M.eref * k, 0.37 + i * 0.1 + j), at = Math.round(sr * gap * (2 * i + j));
    mx.set(x.subarray(0, Math.min(x.length, mx.length - at)), at); if (!j) mm.push([gap * 2 * i, "#7aa0ff", p]);
  }));
  out.matrix = { wav: wav([mx]), png: spectro(mx, mm) };
  return out;
}, [log, SECONDS]);
for (const [k, v] of Object.entries(result)) {
  fs.writeFileSync(path.join(OUT, `${k}.wav`), Buffer.from(v.wav, "base64"));
  fs.writeFileSync(path.join(OUT, `${k}.png`), Buffer.from(v.png.split(",")[1], "base64"));
}
const summary = { seconds: SECONDS, cues: log.length, counts: Object.fromEntries(["strike", "knockdown", "bell", "chime", "step", "skid"].map(n => [n, log.filter(e => e[1] === n).length])),
  contact_impacts: log.filter(e => e[1] === "contacts").reduce((a, e) => a + (e[2][0]?.impacts.length || 0), 0),
  samples: { peak: result.samples.peak, rms_db: result.samples.rms_db }, synth: { peak: result.synth.peak, rms_db: result.synth.rms_db } };
fs.writeFileSync(path.join(OUT, "audio.json"), JSON.stringify(summary, null, 1));
console.log(JSON.stringify(summary));
await browser.close();
