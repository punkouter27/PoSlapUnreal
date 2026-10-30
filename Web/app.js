// PoSlap in the browser: the match engine (sim.js) steps MuJoCo at 200 Hz in real time; three.js draws every
// physics shape where MuJoCo puts it (drawing only, as Unreal does). Served from the repo root: Web/serve.py.
//
// The page is a broadcast HUD: the corner anchors (TL the match state, TC the rate, TR the settings, BL the
// telemetry, BR the build stamp), a bottom dock with the spectator's pick and the tab row, and a bottom sheet
// with the details. TAB opens and closes the sheet; the default view stays clean.
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import loadMujoco from "@mujoco/mujoco";
import * as ort from "onnxruntime-web";
import { loadMatch, MATCHUPS, matchupKey } from "./loader.js";
import { loadArena, loadAthlete } from "./art.js";
import { Look, TIERS } from "./fx.js";
import { Sound } from "./audio.js";

ort.env.wasm.wasmPaths = new URL("./node_modules/onnxruntime-web/dist/", location.href).href;   // relative paths resolve against ort's own file
ort.env.wasm.numThreads = 1;   // no cross-origin isolation needed

const $ = id => document.getElementById(id);
const COLORS = { red: 0xd9473f, blue: 0x4f7fe0 };
const DANGER_FULL = 12;   // m/s of head snap that reads as full KO danger (the relay's scale)
// ?live=ws://<host>:8765 (Web/live.py) or an Azure Web PubSub client URL: draw the match Unreal is playing instead of
// running our own; the physics here only places the bodies where Unreal's MuJoCo put them.
// Only a WebSocket URL we recognise is used: a ?live= parameter is attacker-controlled, and the relay it points at
// sends the text that reaches the HUD, so an unexpected scheme or host must not become a connection.
const LIVE = (() => {
  const raw = new URLSearchParams(location.search).get("live");
  if (!raw) return null;
  try {
    const u = new URL(raw);
    if (u.protocol !== "ws:" && u.protocol !== "wss:") return null;
    const host = u.hostname.toLowerCase();
    const local = host === "127.0.0.1" || host === "localhost" || host === "[::1]" || host === "::1";
    if (!local && !host.endsWith(".webpubsub.azure.com")) return null;
    return u.href;
  } catch { return null; }
})();
const liveSeries = [0, 0];
const BUILD = $("stamp").textContent.trim() || "dev";   // Web/build.py writes the build date (YYMMDD)

// ---- the match state the HUD shows -----------------------------------------------------------------------
const S = {
  phase: "menu",       // menu | practice | live | result
  round: 1, cards: [[0, 0], [0, 0], [0, 0]], series: [0, 0],
  strikes: 0, kos: 0, knockdowns: 0, fouls: 0, lastStrike: null, impact: null,
  danger: { red: 0, blue: 0, source: "local" },
  live: false, steps: 0, simTime: 0,
};
const NAMES = {};   // corner -> athlete (the card's display name), filled once the match loads

// ---- the spectator's pick: who lands the next strike -------------------------------------------------------
const STREAK_KEY = "poslap.streak.v1";
const streak = { n: 0, best: 0 };
try { const s = JSON.parse(localStorage.getItem(STREAK_KEY) || "null"); if (s) { streak.n = +s.n || 0; streak.best = +s.best || 0; } } catch { }
const saveStreak = () => { try { localStorage.setItem(STREAK_KEY, JSON.stringify(streak)); } catch { } };
let pick = null;        // the pick for the turn in progress: "red" | "blue" | "neither" | null
let pickOpen = false;   // a turn is running: the pick counts

function setPick(v) {
  pick = v;
  for (const b of $("picks").children) b.setAttribute("aria-pressed", String(b.dataset.pick === v));
}
function setPickOpen(on) {
  pickOpen = on;
  for (const b of $("picks").children) b.disabled = !on;   // no turn to predict: the pick is off
  if (!on) setPick(null);
}
function renderStreak() {
  const info = $("pickinfo");
  info.textContent = "";
  const n = document.createElement("span"); n.className = "n"; n.textContent = String(streak.n);
  const best = document.createElement("span"); best.className = "best"; best.textContent = `best ${streak.best}`;
  info.append(n, document.createTextNode(" "), best);
  const line = $("streak");
  line.textContent = "";
  line.append(document.createTextNode(`streak ${streak.n} `));
  const b = document.createElement("span"); b.className = "best"; b.textContent = `· best ${streak.best}`;
  line.appendChild(b);
}
let pickBannerTimer = 0;
function pickBanner(text, kind) {
  const el = $("pickbanner");
  el.textContent = text;
  el.className = `pickbanner on ${kind}`;
  clearTimeout(pickBannerTimer);
  pickBannerTimer = setTimeout(() => { el.className = "pickbanner"; }, 1900);
}
function resolvePick(e) {
  // the turn is over: the strike landed (the striker) or nobody did (the clock, a foul, a fall)
  const landed = e.touch !== null && e.touch !== undefined;
  const answer = landed ? e.striker : "neither";
  if (pick === null) pickBanner("NO PICK", "none");
  else if (pick === answer) { streak.n += 1; streak.best = Math.max(streak.best, streak.n); pickBanner(`CORRECT — streak ${streak.n}`, "good"); }
  else { streak.n = 0; pickBanner("MISSED", "bad"); }
  saveStreak();
  renderStreak();
  setPickOpen(false);
}
$("picks").addEventListener("click", e => {
  const b = e.target.closest("button[data-pick]");
  if (!b || b.disabled) return;
  setPick(b.dataset.pick);
});

// ---- the banner -------------------------------------------------------------------------------------------
let bannerTimer = 0;
function banner(big, small = "", seconds = 1.6, color = "") {
  $("bannerBig").textContent = big; $("bannerBig").style.color = color; $("bannerSmall").textContent = small;
  $("banner").classList.add("on"); clearTimeout(bannerTimer);
  bannerTimer = setTimeout(() => $("banner").classList.remove("on"), seconds * 1000);
}
// The striker's name arrives over the wire (the relay), so it is text, never markup: this builds the label with the
// DOM and returns the node, so no attacker-supplied string is ever parsed as HTML.
const cornerNode = n => {
  const wrap = document.createElement("span");
  const s = document.createElement("span");
  s.className = n === "blue" ? "blue" : "red";
  s.textContent = String(n).toUpperCase();
  wrap.appendChild(s);
  const name = NAMES[n];
  if (name) wrap.appendChild(document.createTextNode(" " + name));
  return wrap;
};
const foulText = fouls => (fouls || []).map(f => Array.isArray(f) ? f.join(" ") : String(f)).join(", ");

