// Node check: one turn each way of a pairing through the browser engine (sim.js), events printed; the broadcast's
// display-only reading (broadcast.js: the feint/real swing tag, the head-snap trace) runs beside it on the same steps.
//   node Web/check.mjs [gg|kk|gk|kg]        (from the repo root)
import fs from "node:fs";
import loadMujoco from "@mujoco/mujoco";
import * as ort from "onnxruntime-web";
import { loadMatch } from "./loader.js";
import { SwingTagger, Snap } from "./broadcast.js";

const mj = await loadMujoco();
const get = (p, kind) => kind === "text" ? fs.readFileSync(p, "utf8") : kind === "json" ? JSON.parse(fs.readFileSync(p, "utf8")) : new Uint8Array(fs.readFileSync(p));
const events = [], tags = [], tagger = new SwingTagger(), snap = new Snap();
let match;
({ match } = await loadMatch(mj, ort, get, e => {
  if (e.type === "turn_start") tagger.reset(); else events.push(e);
  if (e.type === "strike") { tags.push(tagger.landed()); snap.start(e.t); }
  if (e.type === "turn_end" && snap.points.length) console.log(`  head snap: ${snap.points.length} samples over ${snap.points.at(-1)[0].toFixed(2)} s, peak ${snap.peak.toFixed(2)} m/s (turn_end head_peak ${e.head_peak})`);
  console.log(JSON.stringify(e, (k, v) => typeof v === "number" ? Math.round(v * 100) / 100 : v));
}, process.argv[2] || "gg"));
const t0 = performance.now();
let steps = 0;
while (events.filter(e => e.type === "turn_end").length < 2 && steps < 200 * 40) {
  await match.step(); steps++;
  if (match.phase === "turn") {   // display only: read after the step, never written back
    const D = match.sides[1 - match.s], G = match.d.geom_xpos, t = match.k * match.dt;
    snap.sample(t, [G[3 * D.head], G[3 * D.head + 1], G[3 * D.head + 2]]);
    if (match.k % 4 === 0 && tagger.sample(t, match.speedNow || 0) === "FEINT") tags.push("FEINT");   // on brain steps, as match.py
  }
}
console.log(`${steps} physics steps (${(steps / 200).toFixed(1)} s simulated) in ${((performance.now() - t0) / 1000).toFixed(1)} s`);
console.log(`swing tags: ${tags.join(", ") || "none"}`);
if (!events.some(e => e.type === "turn_end")) process.exit(1);
