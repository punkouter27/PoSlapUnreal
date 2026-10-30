// Second-screen relay check: node Web/live_check.mjs (from the repo root). Starts Web/live.py on a spare port, plays a
// fake Unreal and two viewers: every message reaches the viewer (one over 64 KiB, the long-length frame), a viewer who
// joins late gets the last pose at once. A publisher without the key is refused. Exits 1 on a miss.
import { spawn } from "node:child_process";

const port = 8799, url = `ws://127.0.0.1:${port}`, key = "check-key";
const relay = spawn("python", ["Web/live.py", "--port", String(port), "--key", key], { stdio: "ignore" });
const wait = ms => new Promise(r => setTimeout(r, ms));
const open = u => new Promise((ok, bad) => { const w = new WebSocket(u); w.onopen = () => ok(w); w.onerror = bad; });
// the relay needs a moment to bind (a busy machine can take seconds): wait for it instead of guessing
async function waitForRelay() {
  for (let i = 0; i < 80; i++) {
    try { await fetch(`http://127.0.0.1:${port}/`); return true; } catch { await wait(250); }
  }
  return false;
}
let ok = false;
try {
  await waitForRelay();
  const got = [], late = [];
  const viewer = await open(url); viewer.onmessage = e => got.push(JSON.parse(e.data));
  const ue = await open(`${url}/ue?key=${key}`);
  ue.send(JSON.stringify({ t: "pose", c: ["grandpa", "kim"], m: 1, r: 1, s: "red", w: false, slow: 1, q: Array.from({ length: 74 }, (_, i) => i / 10) }));
  ue.send(JSON.stringify({ t: "ev", e: { type: "strike", striker: "red", speed: 11.2 } }));
  ue.send(JSON.stringify({ t: "big", p: "x".repeat(70000) }));
  await wait(300);
  const latecomer = await open(url); latecomer.onmessage = e => late.push(JSON.parse(e.data));
  await wait(300);
  // a publisher without the key is refused: it cannot push into the viewers' pages
  const rogue = await open(`${url}/ue`);
  await wait(300);
  const rogueSent = rogue.send(JSON.stringify({ t: "ev", e: { type: "strike", striker: "red", speed: 99 } }));
  await wait(300);
  const noKey = got.length === 3 && got[0].q[73] === 7.3 && got[1].e.speed === 11.2 && got[2].p.length === 70000
    && late.length === 1 && late[0].t === "pose" && got.every(m => m.e?.speed !== 99);
  ok = noKey && !rogueSent;
  console.log(`viewer got ${got.map(m => m.t).join(", ")}; late viewer got ${late.map(m => m.t).join(", ")}; keyless publisher refused -> ${ok ? "PASS" : "FAIL"}`);
  for (const w of [viewer, latecomer, ue]) w.close();
  try { rogue.close(); } catch { }
} finally {
  relay.kill();
}
process.exit(ok ? 0 : 1);