function renderPips(round, turnStriker, bStruck, bFinished) {
  const el = $("pipsTrack");
  if (!el) return;
  const first = S.firstStriker || "red";
  const turnOffset = turnStriker === first ? 0 : 1;
  const activeTurn = bFinished ? S.turnsDone : Math.min(5, Math.max(0, (round - 1) * 2 + turnOffset));
  let str = "";
  for (let r = 1; r <= 3; r++) {
    const t1 = (r - 1) * 2, t2 = t1 + 1;
    // a finished match: the turns it played, then "–" for those a KO cut off
    const pip = t => bFinished ? (t < S.turnsDone ? "●" : "–") : (t < activeTurn || (t === activeTurn && bStruck) ? "●" : (t === activeTurn ? "◈" : "○"));
    str += `R${r} [${pip(t1)} ${pip(t2)}]  `;
  }
  el.textContent = str.trim();
}

let countTimer = null, count = 0;
function startTenCount() {
  const tc = $("tencount");
  if (!tc) return;
  tc.hidden = false;
  count = 1;
  $("tcNum").textContent = count;
  runCount(1000);
}
function runCount(ms) {   // the referee's count; a KO finishes it quickly to 10 instead of cutting it off
  clearInterval(countTimer);
  countTimer = setInterval(() => {
    count++;
    if (count > 10) { clearInterval(countTimer); $("tencount").hidden = true; }
    else { $("tcNum").textContent = count; }
  }, ms);
}
function finishTenCount() {
  if ($("tencount").hidden) { $("tencount").hidden = false; count = 0; }   // a KO with no knockdown call first
  runCount(170);
}
function stopTenCount() {
  clearInterval(countTimer);
  const tc = $("tencount");
  if (tc) tc.hidden = true;
}

function updateMomentum(redRatio) {
  const fill = $("momentumFill");
  const lbl = $("momentumLabel");
  if (!fill || !lbl) return;
  const pct = Math.round(Math.max(0, Math.min(1, redRatio)) * 100);
  fill.style.width = `${pct}%`;
  lbl.textContent = `RED ${pct}% · MOMENTUM · ${100 - pct}% BLUE`;
}
// momentum: what each corner has done to the other, on the judges' terms (match.py scoreRound: speed, head snap,
// a stagger), plus a knockdown; a KO is all of it. Even (50 %) until somebody lands.
const MOM = { red: 0, blue: 0 };
const momentum = () => updateMomentum((MOM.red + 20) / (MOM.red + MOM.blue + 40));   // a prior of 20 a side: one slap tilts it, it does not settle it
let resultTimer = 0;

// ---- the events: the local sim and the live feed both land here --------------------------------------------
function onEvent(e) {
  switch (e.type) {
    case "match_start":
      S.firstStriker = e.first || "red";
      stopTenCount();
      renderPips(1, S.firstStriker, false, false);
      banner(e.match ? `MATCH ${e.match}` : "POWER SLAP BOUT", `★ ${String(e.first || "Red").toUpperCase()} WINS COIN TOSS · STRIKES FIRST ★`, 3.5, "var(--gold)");
      S.cards = [[0, 0], [0, 0], [0, 0]]; S.strikes = 0; S.kos = 0; S.knockdowns = 0; S.fouls = 0; S.turnsDone = 0;
      clearTimeout(resultTimer);
      MOM.red = MOM.blue = 0; momentum();
      S.phase = LIVE ? "live" : "practice";
      $("cardsLine").textContent = ""; renderCards();
      sound.bell();
      for (const w of wear) Object.assign(w, { local: null, at: -99, speed: 0 });
      break;
    case "turn_start":
      stopTenCount();
      S.round = e.round || S.round;
      renderPips(S.round, e.striker, false, false);
      $("round").textContent = `Round ${S.round} / 3`;
      $("turn").textContent = "";
      $("turn").append(cornerNode(e.striker), document.createTextNode(" strikes"));
      $("call").textContent = "";
      setPickOpen(true);       // the next strike is up for grabs
      break;
    case "strike": {
      const palm = !!e.palm;
      S.lastStrike = { striker: e.striker, part: e.part, palm, speed: e.speed, t: e.t };
      S.strikes++;
      renderPips(S.round, e.striker, true, false);
      $("call").textContent = `${e.speed.toFixed(1)} m/s · ${String(e.part || "").replace("_r", "")}${palm ? " · palm" : ""}`;
      banner(e.speed >= 8 ? (palm ? "Clean Palm Strike" : "Clean Hit") : (palm ? "Palm Strike" : "Hit"),
        `${e.speed.toFixed(1)} m/s${palm ? "" : " · not the palm"}`, 1.4, palm ? "var(--gold)" : "");
      sound.strike(e.speed, e.pos ? e.pos[0] : 0);
      if (e.pos) struck(e.striker, e.pos, e.speed);   // (Unreal's strike line has no contact point)
      break;
    }
    case "impact":   // Unreal's broadcast science (the browser sim does not write it)
      S.impact = { hic: e.hic, bric: e.bric, chain: e.chain, order: e.chain_order };
      break;
    case "faint":
      $("call").textContent = `${e.athlete} passes out`;
      banner("Out cold!", `${e.athlete} passes out`, 2, "var(--gold)");
      break;
    case "knockdown":
      S.knockdowns++;
      startTenCount();
      $("call").textContent = `${e.athlete} down (${e.part})`;
      if (e.athlete === "red" || e.athlete === "blue") { S.danger[e.athlete] = 1; MOM[e.athlete === "red" ? "blue" : "red"] += 15; momentum(); }
      sound.knockdown(e.athlete === "red" ? -0.4 : 0.4);
      break;
    case "foul":
      S.fouls++;
      $("call").textContent = `Foul: ${e.athlete} ${e.why}`;
      break;
    case "turn_end": {
      S.turnsDone = (S.turnsDone || 0) + 1;
      if (e.ko) finishTenCount(); else stopTenCount();   // a KO: the count runs out to 10
      renderPips(S.round, e.striker, true, false);
      const call = e.ko ? "KO" : e.end === "recovered" ? `Recovered · head snap ${Number(e.head_peak).toFixed(1)} m/s`
        : (e.fouls && e.fouls.length) ? `Foul: ${foulText(e.fouls)}` : e.end;
      $("call").textContent = call;
      if (e.ko) sound.bell(true);
      else sound.reaction(!!e.stagger, e.touch !== null && e.touch !== undefined, Math.min(1, (e.speed || 0) / 10));
      if (e.ko) { S.kos++; banner("KNOCKOUT!", `${e.striker ? e.striker.toUpperCase() : "Striker"} wins by KO`, 2.2, "var(--gold)"); }
      if (e.touch !== null && e.touch !== undefined && (e.striker === "red" || e.striker === "blue")) {
        MOM[e.striker] += (e.speed || 0) + 5 * (e.head_peak || 0) + (e.stagger ? 10 : 0);
        momentum();
      }
      // the danger: the head snap the defender absorbed (the live feed carries no capture point)
      if (e.touch !== null && e.touch !== undefined && (e.striker === "red" || e.striker === "blue")) {
        const other = e.striker === "red" ? "blue" : "red";
        S.danger[other] = Math.max(S.danger[other], Math.min(1, (e.head_peak || 0) / DANGER_FULL));
        if (LIVE) S.danger.source = "live";
      }
      resolvePick(e);
      break;
    }
    case "round":
      S.cards = e.cards || S.cards;
      renderCards();
      break;
    case "result":
      if (e.how !== "KO") stopTenCount();   // a KO's count runs out to 10 (turn_end)
      renderPips(3, "", true, true);
      S.cards = e.cards || S.cards;
      renderCards();
      S.phase = "result";
      {
        const show = () => banner(e.winner ? `🏆 ${e.winner.toUpperCase()} WINS! 🏆` : "MATCH DRAW", `${String(e.how || "").toUpperCase()} · FINAL`, 6, e.winner === "red" ? "var(--red)" : e.winner === "blue" ? "var(--blue)" : "var(--gold)");
        clearTimeout(resultTimer);
        if (e.how === "KO") resultTimer = setTimeout(show, 2200); else show();   // the KNOCKOUT! call has its moment first
      }
      // the match is over: the turn line and the clock say so, not the last turn's
      $("turn").textContent = "";
      if (e.winner === "red" || e.winner === "blue") {
        $("turn").append(cornerNode(e.winner), document.createTextNode(` wins · ${e.how}`));
        if (e.how === "KO") updateMomentum(e.winner === "red" ? 1 : 0);   // a KO is all of it
      } else $("turn").textContent = "Draw";
      $("clock").textContent = "final";
      if (LIVE && e.winner) liveSeries[e.winner === "red" ? 0 : 1]++;
      { const sr = LIVE ? liveSeries : game.match.series; S.series = [sr[0], sr[1]]; }
      renderSheet();
      break;
  }
}
function renderCards() {
  const line = $("cardsLine");
  line.textContent = "";
  const label = document.createElement("span"); label.textContent = "CARDS ";
  const b = document.createElement("b");
  b.textContent = S.cards.map(c => `${c[0]}–${c[1]}`).join("  ");
  line.append(label, b);
}

