// Python vs browser: replay a match training/match.py logged through sim.js and compare the referee's events.
//   node Web/parity.mjs <Logs/Gameplay/YYMMDD/match_*.jsonl> [gg|kk|gk|kg]      (from the repo root)
// The log's match_start gives the first striker, the seed and (rules.version 2) the rules; the brains are the pairing's
// (loader.js MATCHUPS), so log the match with the same two. Env: SLAP_PHYSICS=<n> as check.mjs; SLAP_RULES=2 forces
// rules 2 on any physics (python's switch of the same name); SLAP_STUN_SPEED=<m/s> plays with another stun threshold
// (parity only: give python the same number). Exit 1 on the first event that differs.
// The turns run back to back as match.py's do (no hold between them, no walk-in).
import fs from "node:fs";
import loadMujoco from "@mujoco/mujoco";
import * as ort from "onnxruntime-web";
import { loadMatch } from "./loader.js";
import { Match } from "./sim.js";
import { RULES } from "./shared.gen.js";

const [logPath, key = "gk"] = process.argv.slice(2);
if (!logPath) { console.error("usage: node Web/parity.mjs <python match .jsonl> [gg|kk|gk|kg]"); process.exit(2); }
const KEYS = ["strike", "foul", "knockdown", "stun", "count", "count_end", "turn_end", "round", "result"];
const py = fs.readFileSync(logPath, "utf8").split("\n").filter(l => l.trim()).map(l => JSON.parse(l));
const start = py.find(e => e.type === "match_start");
const PHYS = Number(process.env.SLAP_PHYSICS || 1);
const remap = p => {   // check.mjs remap: a physics version's models, the newest brain trained at or below it
  if (PHYS < 2) return p;
  const b = p.match(/^training\/models\/brains\/(\w+)\.onnx$/);
  if (b) return [4, 3, 2].filter(v => v <= PHYS).map(v => `training/models/brains/${b[1]}_v${v}.onnx`).find(f => fs.existsSync(f)) ?? p;
  const v = p.replace(/^training\/models\/(?!brains\/)/, `training/models/v${PHYS}/`);
  return fs.existsSync(v) ? v : p;
};
const get = (p, kind) => (p = remap(p), kind === "text" ? fs.readFileSync(p, "utf8") : kind === "json" ? JSON.parse(fs.readFileSync(p, "utf8")) : new Uint8Array(fs.readFileSync(p)));

Match.forceRules2 = process.env.SLAP_RULES === "2";
if (process.env.SLAP_STUN_SPEED) Match.rules = { ...RULES, stun_speed: Number(process.env.SLAP_STUN_SPEED) };
Match.first = ["red", "blue"].indexOf(start.first); Match.seed = start.seed;
const js = [];
const mj = await loadMujoco();
const { match } = await loadMatch(mj, ort, get, e => js.push(e), key);
match.autoNext = false;
if ((start.rules?.version === 2) !== match.r2) { console.error(`rules differ: the log plays rules ${start.rules?.version ?? 1}, the browser rules ${match.r2 ? 2 : 1}`); process.exit(1); }
for (let n = 0; n < 200 * 60 * 8 && match.phase !== "after" && match.phase !== "over"; n++) {
  if (match.phase === "hold") match.startTurn(); else await match.step();
}

// what python logs, to the decimals it logs: t and count to 3, speeds to 2; fouls as "who: why"; python-only fields dropped
const r = (x, n) => typeof x === "number" ? Math.round(x * 10 ** n) / 10 ** n : x;
const norm = e => {
  const o = { ...e };
  for (const k of ["pos", "hand_speed", "feint", "feints", "match"]) delete o[k];   // additive / page-only fields
  for (const k of ["t", "touch", "count"]) if (k in o) o[k] = r(o[k], 3);
  for (const k of ["speed", "head_peak", "severity", "stun"]) if (k in o) o[k] = r(o[k], 2);
  if (o.fouls) o.fouls = o.fouls.map(f => Array.isArray(f) ? `${f[0]}: ${f[1]}` : f);
  if (o.type === "result") delete o.round;   // python logs the round a match ended in; the browser's result does not carry it
  return o;
};
const a = py.filter(e => KEYS.includes(e.type)).map(norm), b = js.filter(e => KEYS.includes(e.type)).map(norm);
// Exact, until a stun. A stunned body falls slack for seconds, and the two ONNX runtimes differ in the 9th decimal of a
// joint angle from the first decision on (measured 2026-11-13): by the time he lands, the fall is a brain step apart.
// So from a match's first stun the fall's own numbers (when a knockdown or a count was called, how long it ran, the
// head's peak speed, which part landed) are compared within LOOSE; who fell, the fouls, the ends and the cards stay exact.
const LOOSE = (x, y) => Math.abs(x - y) <= Math.max(0.25, 0.15 * Math.abs(x));
const same = (x, y, loose) => {
  const kx = Object.keys(x).sort(), ky = Object.keys(y).sort();
  return JSON.stringify(kx) === JSON.stringify(ky) && kx.every(k => JSON.stringify(x[k]) === JSON.stringify(y[k])
    || (loose && (((k === "t" || k === "count" || k === "head_peak") && typeof x[k] === "number" && typeof y[k] === "number" && LOOSE(x[k], y[k]))
      || (k === "part" && x.type === "knockdown"))));
};
let bad = -1, stunned = false;
for (let i = 0; i < Math.max(a.length, b.length); i++) {
  if (!a[i] || !b[i] || !same(a[i], b[i], stunned)) { bad = i; break; }
  stunned ||= a[i].type === "stun";
}
const tally = t => b.filter(e => e.type === t).length;
console.log(`${logPath}: rules ${match.r2 ? 2 : 1}, first ${start.first}, seed ${start.seed}: python ${a.length} events, browser ${b.length} `
  + `(strikes ${tally("strike")}, fouls ${tally("foul")}, knockdowns ${tally("knockdown")}, stuns ${tally("stun")}, counts ${tally("count_end")})`);
if (bad >= 0) {
  console.log(`  FIRST MISMATCH at event ${bad}:\n    python : ${JSON.stringify(a[bad])}\n    browser: ${JSON.stringify(b[bad])}`);
  process.exit(1);
}
console.log(`  PASS: ${b.find(e => e.type === "result")?.how}, winner ${b.find(e => e.type === "result")?.winner}`);
