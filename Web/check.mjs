// Node check: one turn each way of Grandpa vs Kim through the browser engine (sim.js), events printed.
//   node Web/check.mjs        (from the repo root)
import fs from "node:fs";
import loadMujoco from "@mujoco/mujoco";
import * as ort from "onnxruntime-web";
import { loadMatch } from "./loader.js";

const mj = await loadMujoco();
const get = (p, kind) => kind === "text" ? fs.readFileSync(p, "utf8") : kind === "json" ? JSON.parse(fs.readFileSync(p, "utf8")) : new Uint8Array(fs.readFileSync(p));
const events = [];
const { match } = await loadMatch(mj, ort, get, e => { if (e.type !== "turn_start") events.push(e); console.log(JSON.stringify(e, (k, v) => typeof v === "number" ? Math.round(v * 100) / 100 : v)); });
const t0 = performance.now();
let steps = 0;
while (events.filter(e => e.type === "turn_end").length < 2 && steps < 200 * 40) { await match.step(); steps++; }
console.log(`${steps} physics steps (${(steps / 200).toFixed(1)} s simulated) in ${((performance.now() - t0) / 1000).toFixed(1)} s`);
if (!events.some(e => e.type === "turn_end")) process.exit(1);