// ---- the danger meter (the Unreal F6 one, in the browser) ---------------------------------------------------
const dmeters = { red: null, blue: null };
const heat = v => `hsl(${Math.round(120 * (1 - v))}, 82%, 55%)`;
function buildDeters() {
  const host = $("dmeters");
  host.textContent = "";
  for (const side of ["red", "blue"]) {
    const row = document.createElement("div"); row.className = "drow";
    const lbl = document.createElement("span"); lbl.className = `lbl ${side}`; lbl.textContent = side.toUpperCase();
    const bar = document.createElement("span"); bar.className = "bar";
    const fill = document.createElement("i"); bar.appendChild(fill);
    const val = document.createElement("span"); val.className = "val";
    const src = document.createElement("span"); src.className = "src";
    row.append(lbl, bar, val, src);
    host.appendChild(row);
    dmeters[side] = { fill, val, src };
  }
}
function updateDeters(vals, source) {
  for (const side of ["red", "blue"]) {
    const v = Math.max(0, Math.min(1, vals[side] || 0));
    dmeters[side].fill.style.width = `${Math.round(v * 100)}%`;
    dmeters[side].fill.style.background = heat(v);
    dmeters[side].val.textContent = `${Math.round(v * 100)}%`;
    dmeters[side].src.textContent = source;
  }
}
// The capture point (USlapController::Wobble): where the centre of mass would come to rest over a step, x + v / w0
// with w0 = sqrt(g / height), against the support polygon of the soles. Outside it, standing still is no longer
// possible: he has to step or fall. 0 inside, 1 at 15 cm out - the same scale Unreal draws.
function localDanger() {
  const m = game.m, d = game.d;
  const g = 9.81;
  return game.match.sides.map(x => {
    const C = [d.subtree_com[3 * x.pelvis], d.subtree_com[3 * x.pelvis + 1], d.subtree_com[3 * x.pelvis + 2]];
    const V = d.subtree_linvel ? [d.subtree_linvel[3 * x.pelvis], d.subtree_linvel[3 * x.pelvis + 1]] : [0, 0];
    const w0 = Math.sqrt(g / Math.max(C[2], 0.2));
    const cp = [C[0] + V[0] / w0, C[1] + V[1] / w0];
    const P = [];
    for (const geom of x.feet) {
      const X = [d.geom_xpos[3 * geom], d.geom_xpos[3 * geom + 1], d.geom_xpos[3 * geom + 2]];
      const Sz = [m.geom_size[3 * geom], m.geom_size[3 * geom + 1], m.geom_size[3 * geom + 2]];
      if (X[2] - Sz[2] > 0.03) continue;   // the sole is off the floor: it holds nothing
      const R = [d.geom_xmat[9 * geom], d.geom_xmat[9 * geom + 1], d.geom_xmat[9 * geom + 2],
        d.geom_xmat[9 * geom + 3], d.geom_xmat[9 * geom + 4], d.geom_xmat[9 * geom + 5],
        d.geom_xmat[9 * geom + 6], d.geom_xmat[9 * geom + 7], d.geom_xmat[9 * geom + 8]];
      for (let k = 0; k < 4; k++) {
        const Lx = (k & 1) ? Sz[0] : -Sz[0], Ly = (k & 2) ? Sz[1] : -Sz[1];
        P.push([X[0] + R[0] * Lx + R[1] * Ly, X[1] + R[3] * Lx + R[4] * Ly]);
      }
    }
    if (P.length < 4) return 1;   // both feet off the floor
    const H = hull(P), cross = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
    let inside = true, gap = Infinity;
    for (let i = 0; i < H.length; i++) {
      const E0 = H[i], E1 = H[(i + 1) % H.length];
      inside = inside && cross(E0, E1, cp) >= 0;
      const ex = E1[0] - E0[0], ey = E1[1] - E0[1];
      const t = Math.max(0, Math.min(1, ((cp[0] - E0[0]) * ex + (cp[1] - E0[1]) * ey) / Math.max(ex * ex + ey * ey, 1e-12)));
      gap = Math.min(gap, Math.hypot(cp[0] - (E0[0] + t * ex), cp[1] - (E0[1] + t * ey)));
    }
    return inside ? 0 : Math.min(1, gap / 0.15);
  });
}
function hull(P) {   // Andrew's monotone chain, counter-clockwise (the Unreal hull)
  const pts = P.slice().sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const cross = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
  const out = [];
  for (const pass of [0, 1]) {
    const start = out.length;
    for (let i = 0; i < pts.length; i++) {
      const Q = pts[pass ? pts.length - 1 - i : i];
      while (out.length >= start + 2 && cross(out[out.length - 2], out[out.length - 1], Q) <= 0) out.pop();
      out.push(Q);
    }
    out.pop();
  }
  return out;
}

// ---- the bottom sheet: Telemetry / Science / Roster / Settings ---------------------------------------------
const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; };
const kv = rows => { const d = el("dl", "kv"); for (const [k, v] of rows) { d.append(el("dt", "", k), el("dd", "", v)); } return d; };
const row = (...kids) => { const r = el("div", "row"); r.append(...kids); return r; };
const btn = (label, act, pressed) => { const b = el("button", "", label); b.dataset.act = act; if (pressed !== undefined) b.setAttribute("aria-pressed", String(pressed)); return b; };

let tab = "telemetry", sheetOpen = false;
const TABS = {
  telemetry: () => {
    const wrap = el("div");
    wrap.append(kv([
      ["phase", S.phase],
      ["physics steps", String(S.steps)],
      ["simulated", `${S.simTime.toFixed(1)} s`],
      ["render", `${fpsNow} fps`],
      ["pick streak", `${streak.n} (best ${streak.best})`],
      ["feed", LIVE ? (S.live ? "live" : "connecting") : "local sim"],
    ]));
    if (LIVE) wrap.append(el("div", "note", LIVE));
    return [wrap];
  },
  science: () => {
    const wrap = el("div");
    const t = el("table");
    const head = el("thead"), hr = el("tr");
    for (const h of ["Judge", "Red", "Blue"]) hr.append(el("th", "", h));
    head.appendChild(hr); t.appendChild(head);
    const body = el("tbody");
    S.cards.forEach((c, j) => {
      const tr = el("tr");
      tr.append(el("th", "", String(j + 1)), el("td", "r", String(c[0])), el("td", "b", String(c[1])));
      body.appendChild(tr);
    });
    t.appendChild(body);
    wrap.append(t);
    wrap.append(kv([
      ["series", `${S.series[0]} – ${S.series[1]}`],
      ["danger RED", `${Math.round((S.danger.red || 0) * 100)}%`],
      ["danger BLUE", `${Math.round((S.danger.blue || 0) * 100)}%`],
      ["danger source", S.danger.source],
      ["strikes", String(S.strikes)],
      ["knockdowns", String(S.knockdowns)],
      ["knockouts", String(S.kos)],
      ["fouls", String(S.fouls)],
      ["last strike", S.lastStrike ? `${S.lastStrike.speed.toFixed(1)} m/s ${S.lastStrike.palm ? "palm" : "no palm"}` : "–"],
    ]));
    if (S.impact) wrap.append(kv([["head g (HIC15)", String(S.impact.hic)], ["brain (BRIC)", String(S.impact.bric)],
      ["kinetic chain", (S.impact.chain || []).join(" · ")]]));
    return [wrap];
  },
  roster: () => {
    const wrap = el("div");
    wrap.append(el("h3", "", "Matchup"));
    const links = el("div", "matchups");
    for (const [k, v] of Object.entries(MATCHUPS)) {
      const a = el("a", "", v.name + (v.untrained ? " *" : ""));
      a.href = `?m=${k}` + (LIVE ? `&live=${encodeURIComponent(LIVE)}` : "");
      a.setAttribute("aria-current", String(k === MATCHUP));
      links.appendChild(a);
    }
    wrap.append(links);
    if (MATCHUPS[MATCHUP].untrained) wrap.append(el("div", "note", "* never trained together: expect falls"));
    wrap.append(el("h3", "", "Athletes"));
    game.match.sides.forEach((s, i) => {
      const c = s.card, l = league[game.matchup.corners[i].brain];
      const box = el("div", "athlete");
      const b = el("b", s.name === "red" ? "red" : "blue", c.display);
      box.append(b, el("span", "", `${c.height_m} m · ${c.mass_kg} kg · STR ${c.strength} · SPD ${c.speed}`));
      if (l) box.append(el("span", "", `ELO ${Math.round(l.elo)} · KO ${Math.round(100 * l.ko_rate)}%`));
      wrap.append(box);
    });
    return [wrap];
  },
  settings: () => {
    const wrap = el("div");
    const play = row(
      btn(paused ? "Play" : "Pause", "pause", paused),
      btn("¼×", "speed", speed === 0.25), btn("1×", "speed", speed === 1), btn("2×", "speed", speed === 2),
      btn("Camera", "cam"), btn("Physics", "phys", art.physics));
    wrap.append(el("h3", "", "Playback"), play);
    wrap.append(el("h3", "", "Sound and picture"), row(btn(sound.muted ? "Sound off" : "Sound on", "sound", !sound.muted),
      ...["auto", ...TIERS.slice().reverse()].map(t => btn(t === "auto" ? `Auto (${look.tier})` : t[0].toUpperCase() + t.slice(1), "gfx", look.auto ? t === "auto" : t === look.tier))));
    wrap.append(el("h3", "", "Session"), row(btn("Back to the menu", "reset")));
    const note = LIVE
      ? `Watching the live match on ${LIVE}. The relay's API answers on the same host: /api/match, /api/events, /api/events/stream.`
      : "A local match: the browser engine runs the same rules as training/match.py.";
    wrap.append(el("div", "note", note));
    wrap.append(el("div", "note", `build ${BUILD} · OBS 136 · 30 motors · 200 Hz`));
    return [wrap];
  },
};
function renderSheet() {
  const panel = $("panel");
  panel.textContent = "";
  if (sheetOpen) panel.append(...TABS[tab]());
  renderBadges();
}
function renderBadges() {
  $("bTelemetry").textContent = S.steps > 999 ? `${(S.steps / 1000).toFixed(1)}k` : String(S.steps);
  $("bScience").textContent = `R${S.round}`;
  $("bRoster").textContent = MATCHUP.toUpperCase();
  const b = $("bSettings");
  b.textContent = LIVE ? (S.live ? "LIVE" : "…") : "SIM";
  b.classList.toggle("hot", !!(LIVE && S.live));
}
function setTab(t) {
  tab = t;
  for (const b of $("tabs").children) b.setAttribute("aria-selected", String(b.dataset.tab === t));
  sheetOpen = true;
  $("sheet").hidden = false;
  renderSheet();
  measureBottom();
}
function toggleSheet(force) {
  sheetOpen = force === undefined ? !sheetOpen : force;
  $("sheet").hidden = !sheetOpen;
  for (const b of $("tabs").children) b.setAttribute("aria-selected", String(sheetOpen && b.dataset.tab === tab));
  renderSheet();
  measureBottom();
}
$("tabs").addEventListener("click", e => {
  const b = e.target.closest("button[data-tab]");
  if (!b) return;
  if (sheetOpen && b.dataset.tab === tab) toggleSheet(false); else setTab(b.dataset.tab);
});
$("gear").onclick = () => setTab("settings");
$("panel").addEventListener("click", e => {
  const b = e.target.closest("button[data-act]");
  if (!b) return;
  const act = b.dataset.act;
  if (act === "pause") { paused = !paused; renderSheet(); }
  else if (act === "speed") { speed = +b.textContent.replace("×", ""); renderSheet(); }
  else if (act === "cam") { view = (view + 1) % VIEWS.length; setView(view); }
  else if (act === "phys") { art.physics = !art.physics; showArt(); renderSheet(); }
  else if (act === "reset") { showMenu(); }
  else if (act === "sound") { sound.unlock(); sound.setMuted(!sound.muted); renderSheet(); }
  else if (act === "gfx") { const t = b.textContent.toLowerCase().split(" ")[0]; look.setTier(t); localStorage.setItem("poslap.gfx", t); renderSheet(); }
});
addEventListener("keydown", e => {
  if (e.key === "Tab") { e.preventDefault(); toggleSheet(); }   // TAB: the detailed stats panel
  else if (e.key === "Escape") { if (sheetOpen) toggleSheet(false); else showMenu(); }
});

// ---- the menu: menu -> live or practice -> result -> reset --------------------------------------------------
const ROSTER_SLOTS = [
  { id: "grandpa", display: "GRANDPA" },
  { id: "kim", display: "KIM" },
  { id: "matt", display: "MATT" },   // no striking brain yet (L2 has not graduated): shown, not picked
];
while (ROSTER_SLOTS.length < 16) {
  ROSTER_SLOTS.push({ id: null, display: null, empty: true });
}
// a character plays here when some pairing on this page has him in a corner
const playable = slot => !!slot.id && Object.values(MATCHUPS).some(m => m.corners.some(c => c.char === slot.id));
let selFighters = [0, 1];   // set from the loaded pairing once it is known (renderMatchups)
let activeCornerPick = 0;

function updateRoster16UI() {
  const rCard = ROSTER_SLOTS[selFighters[0]];
  const bCard = ROSTER_SLOTS[selFighters[1]];
  if ($("lblRed")) $("lblRed").textContent = rCard.display || "OPEN";
  if ($("lblBlue")) $("lblBlue").textContent = bCard.display || "OPEN";
  if ($("btnPickRed")) $("btnPickRed").classList.toggle("active", activeCornerPick === 0);
  if ($("btnPickBlue")) $("btnPickBlue").classList.toggle("active", activeCornerPick === 1);
  if ($("slotPrompt")) $("slotPrompt").textContent = activeCornerPick === 0 
    ? "Tap an available slot for RED (P1)" 
    : "Tap an available slot for BLUE (P2)";

  const grid = $("roster16");
  if (!grid) return;
  grid.textContent = "";
  ROSTER_SLOTS.forEach((slot, i) => {
    const b = el("button", slot.empty ? "empty" : "");
    const isRed = selFighters[0] === i;
    const isBlue = selFighters[1] === i;
    if (isRed && isBlue) b.classList.add("selBoth");
    else if (isRed) b.classList.add("selRed");
    else if (isBlue) b.classList.add("selBlue");

    if (slot.empty) {
      b.textContent = `${i + 1} —`;
      b.disabled = true;
    } else if (!playable(slot)) {
      b.textContent = `${i + 1}. ${slot.display} · in training`;
      b.classList.add("empty");
      b.disabled = true;
    } else {
      let tag = "";
      if (isRed && isBlue) tag = " [1 and 2]";
      else if (isRed) tag = " [P1]";
      else if (isBlue) tag = " [P2]";
      b.textContent = `${i + 1}. ${slot.display}${tag}`;
      b.onclick = () => {
        selFighters[activeCornerPick] = i;
        activeCornerPick = 1 - activeCornerPick;
        updateRoster16UI();
      };
    }
    grid.appendChild(b);
  });
}

if ($("btnPickRed")) $("btnPickRed").onclick = () => { activeCornerPick = 0; updateRoster16UI(); };
if ($("btnPickBlue")) $("btnPickBlue").onclick = () => { activeCornerPick = 1; updateRoster16UI(); };

function showMenu() {
  S.phase = "menu";
  started = false;
  if (sheetOpen) toggleSheet(false);   // the menu, not the stats behind it
  $("menu").hidden = false;
  updateRoster16UI();
}
let everStarted = false;   // a match ran before the menu came back: START begins a fresh one, not the old one mid-turn
async function startPractice() {
  const key = matchupKey(ROSTER_SLOTS[selFighters[0]].id, ROSTER_SLOTS[selFighters[1]].id);
  if (!key) { $("slotPrompt").textContent = "No arena for that pairing yet"; return; }
  if (key !== MATCHUP) {   // another pairing: its own arena and brains
    const url = new URL(location.href);
    url.searchParams.set("m", key);
    url.searchParams.set("go", "1");   // straight into the match, no second START
    location.href = url.toString();
    return;
  }
  if (everStarted) {
    while (busy) await new Promise(r => setTimeout(r, 5));   // a physics batch in flight finishes first
    S.danger = { red: 0, blue: 0, source: "local" };
    game.match.newMatch();
  }
  $("menu").hidden = true;
  S.phase = "practice";
  started = everStarted = true;
}
$("practice").onclick = startPractice;
$("watchlive").onclick = () => { $("menu").hidden = true; S.phase = "live"; };
function renderMatchups() {
  game.matchup.corners.forEach((c, i) => {   // the picks open on the pairing that is loaded
    const s = ROSTER_SLOTS.findIndex(x => x.id === c.char);
    if (s >= 0) selFighters[i] = s;
  });
  updateRoster16UI();
  const host = $("menuMatch");
  if (host) {
    host.textContent = "";
    for (const [k, v] of Object.entries(MATCHUPS)) {
      const a = el("a", "", v.name + (v.untrained ? " *" : ""));
      a.href = `?m=${k}` + (LIVE ? `&live=${encodeURIComponent(LIVE)}` : "");
      a.setAttribute("aria-current", String(k === MATCHUP));
      host.appendChild(a);
    }
    if (MATCHUPS[MATCHUP].untrained) host.append(el("span", "note", "* never trained together: expect falls"));
  }
  $("menuFoot").textContent = `build ${BUILD} · ${MATCHUPS[MATCHUP].name}`;
}

// ---- the layout: the dock's height is a CSS variable, so the HUD never sits under it -------------------------
function measureBottom() {
  const h = $("bottom").getBoundingClientRect().height;
  document.documentElement.style.setProperty("--bottomh", `${Math.round(h)}px`);
}
// ---- 3D -------------------------------------------------------------------------------------------------
const canvas = $("view");
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.shadowMap.enabled = true; renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 1.4;   // the arena art is dark at 1.05 (Unreal meters on the lit stage)
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0b0b0d); scene.fog = new THREE.Fog(0x0b0b0d, 8, 18);
const camera = new THREE.PerspectiveCamera(38, 1, 0.05, 50);
camera.up.set(0, 0, 1);   // MuJoCo is Z-up
const controls = new OrbitControls(camera, canvas);
// from -Y: red (-X) on the screen's left, under the HUD's red-first rows and the RED pick (Unreal's director does the same)
const VIEWS = [{ pos: [0, -4.6, 1.5], at: [0, 0, 1.05] }, { pos: [2.6, -2.6, 2.4], at: [0, 0, 1.1] }, { pos: [-2.4, -0.9, 1.75], at: [0.2, 0, 1.4] }];
let view = 0;
function setView(i) { const v = VIEWS[i]; camera.position.set(...v.pos); controls.target.set(...v.at); controls.update(); }
setView(0);

scene.add(new THREE.HemisphereLight(0x8890a8, 0x201814, 1.1));
const key = new THREE.SpotLight(0xfff1dc, 60, 12, 0.55, 0.5, 1.2);
key.position.set(0.3, -1.2, 5.5); key.target.position.set(0, 0, 1); key.castShadow = true; key.shadow.mapSize.set(2048, 2048);
scene.add(key, key.target);
const look = new Look(renderer, scene, camera, key);   // the post chain, the beam, the spray (fx.js)
look.setTier(new URLSearchParams(location.search).get("gfx") || localStorage.getItem("poslap.gfx") || "auto");
const sound = new Sound();
addEventListener("pointerdown", () => sound.unlock(), { once: true });   // browsers start audio on a gesture
addEventListener("keydown", () => sound.unlock(), { once: true });
for (const [x, c] of [[-2.4, 0xff8a5c], [2.4, 0x7aa0ff]]) { const r = new THREE.PointLight(c, 6, 6); r.position.set(x, 1.6, 2.6); scene.add(r); }   // rims behind the athletes, across from the camera

// the mat: dark floor, a red half and a blue half under the corners, a white ring, the foot boxes
const mat = new THREE.Group(); scene.add(mat);   // the stand-in mat and floor, until the arena art loads
const disc = (r0, r1, color, a0 = 0, len = Math.PI * 2, z = 0.001) => {
  const m = new THREE.Mesh(new THREE.RingGeometry(r0, r1, 96, 1, a0, len), new THREE.MeshStandardMaterial({ color, roughness: 0.85 }));
  m.position.z = z; m.receiveShadow = true; mat.add(m); return m;
};
const floor = new THREE.Mesh(new THREE.PlaneGeometry(40, 40), new THREE.MeshStandardMaterial({ color: 0x141416, roughness: 1 }));
floor.receiveShadow = true; mat.add(floor);
disc(0, 2.6, 0x7e2a26, Math.PI / 2, Math.PI);      // red corner at -X
disc(0, 2.6, 0x26407e, -Math.PI / 2, Math.PI);     // blue corner at +X
disc(2.6, 2.75, 0xe9e6dd, 0, Math.PI * 2, 0.002);
disc(0, 1.35, 0x3a3a40, 0, Math.PI * 2, 0.0015).material.transparent = true;

const shapes = [];   // [mesh, geom id]
const art = { athletes: [], arena: null, physics: false };   // the art view (meshes) or the physics view (shapes)
function buildShapes(m, sides) {
  const owner = g => sides.find(s => s.geoms.has(g));
  for (let g = 0; g < m.ngeom; g++) {
    const type = m.geom_type[g], sz = [m.geom_size[3 * g], m.geom_size[3 * g + 1], m.geom_size[3 * g + 2]];
    let geo;
    if (type === 2) geo = new THREE.SphereGeometry(sz[0], 24, 16);
    else if (type === 3) geo = new THREE.CapsuleGeometry(sz[0], 2 * sz[1], 8, 16).rotateX(Math.PI / 2);   // MuJoCo capsules run along local z
    else if (type === 4) geo = new THREE.SphereGeometry(1, 24, 16).scale(...sz);
    else if (type === 5) geo = new THREE.CylinderGeometry(sz[0], sz[0], 2 * sz[1], 24).rotateX(Math.PI / 2);
    else if (type === 6) geo = new THREE.BoxGeometry(2 * sz[0], 2 * sz[1], 2 * sz[2]);
    else continue;   // the plane is drawn as the mat above
    const s = owner(g), color = s ? COLORS[s.name] : 0x1c1c22;
    const mesh = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ color, roughness: s ? 0.55 : 0.4, metalness: s ? 0 : 0.2 }));
    mesh.castShadow = mesh.receiveShadow = true; mesh.matrixAutoUpdate = false;
    scene.add(mesh); shapes.push([mesh, g]);
  }
}
function footBoxes(d, sides) {
  for (const s of sides) {
    const xs = s.feet.map(f => d.geom_xpos[3 * f]), ys = s.feet.map(f => d.geom_xpos[3 * f + 1]);
    const cx = (xs[0] + xs[1]) / 2, cy = (ys[0] + ys[1]) / 2, hx = 0.2 + 0.13, hy = 0.25 + Math.abs(ys[0] - ys[1]) / 2 + 0.05;
    const pts = [[-hx, -hy], [hx, -hy], [hx, hy], [-hx, hy], [-hx, -hy]].map(([x, y]) => new THREE.Vector3(cx + x, cy + y, 0.004));
    scene.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({ color: 0xf1c24b })));
  }
}
// the drawn hand stays outside the face it hits: at 200 Hz a 10 m/s palm moves 5 cm a step and MuJoCo's soft contact
// lets it sink 4-7 cm into the head sphere; the bone is moved out along the contact normal (drawing only)
const pushers = [];
function buildPushers(m, sides) {
  sides.forEach((s, i) => {
    const o = sides[1 - i], r = m.geom_size[3 * o.head];
    const thick = g => Math.min(m.geom_size[3 * g], m.geom_size[3 * g + 1], m.geom_size[3 * g + 2]);
    const hands = { hand_l: s.hands[0], hand_r: s.hands[1] };
    pushers[i] = (name, p, d) => {
      const g = hands[name];
      if (g === undefined) return;
      const P = d.geom_xpos, dx = P[3 * g] - P[3 * o.head], dy = P[3 * g + 1] - P[3 * o.head + 1], dz = P[3 * g + 2] - P[3 * o.head + 2];
      const dist = Math.hypot(dx, dy, dz), need = r + thick(g) + 0.004 - dist;
      if (need > 0 && dist > 1e-6) p.set(p.x + dx / dist * need, p.y + dy / dist * need, p.z + dz / dist * need);
    };
  });
}
// the skin's wear (Unreal's ASlapHUD): the cheek rings and is pushed in where the slap landed, flushes red round that
// spot (following the head) with the neck damage plus the sting of the last slap, and greys with the lost stamina
const wear = [{ local: null, at: -99, speed: 0, dir: new THREE.Vector3() }, { local: null, at: -99, speed: 0, dir: new THREE.Vector3() }];
const MIN_NECK = 0.5, MIN_STAMINA = 0.7, CLEAN = 8;
const headFrame = (d, b) => [new THREE.Vector3(d.xpos[3 * b], d.xpos[3 * b + 1], d.xpos[3 * b + 2]),
  new THREE.Matrix3().set(...Array.from(d.xmat.subarray(9 * b, 9 * b + 9)))];   // MuJoCo's xmat is row-major, as set() takes it
function struck(striker, pos, speed) {
  const sides = game.match.sides, j = sides.findIndex(s => s.name !== striker);
  const [c, R] = headFrame(game.d, sides[j].headBody), p = new THREE.Vector3(...pos);
  wear[j].local = p.clone().sub(c).applyMatrix3(R.transpose());
  wear[j].dir = c.clone().sub(p).normalize();   // the cheek is pushed toward the head's centre
  wear[j].at = performance.now() / 1000; wear[j].speed = speed;
  look.hit(pos, wear[j].dir, speed);
}
function skinTick(d) {
  const now = performance.now() / 1000;
  art.athletes.forEach((a, i) => {
    const s = game.match.sides[i], w = wear[i];
    const P = w.local && (([c, R]) => w.local.clone().applyMatrix3(R).add(c))(headFrame(d, s.headBody));
    for (const u of a.skins) {
      if (P) { u.flushPoint.value.copy(P); u.ripplePoint.value.copy(P); u.rippleDir.value.copy(w.dir); }
      u.rippleTime.value = now - w.at; u.rippleAmp.value = 0.2 * w.speed;
      const sting = 0.5 * Math.min(1.5, w.speed / CLEAN) * Math.exp(-(now - w.at) / 2);
      u.flush.value = Math.max(0, Math.min(1, (1 - s.neckStrength) / (1 - MIN_NECK) + sting));
      u.tired.value = Math.max(0, Math.min(1, (1 - s.stamina) / (1 - MIN_STAMINA)));
    }
  });
}
// footsteps and skids, from MuJoCo's soles: a sole that lifts 1.5 cm and comes back down is a step; a planted sole
// sliding faster than 0.6 m/s squeaks or scrapes (at most every 0.3 s per foot)
const soles = [];
function feetTick(d, dt) {
  if (!dt) return;
  game.match.sides.forEach((s, i) => s.feet.forEach((g, j) => {
    const k = 2 * i + j, x = d.geom_xpos[3 * g], y = d.geom_xpos[3 * g + 1], z = d.geom_xpos[3 * g + 2];
    const f = soles[k] ??= { z0: z, up: false, x, y, quiet: 0 };
    const v = Math.hypot(x - f.x, y - f.y) / dt;
    f.quiet -= dt;
    if (z > f.z0 + 0.015) f.up = true;
    else if (f.up && z < f.z0 + 0.005) { f.up = false; sound.step(x, 0.25 + 0.25 * Math.random()); }
    else if (!f.up && z < f.z0 + 0.005 && v > 0.6 && v < 5 && f.quiet <= 0) { f.quiet = 0.3; sound.skid(x, v); }
    Object.assign(f, { x, y });
  }));
}
function draw(d) {
  art.athletes.forEach((a, i) => a.update(d, pushers[i]));
  if (art.athletes.length) skinTick(d);
  const P = d.geom_xpos, X = d.geom_xmat;
  for (const [mesh, g] of shapes) {
    const b = 9 * g;
    mesh.matrix.set(X[b], X[b + 1], X[b + 2], P[3 * g], X[b + 3], X[b + 4], X[b + 5], P[3 * g + 1], X[b + 6], X[b + 7], X[b + 8], P[3 * g + 2], 0, 0, 0, 1);
  }
}
const fx = [];
function flash(pos) {
  const ring = new THREE.Mesh(new THREE.TorusGeometry(0.05, 0.012, 8, 32), new THREE.MeshBasicMaterial({ color: 0xffe08a, transparent: true }));
  ring.position.set(...pos); ring.lookAt(camera.position); scene.add(ring); fx.push([ring, 0]);
}
function tickFx(dt) {
  for (let i = fx.length - 1; i >= 0; i--) {
    const f = fx[i]; f[1] += dt; f[0].scale.setScalar(1 + f[1] * 14); f[0].material.opacity = Math.max(0, 1 - f[1] / 0.45);
    if (f[1] > 0.45) { scene.remove(f[0]); f[0].geometry.dispose(); fx.splice(i, 1); }
  }
}
function resize() { const w = innerWidth, h = innerHeight; look.resize(w, h); camera.aspect = w / h; camera.updateProjectionMatrix(); }
addEventListener("resize", resize); resize();

// ---- the loop: physics in real time, drawing every frame ---------------------------------------------------
const get = async (p, kind) => { const r = await fetch("../" + p); if (!r.ok) throw new Error(`${p}: ${r.status}`); return kind === "text" ? r.text() : kind === "json" ? r.json() : new Uint8Array(await r.arrayBuffer()); };
const MATCHUP = MATCHUPS[new URLSearchParams(location.search).get("m")] ? new URLSearchParams(location.search).get("m") : "gg";
let league = {};   // training/models/league.json (training/scouting.py): Elo and tendencies per brain, if it has run
try { league = (await get("training/models/league.json", "json")).brains || {}; } catch { }
let game, speed = 1, paused = false, busy = false, debt = 0, prevT = performance.now();
let started = false;      // the menu gates the match: nothing runs until the spectator picks one
let fpsNow = 0, frames = 0, fpsT = performance.now();

try {
  $("loadmsg").textContent = "Loading MuJoCo…";
  const mj = await loadMujoco();
  $("loadmsg").textContent = "Loading the arena and both brains…";
  game = await loadMatch(mj, ort, get, onEvent, MATCHUP);
  buildShapes(game.m, game.match.sides); footBoxes(game.d, game.match.sides); buildPushers(game.m, game.match.sides);
  loadArt(mj).catch(err => console.error("art:", err));   // the match starts on the shapes; the art swaps in when loaded
  for (const s of game.match.sides) NAMES[s.name] = s.card.display;
  buildDeters(); renderStreak(); renderMatchups(); renderBadges(); renderCards(); measureBottom();
  if (LIVE) { S.phase = "live"; $("watchlive").hidden = false; $("practice").hidden = true; connectLive(); }
  else if (new URLSearchParams(location.search).has("go")) startPractice();   // START picked this pairing on the last page
  if (game.match.phase === "turn") { $("turn").textContent = ""; $("turn").append(cornerNode(game.match.sides[game.match.s].name), document.createTextNode(" strikes")); }   // ?nowalkin: the first turn began before the names were known
  $("loading").remove();
  requestAnimationFrame(loop);
} catch (err) {
  $("loadmsg").textContent = `Could not start: ${err.message}`; console.error(err);
}

async function advance(n) {
  busy = true;
  try { for (let i = 0; i < n; i++) { await game.match.step(); S.steps++; } } finally { busy = false; }
}
function loop(now) {
  const dt = Math.min(0.1, (now - prevT) / 1000); prevT = now;
  if (!paused && !LIVE && started) debt += dt * speed;
  const h = game.match.dt;
  if (!busy && debt >= h) { const n = Math.min(Math.floor(debt / h), 60); debt -= n * h; if (debt > 0.25) debt = 0; advance(n); }
  const M = game.match;
  if (LIVE) {
    const age = (performance.now() - live.at) / 1000;
    $("clock").textContent = age > 3 ? "LIVE · waiting for Unreal" : `LIVE${live.walkIn ? " · walk-in" : ""}${live.slow < 1 ? " · slow motion" : ""}`;
  } else if (M.phase === "walkin") {
    $("round").textContent = "RING WALK-IN";
    $("turn").textContent = `Approaching podium (${Math.round((M.walkInProg || 0) * 100)}%)`;
    $("clock").textContent = `walk-in ${Math.max(0, (M.walkInLeft || 0) * h).toFixed(1)} s`;
  } else if (M.phase === "turn") {
    $("clock").textContent = `${M.touchK === null ? "clock" : "recovery"} ${(M.touchK === null ? Math.max(0, 30 - M.k * h) : Math.max(0, 5 - (M.k - M.touchK) * h)).toFixed(1)} s`;
  } else if (M.phase === "hold") {
    $("clock").textContent = `next turn in ${Math.max(0, M.holdLeft * h).toFixed(1)} s`;
  } else if (M.phase === "after") {
    $("clock").textContent = `final · next match in ${Math.max(0, M.holdLeft * h).toFixed(0)} s`;
  }
  frames++;
  if (now - fpsT > 500) {
    fpsNow = Math.round(frames * 1000 / (now - fpsT)); frames = 0; fpsT = now;
    $("fps").textContent = `${fpsNow} fps`;
    $("steps").textContent = LIVE ? "live feed" : `${S.steps} steps`;
    S.simTime = S.steps * h;
    $("tel").textContent = LIVE ? `live · ${S.strikes} strikes` : `step ${S.steps} · ${S.simTime.toFixed(1)} s`;
    if (!LIVE) {   // the local capture point: the danger the browser's own match is in
      const d = localDanger();
      S.danger.red = d[0]; S.danger.blue = d[1]; S.danger.source = "local";
    }
    updateDeters(S.danger, S.danger.source);
    renderBadges();
  }
  draw(game.d); tickFx(dt * (paused ? 0 : 1)); controls.update();
  feetTick(game.d, paused ? 0 : dt);
  sound.tick(Math.max(S.danger.red || 0, S.danger.blue || 0), Math.min(...game.match.sides.map(s => s.stamina ?? 1)));
  look.render(dt * (paused ? 0 : 1), fpsNow);
  requestAnimationFrame(loop);
}

// ---- second screen ----------------------------------------------------------------------------------------------
const live = { at: 0, slow: 1, walkIn: false };
function connectLive() {
  const azure = LIVE.includes("webpubsub.azure.com");
  const ws = new WebSocket(LIVE, azure ? "json.webpubsub.azure.v1" : undefined);
  ws.onopen = () => { S.live = true; renderBadges(); if (azure) ws.send(JSON.stringify({ type: "joinGroup", group: "live" })); };
  ws.onclose = () => { S.live = false; renderBadges(); setTimeout(connectLive, 3000); };   // Unreal or the relay restarted: keep trying
  ws.onmessage = ev => {
    let msg = JSON.parse(ev.data);
    if (azure) { if (msg.type !== "message") return; msg = typeof msg.data === "string" ? JSON.parse(msg.data) : msg.data; }
    if (msg.t === "ev") { onEvent(msg.e); return; }
    if (msg.t !== "pose") return;
    // another pairing in Unreal: reload on the matchup whose two characters match, if this page has it
    const corners = game.matchup.corners.map(c => c.char);
    if (corners[0] !== msg.c[0] || corners[1] !== msg.c[1]) {
      const key = Object.keys(MATCHUPS).find(k => MATCHUPS[k].corners[0].char === msg.c[0] && MATCHUPS[k].corners[1].char === msg.c[1]);
      if (key) { location.search = `?m=${key}&live=${encodeURIComponent(LIVE)}`; return; }
      $("clock").textContent = `LIVE · ${msg.c.join(" vs ")} is not on this page`; return;
    }
    const q = game.d.qpos, n = 7 + game.match.sides[0].qa.length;
    game.match.sides.forEach((s, i) => {   // [root (7), joints in fingerprint order] per athlete, red then blue
      for (let k = 0; k < 7; k++) q[s.rq + k] = msg.q[n * i + k];
      s.qa.forEach((a, j) => { q[a] = msg.q[n * i + 7 + j]; });
    });
    game.match.mj.mj_forward(game.m, game.d);
    Object.assign(live, { at: performance.now(), slow: msg.slow, walkIn: msg.w });
    if (msg.w) $("turn").textContent = "Approaching the podium";
    else { $("turn").textContent = ""; $("turn").append(cornerNode(msg.s), document.createTextNode(" strikes")); }
  };
}

async function loadArt(mj) {
  const TINTS = { red_: 0xd9473f, blue_: 0x4f7fe0 };
  const [arena, ...athletes] = await Promise.all([loadArena(scene),
    ...game.matchup.corners.map(c => loadAthlete(scene, `./assets/${c.char}.glb`, mj, game.m, c.prefix, TINTS[c.prefix]))]);
  art.arena = arena; art.athletes = athletes; showArt();
}
function showArt() {   // art view: meshes and arena; physics view: every MuJoCo shape on the stand-in mat
  const on = !art.physics && art.arena;
  for (const [mesh] of shapes) mesh.visible = !on;
  mat.visible = !on;
  if (art.arena) art.arena.visible = !art.physics;
  for (const a of art.athletes) a.root.visible = !art.physics;
}
addEventListener("resize", measureBottom);
