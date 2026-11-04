// PoSlap in the browser: the match engine (sim.js) steps MuJoCo at 200 Hz in real time; three.js draws every
// physics shape where MuJoCo puts it (drawing only, as Unreal does). Served from the repo root: Web/serve.py.
//
// The page is a broadcast HUD: the corner anchors (TL the match state, TC the rate, TR the view settings and the
// stats toggle, BL the detailed telemetry, BR the build stamp), the athletes' plates, a bottom dock with the
// spectator's pick and the tab row, and a bottom sheet (Match / Brains / View). TAB (or ≡) shows the detailed stats;
// the default view stays clean. Everything here is display-only: the rules live in sim.js (= training/match.py).
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import loadMujoco from "@mujoco/mujoco";
import * as ort from "onnxruntime-web";
import { loadMatch, MATCHUPS, matchupKey } from "./loader.js";
import { loadArena, loadAthlete, clipPush } from "./art.js";
import { Look, TIERS } from "./fx.js";
import { Sound } from "./audio.js";
import { SwingTagger, Read, Snap, Director, SNAP_S } from "./broadcast.js";

ort.env.wasm.wasmPaths = new URL("./node_modules/onnxruntime-web/dist/", location.href).href;   // relative paths resolve against ort's own file
ort.env.wasm.numThreads = 1;   // no cross-origin isolation needed

const $ = id => document.getElementById(id);
const QS = new URLSearchParams(location.search);
const SIDES = ["red", "blue"];
const COLORS = { red: 0xd9473f, blue: 0x4f7fe0 };
const DANGER_FULL = 12;                    // m/s of head snap that reads as full KO danger (the relay's scale)
const MIN_NECK = 0.5, MIN_STAMINA = 0.7;   // sim.js's fatigue floors (the bars empty at the floor)
const CLEAN = 8;                           // m/s: a clean strike (Blueprint L2)
const CLEAN_MOMENTUM = 3.0 * 9;            // kg m/s: a 3 kg strike mass at 9 m/s, the contact burst's size 1
const NEAR_MISS_DIST = 0.15, NEAR_MISS_SPEED = 4;   // m, m/s: a palm this fast this close to the face, no touch: "ooooh"
const TENSION_DIST = 0.55, TENSION_SPEED = 1.5;   // m, m/s: the palm closing on the face cuts the director to the close shot
const RESULT_HOLD_S = +new URLSearchParams(location.search).get("hold") || 10;   // s the result stays up before the menu comes back (MENU or REMATCH sooner; ?hold= for the screenshot run)
// ?live=ws://<host>:8765 (Web/live.py) or an Azure Web PubSub client URL: draw the match Unreal is playing instead of
// running our own; the physics here only places the bodies where Unreal's MuJoCo put them.
// Only a WebSocket URL we recognise is used: a ?live= parameter is attacker-controlled, and the relay it points at
// sends the text that reaches the HUD, so an unexpected scheme or host must not become a connection.
const LIVE = (() => {
  const raw = QS.get("live");
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
  phase: "menu",       // the flow: menu -> gameplay -> result -> reset -> menu | gameplay
  round: 1, cards: [[0, 0], [0, 0], [0, 0]], series: [0, 0],
  strikes: 0, kos: 0, knockdowns: 0, fouls: 0, lastStrike: null, impact: null,
  danger: { red: 0, blue: 0, source: "local" },
  kd: { red: 0, blue: 0 }, koed: null,            // the badges on the plates
  fatigue: { red: { stamina: 1, neck: 1 }, blue: { stamina: 1, neck: 1 } },
  striker: null, strikeAt: -1, feints: 0, reals: 0, tag: null,
  live: false, steps: 0, simTime: 0,
};
const NAMES = {};   // corner -> athlete (the card's display name), filled once the match loads

// ---- the state flow: every change of screen goes through go(), and only along FLOW ---------------------------
const FLOW = { menu: ["gameplay"], gameplay: ["result", "menu"], result: ["reset"], reset: ["menu", "gameplay"] };
const flowLog = ["menu"];
function go(next) {
  if (!FLOW[S.phase]?.includes(next)) { console.warn(`flow: ${S.phase} -> ${next} refused`); return false; }
  S.phase = next; flowLog.push(next); document.body.dataset.phase = next;
  return true;
}
window.__poslap = { get phase() { return S.phase; }, flow: flowLog };   // read by the screenshot / flow check
document.body.dataset.phase = "menu";

// ---- the spectator's pick: who lands the next strike -------------------------------------------------------
const STREAK_KEY = "poslap.streak.v1";
const streak = { n: 0, best: 0 };
try { const s = JSON.parse(localStorage.getItem(STREAK_KEY) || "null"); if (s) { streak.n = +s.n || 0; streak.best = +s.best || 0; } } catch { }
const saveStreak = () => { try { localStorage.setItem(STREAK_KEY, JSON.stringify(streak)); } catch { } };
let pick = null;        // the pick for the turn in progress: "red" | "blue" | "neither" | null

function setPick(v) {
  pick = v;
  for (const b of $("picks").children) b.setAttribute("aria-pressed", String(b.dataset.pick === v));
}
function setPickOpen(on) {
  for (const b of $("picks").children) b.disabled = !on;   // no turn to predict: the pick is off
  if (!on) setPick(null);
}
function renderStreak() {
  const info = $("pickinfo");
  info.textContent = "";
  info.append(el("span", "n", String(streak.n)), document.createTextNode(" "), el("span", "best", `best ${streak.best}`));
  $("streak").textContent = `pick streak ${streak.n} · best ${streak.best}`;
}
let pickBannerTimer = 0;
function pickBanner(text, kind) {
  const b = $("pickbanner");
  b.textContent = text;
  b.className = `pickbanner on ${kind}`;
  clearTimeout(pickBannerTimer);
  pickBannerTimer = setTimeout(() => { b.className = "pickbanner"; }, 1900);
}
function resolvePick(e) {
  // the turn is over: the strike landed (the striker) or nobody did (the clock, a foul, a fall)
  const answer = landedOf(e) ? e.striker : "neither";
  if (pick === null) pickBanner("NO PICK", "none");
  else if (pick === answer) { streak.n += 1; streak.best = Math.max(streak.best, streak.n); pickBanner(`CORRECT · streak ${streak.n}`, "good"); }
  else { streak.n = 0; pickBanner("MISSED", "bad"); }
  saveStreak();
  renderStreak();
  setPickOpen(false);
}
$("picks").addEventListener("click", e => {
  const b = e.target.closest("button[data-pick]");
  if (b && !b.disabled) setPick(b.dataset.pick);
});

// ---- small DOM helpers (text only: no wire string is ever parsed as HTML) ---------------------------------------
const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text !== undefined) n.textContent = text; return n; };
const kv = rows => { const d = el("dl", "kv"); for (const [k, v] of rows) { const p = el("div"); p.append(el("dt", "", k), el("dd", "", v)); d.append(p); } return d; };
const row = (...kids) => { const r = el("div", "row"); r.append(...kids); return r; };
const btn = (label, act, pressed, val) => { const b = el("button", "", label); b.dataset.act = act; if (val !== undefined) b.dataset.val = val; if (pressed !== undefined) b.setAttribute("aria-pressed", String(pressed)); return b; };
const other = side => side === "red" ? "blue" : "red";
const isSide = s => s === "red" || s === "blue";
const landedOf = e => e.touch !== null && e.touch !== undefined;
const clamp01 = v => Math.max(0, Math.min(1, v));
// a long name shrinks to fit its box, down to the 14 px floor, then the ellipsis takes over
function fit(node, max, min = 14) {
  let f = max;
  node.style.fontSize = `${f}px`;
  while (f > min && node.scrollWidth > node.clientWidth + 0.5) node.style.fontSize = `${--f}px`;
}

// ---- the banner -------------------------------------------------------------------------------------------
let bannerTimer = 0;
function banner(big, small = "", seconds = 1.6, color = "") {
  $("bannerBig").textContent = big; $("bannerBig").style.color = color; $("bannerSmall").textContent = small;
  $("banner").classList.add("on"); clearTimeout(bannerTimer);
  bannerTimer = setTimeout(() => $("banner").classList.remove("on"), seconds * 1000);
}
// "RED strikes": the corner word coloured; the name is on the plate
function cornerLine(n, rest) {
  const t = $("turn");
  t.textContent = "";
  t.append(el("span", n === "blue" ? "blue" : "red", String(n).toUpperCase()), document.createTextNode(rest));
}
const foulText = fouls => (fouls || []).map(f => Array.isArray(f) ? f.join(" ") : String(f)).join(", ");

function renderPips(round, turnStriker, struck, finished) {
  const first = S.firstStriker || "red";
  const activeTurn = finished ? S.turnsDone : Math.min(5, Math.max(0, (round - 1) * 2 + (turnStriker === first ? 0 : 1)));
  // a finished match: the turns it played, then "–" for those a KO cut off
  const pip = t => finished ? (t < S.turnsDone ? "●" : "–") : (t < activeTurn || (t === activeTurn && struck) ? "●" : t === activeTurn ? "◈" : "○");
  $("pipsTrack").textContent = `R${Math.min(3, round)}/3 ` + [0, 1, 2].map(r => pip(2 * r) + pip(2 * r + 1)).join(" ");
}

let countTimer = null, count = 0;
function startTenCount() {
  $("tencount").hidden = false;
  count = 1;
  $("tcNum").textContent = count;
  runCount(1000);
}
function runCount(ms) {   // the referee's count; a KO finishes it quickly to 10 instead of cutting it off
  clearInterval(countTimer);
  countTimer = setInterval(() => {
    count++;
    if (count > 10) { clearInterval(countTimer); $("tencount").hidden = true; }
    else $("tcNum").textContent = count;
  }, ms);
}
function finishTenCount() {
  if ($("tencount").hidden) { $("tencount").hidden = false; count = 0; }   // a KO with no knockdown call first
  runCount(170);
}
function stopTenCount() { clearInterval(countTimer); $("tencount").hidden = true; }

// momentum: what each corner has done to the other, on the judges' terms (match.py scoreRound: speed, head snap,
// a stagger), plus a knockdown; a KO is all of it. Even (50 %) until somebody lands.
const MOM = { red: 0, blue: 0 };
function updateMomentum(redRatio) {
  const pct = Math.round(clamp01(redRatio) * 100);
  $("momentumFill").style.width = `${pct}%`;
  $("momRed").textContent = `${pct}%`; $("momBlue").textContent = `${100 - pct}%`;
}
const momentum = () => updateMomentum((MOM.red + 20) / (MOM.red + MOM.blue + 40));   // a prior of 20 a side: one slap tilts it, it does not settle it

// ---- the plates: name, badges, stamina and neck bars, the read (defender) or the swing tag (striker) ----------------
const plates = {};
function buildPlates() {
  for (const side of SIDES) {
    const host = $(side === "red" ? "plateRed" : "plateBlue");
    host.textContent = "";
    const name = el("div", "pname"), nm = el("span", "nm", NAMES[side] || side.toUpperCase()), badges = el("span", "pbadges");
    name.append(nm, badges);
    const meter = label => {
      const m = el("div", "meter"), bar = el("span", "bar"), fill = el("i"), v = el("span", "v", "100%");
      bar.append(fill); m.append(el("span", "", label), bar, v); host.append(m);
      return { fill, v };
    };
    host.append(name);
    const sta = meter("STA"), neck = meter("NECK");
    const cue = el("div", "cue"), rd = el("span", "rd"), rdFill = el("i"), cueText = el("span");
    rd.append(rdFill); cue.append(rd, cueText); host.append(cue);
    plates[side] = { host, nm, badges, sta, neck, cue, rd, rdFill, cueText };
  }
  fitNames();
}
function fitNames() { for (const side of SIDES) if (plates[side]) fit(plates[side].nm, innerWidth < 400 ? 16 : 18); }
function renderBadges2() {
  for (const side of SIDES) {
    const b = plates[side]?.badges;
    if (!b) continue;
    b.textContent = "";
    if (S.kd[side]) b.append(el("span", "pbadge", S.kd[side] > 1 ? `KD×${S.kd[side]}` : "KD"));
    if (S.koed === side) b.append(el("span", "pbadge ko", "KO"));
  }
}
const barColor = v => `hsl(${Math.round(120 * v)}, 70%, 52%)`;
function renderPlates() {
  for (const side of SIDES) {
    const p = plates[side];
    if (!p) continue;
    const f = S.fatigue[side];
    for (const [m, v, floor] of [[p.sta, f.stamina, MIN_STAMINA], [p.neck, f.neck, MIN_NECK]]) {
      const k = clamp01((v - floor) / (1 - floor));   // full when fresh, empty at the floor
      m.fill.style.width = `${Math.round(100 * k)}%`; m.fill.style.backgroundColor = barColor(k);
      m.v.textContent = `${Math.round(100 * v)}%`;
    }
    p.host.classList.toggle("striking", S.striker === side && S.phase === "gameplay");
    // the striker's plate shows the swing tag; the defender's shows his brain's read of the striker
    const now = performance.now() / 1000;
    if (S.striker === side && S.tag && now - S.tag.at < 2.2) {
      p.cue.className = `cue ${S.tag.kind === "FEINT" ? "feint" : "real"}`;
      p.rd.style.display = "none"; p.cueText.textContent = S.tag.kind === "FEINT" ? `FEINT · ${S.tag.peak.toFixed(1)} m/s` : `REAL · ${S.tag.peak.toFixed(1)} m/s`;
    } else if (S.striker && S.striker !== side && read.value > 0.05 && S.phase === "gameplay") {
      p.cue.className = "cue"; p.rd.style.display = ""; p.rdFill.style.width = `${Math.round(100 * read.value)}%`;
      p.cueText.textContent = `READ ${read.kind || "…"}`;
    } else { p.cue.className = "cue"; p.rd.style.display = "none"; p.cueText.textContent = ""; }
  }
}

// ---- the broadcast's reading of the match (broadcast.js; display only) -------------------------------------------
const tagger = new SwingTagger(), read = new Read(), snap = new Snap(), director = new Director();
let sparkOnUntil = 0;
// one sample of the striker's palm and the defender's face: per physics step (the local sim) or per pose (live).
// The swing tag reads on brain steps (tagStep), as match.py's feint read does.
function sampleBodies(t, striker, speed, tagStep = true) {
  const M = game.match, d = game.d;
  const si = M.sides.findIndex(x => x.name === striker);
  if (si < 0) return;
  const Sx = M.sides[si], Dx = M.sides[1 - si], G = d.geom_xpos;
  const hand = [G[3 * Sx.hand], G[3 * Sx.hand + 1], G[3 * Sx.hand + 2]], head = [G[3 * Dx.head], G[3 * Dx.head + 1], G[3 * Dx.head + 2]];
  const shoulder = Sx.shR >= 0 ? d.qvel[Sx.va[Sx.shR]] : 0;
  snap.sample(t, head);
  if (S.strikeAt >= 0) return;   // the swing landed: the read and the tag are done for this turn
  read.sample(t, hand, head, shoulder);
  if (tagStep && !liveFeints && tagger.sample(t, speed) === "FEINT") feintTag(tagger.peak);
  const gap = Math.hypot(hand[0] - head[0], hand[1] - head[1], hand[2] - head[2]);
  S.tension = gap < TENSION_DIST && speed > TENSION_SPEED;   // the director's tension cut (SlapHUD.cpp Direct), undelayed
  if (gap < NEAR_MISS_DIST && speed > NEAR_MISS_SPEED) sound.nearMiss(speed / CLEAN);   // (it waits 2.5 s between)
}
let liveFeints = false;   // the live feed writes its own "feint" events (match.py / Unreal): those win over our read
function feintTag(peak) {
  S.feints++; S.tag = { kind: "FEINT", peak, at: performance.now() / 1000 };
  sound.nearMiss(peak / CLEAN);
}
function drawSpark() {
  const c = $("spark"), g = c.getContext("2d"), W = c.width, H = c.height, pad = 6;
  g.clearRect(0, 0, W, H);
  const P = snap.points;
  if (!P.length) return;
  const top = Math.max(6, snap.peak * 1.15);   // the trace fills the box; the KO-danger line shows once it is in range
  const x = t => pad + (W - 2 * pad) * t / SNAP_S, y = v => H - pad - (H - 2 * pad) * v / top;
  g.strokeStyle = "rgb(255 255 255 / .18)"; g.lineWidth = 2;   // the baseline and the KO-danger line (12 m/s)
  g.beginPath(); g.moveTo(pad, y(0)); g.lineTo(W - pad, y(0)); g.stroke();
  if (DANGER_FULL <= top) { g.setLineDash([6, 6]); g.beginPath(); g.moveTo(pad, y(DANGER_FULL)); g.lineTo(W - pad, y(DANGER_FULL)); g.stroke(); g.setLineDash([]); }
  g.strokeStyle = "#f1c24b"; g.lineWidth = 4; g.lineJoin = "round";
  g.beginPath();
  P.forEach(([t, v], i) => (i ? g.lineTo(x(t), y(v)) : g.moveTo(x(t), y(v))));
  g.stroke();
  const pk = P.reduce((a, b) => (b[1] > a[1] ? b : a));   // the peak marker
  g.fillStyle = "#f2f0ea"; g.beginPath(); g.arc(x(pk[0]), y(pk[1]), 5, 0, Math.PI * 2); g.fill();
  $("sparkPeak").textContent = snap.peak.toFixed(1);
}

// ---- the events: the local sim and the live feed both land here --------------------------------------------
function onEvent(e) {
  switch (e.type) {
    case "match_start":
      if (LIVE && S.phase === "result") { go("reset"); go("gameplay"); hideResult(); }
      S.firstStriker = e.first || "red";
      stopTenCount();
      renderPips(1, S.firstStriker, false, false);
      banner(e.match ? `MATCH ${e.match}` : "POWER SLAP BOUT", `${String(e.first || "red").toUpperCase()} WINS THE TOSS · STRIKES FIRST`, 3.5, "var(--gold)");
      Object.assign(S, { cards: [[0, 0], [0, 0], [0, 0]], strikes: 0, kos: 0, knockdowns: 0, fouls: 0, turnsDone: 0, feints: 0, reals: 0,
        kd: { red: 0, blue: 0 }, koed: null, tag: null, strikeAt: -1, decided: false });
      S.fatigue = { red: { stamina: 1, neck: 1 }, blue: { stamina: 1, neck: 1 } };
      clearTimeout(resultTimer);
      MOM.red = MOM.blue = 0; momentum();
      renderCards(); renderBadges2();
      sound.chime(); sound.bell();
      for (const w of wear) Object.assign(w, { local: null, at: -99, speed: 0 });
      break;
    case "turn_start":
      stopTenCount();
      S.round = e.round || S.round;
      S.striker = isSide(e.striker) ? e.striker : null; S.strikeAt = -1; S.tag = null; S.tension = false;
      tagger.reset(); read.reset();
      renderPips(S.round, e.striker, false, false);
      cornerLine(e.striker, " strikes");
      $("call").textContent = "";
      setPickOpen(true);       // the next strike is up for grabs
      break;
    case "strike": {
      const palm = !!e.palm;
      S.lastStrike = { striker: e.striker, part: e.part, palm, speed: e.speed, t: e.t };
      S.strikes++; S.reals++;
      tagger.landed(); read.reset();   // the read is over once the palm lands
      S.tag = { kind: "REAL", peak: e.speed, at: performance.now() / 1000 };
      S.strikeAt = performance.now() / 1000;
      snap.start(sampleClock());
      sparkOnUntil = S.strikeAt + SNAP_S + 2.5;
      renderPips(S.round, e.striker, true, false);
      $("call").textContent = `${e.speed.toFixed(1)} m/s · ${String(e.part || "").replace("_r", "")}${palm ? " · palm" : ""}`;
      banner(e.speed >= CLEAN ? (palm ? "Clean Palm Strike" : "Clean Hit") : (palm ? "Palm Strike" : "Hit"),
        `${e.speed.toFixed(1)} m/s${palm ? "" : " · not the palm"}`, 1.4, palm ? "var(--gold)" : "");
      sound.strike(e.speed, e.pos ? e.pos[0] : 0, e.striker);
      if (window.__poslapLook?.pauseOnStrike) paused = true;   // the report's contact frame (display only)
      if (e.pos) struck(e.striker, e.pos, e.speed);   // (Unreal's strike line has no contact point)
      break;
    }
    case "feint":    // a feed that classifies its own swings (match.py feint_read): the same tag, from the source
      if (LIVE) { liveFeints = true; feintTag(+e.peak || 0); }
      break;
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
      if (isSide(e.athlete)) {
        S.danger[e.athlete] = 1; S.kd[e.athlete]++; MOM[other(e.athlete)] += 15; momentum(); renderBadges2();
        dust(e.athlete, e.part);
      }
      S.downAt = performance.now() / 1000;
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
      const landed = landedOf(e);
      if (e.ko) sound.bell(true);
      else sound.reaction(!!e.stagger, landed, Math.min(1, (e.speed || 0) / 10));
      if (e.ko) {
        S.kos++; if (isSide(e.striker)) { S.koed = other(e.striker); renderBadges2(); }
        banner("KNOCKOUT!", `${e.striker ? e.striker.toUpperCase() : "Striker"} wins by KO`, 2.2, "var(--gold)");
      }
      if (landed && isSide(e.striker)) {
        MOM[e.striker] += (e.speed || 0) + 5 * (e.head_peak || 0) + (e.stagger ? 10 : 0);
        momentum();
        // the danger: the head snap the defender absorbed (the live feed carries no capture point)
        const def = other(e.striker);
        S.danger[def] = Math.max(S.danger[def], Math.min(1, (e.head_peak || 0) / DANGER_FULL));
        if (LIVE) S.danger.source = "live";
      }
      if (LIVE) liveFatigue(e);
      resolvePick(e);
      break;
    }
    case "round":
      S.cards = e.cards || S.cards;
      renderCards();
      break;
    case "result":
      if (e.how !== "KO") stopTenCount();   // a KO's count runs out to 10 (turn_end)
      lastResult = null;
      if (!LIVE && isSide(e.winner)) {
        const w = e.winner === "red" ? 0 : 1;
        lastResult = { text: `${e.winner.toUpperCase()} ${game.match.sides[w].card.display.toUpperCase()} wins by ${e.how}`, elo: rate(w) };
      }
      renderPips(3, "", true, true);
      S.cards = e.cards || S.cards;
      renderCards();
      S.striker = null;
      // the match is over: the turn line and the clock say so, not the last turn's
      if (isSide(e.winner)) {
        cornerLine(e.winner, ` wins · ${e.how}`);
        if (e.how === "KO") updateMomentum(e.winner === "red" ? 1 : 0);   // a KO is all of it
      } else $("turn").textContent = "Draw";
      $("clock").textContent = "final";
      if (LIVE && e.winner) liveSeries[e.winner === "red" ? 0 : 1]++;
      { const sr = LIVE ? liveSeries : game.match.series; S.series = [sr[0], sr[1]]; }
      if (S.phase === "gameplay") go("result");
      clearTimeout(resultTimer);
      resultTimer = setTimeout(() => showResult(e), e.how === "KO" ? 2200 : 300);   // the KNOCKOUT! call has its moment first
      renderSheet();
      break;
  }
}
// Unreal's events carry no fatigue: the live view applies match.py's rule to what the events say (display only)
function liveFatigue(e) {
  if (!isSide(e.striker)) return;
  const sx = game.match.sides.find(x => x.name === e.striker), dx = game.match.sides.find(x => x.name !== e.striker);
  const fs = S.fatigue[e.striker], fd = S.fatigue[other(e.striker)];
  fs.stamina = e.stamina ?? Math.max(MIN_STAMINA, fs.stamina - (sx?.card.stamina_per_strike || 0));
  if (landedOf(e) && !e.ko) fd.neck = Math.max(MIN_NECK, fd.neck - (dx?.card.neck_damage_per_snap || 0) * (e.head_peak || 0));
}
function renderCards() {
  $("cardsLine").textContent = "";
  $("cardsLine").append(document.createTextNode("cards "), el("b", "", S.cards.map(c => `${c[0]}–${c[1]}`).join("  ")));
}

// ---- the result: win or loss, the judges' cards flipped one by one -----------------------------------------------
let resultTimer = 0, resultShownAt = 0;
const FLIP_S = 0.7;   // s between the cards
function showResult(e) {
  const win = isSide(e.winner) ? e.winner : null;
  $("resWho").textContent = win ? `${win.toUpperCase()} WINS` : "DRAW";
  $("resWho").style.color = win ? `var(--${win})` : "var(--gold)";
  $("resHow").textContent = `${win ? (NAMES[win] || "") + " · " : ""}${e.how || ""}`;
  const host = $("judges");
  host.textContent = "";
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  (e.cards || S.cards).forEach((c, j) => {
    const card = el("div", "jcard"), front = el("div", "front", `JUDGE ${j + 1}`), back = el("div", "back");
    const forSide = c[0] > c[1] ? "red" : c[1] > c[0] ? "blue" : "even";
    back.append(el("span", "j", `JUDGE ${j + 1}`), el("span", "sc", `${c[0]}–${c[1]}`), el("span", `for ${forSide}`, forSide === "even" ? "EVEN" : forSide.toUpperCase()));
    card.append(front, back); host.append(card);
    if (reduced) card.classList.add("flip");
    else setTimeout(() => card.classList.add("flip"), 400 + 1000 * FLIP_S * j);
  });
  $("resKo").textContent = e.how === "KO" ? "Stopped by knockout: the cards as they stood" : "";
  $("resElo").textContent = lastResult?.elo || "";
  $("resAgain").hidden = !!LIVE; $("resMenu").hidden = !!LIVE;
  $("result").classList.add("on");
  resultShownAt = performance.now() / 1000;
  banner(win ? `${win.toUpperCase()} WINS!` : "MATCH DRAW", `${String(e.how || "").toUpperCase()} · FINAL`, 2.5, win ? `var(--${win})` : "var(--gold)");
}
function hideResult() { clearTimeout(resultTimer); $("result").classList.remove("on"); }
$("resMenu").onclick = () => showMenu();
$("resAgain").onclick = () => rematch();

// ---- the danger meter (the Unreal F6 one, in the browser) ---------------------------------------------------
const dmeters = {};
const heat = v => `hsl(${Math.round(120 * (1 - v))}, 82%, 55%)`;
function buildDangerMeters() {
  const host = $("dmeters");
  host.textContent = "";
  for (const side of SIDES) {
    const r = el("div", "drow"), bar = el("span", "bar"), fill = el("i"), val = el("span", "val");
    bar.appendChild(fill);
    r.append(el("span", `lbl ${side}`, side.toUpperCase()), bar, val);
    host.appendChild(r);
    dmeters[side] = { fill, val };
  }
}
function updateDangerMeters(vals) {
  for (const side of SIDES) {
    const v = clamp01(vals[side] || 0);
    dmeters[side].fill.style.width = `${Math.round(v * 100)}%`;
    dmeters[side].fill.style.background = heat(v);
    dmeters[side].val.textContent = `${Math.round(v * 100)}%`;
  }
}
const cross = (o, a, b) => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);
// The capture point (USlapController::Wobble): where the centre of mass would come to rest over a step, x + v / w0
// with w0 = sqrt(g / height), against the support polygon of the soles. Outside it, standing still is no longer
// possible: he has to step or fall. 0 inside, 1 at 15 cm out - the same scale Unreal draws.
function localDanger() {
  const m = game.m, d = game.d, g = 9.81;
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
      const R = d.geom_xmat.subarray(9 * geom, 9 * geom + 9);
      for (let k = 0; k < 4; k++) {
        const Lx = (k & 1) ? Sz[0] : -Sz[0], Ly = (k & 2) ? Sz[1] : -Sz[1];
        P.push([X[0] + R[0] * Lx + R[1] * Ly, X[1] + R[3] * Lx + R[4] * Ly]);
      }
    }
    if (P.length < 4) return 1;   // both feet off the floor
    const H = hull(P);
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

// ---- detailed stats (TAB / ≡): the default HUD stays clean ------------------------------------------------------
let detail = false;
function toggleDetail(force) {
  detail = force === undefined ? !detail : force;
  $("hudgrid").classList.toggle("detail", detail);
  $("statsBtn").setAttribute("aria-pressed", String(detail));
}
$("statsBtn").onclick = () => toggleDetail();

// ---- the bottom sheet: Match / Brains / View ------------------------------------------------------------------
const linkFor = k => `?m=${k}` + (LIVE ? `&live=${encodeURIComponent(LIVE)}` : "");
function matchupLinks() {   // the pairings this page can play (the Brains tab)
  const links = el("div", "matchups");
  for (const [k, v] of Object.entries(MATCHUPS)) {
    const a = el("a", "", v.name + (v.untrained ? " *" : ""));
    a.href = linkFor(k);
    a.setAttribute("aria-current", String(k === MATCHUP));
    links.appendChild(a);
  }
  if (MATCHUPS[MATCHUP].untrained) links.append(el("span", "note", "* never trained together: expect falls"));
  return links;
}
let tab = "match", sheetOpen = false;
const TABS = {
  match: () => {
    const t = el("table"), head = el("thead"), hr = el("tr"), body = el("tbody");
    for (const h of ["Judge", "Red", "Blue"]) hr.append(el("th", "", h));
    head.appendChild(hr); t.appendChild(head);
    S.cards.forEach((c, j) => {
      const tr = el("tr");
      tr.append(el("th", "", String(j + 1)), el("td", "r", String(c[0])), el("td", "b", String(c[1])));
      body.appendChild(tr);
    });
    t.appendChild(body);
    const out = [t, kv([
      ["series", `${S.series[0]}–${S.series[1]}`], ["strikes", String(S.strikes)], ["feints", String(S.feints)],
      ["KD", String(S.knockdowns)], ["KO", String(S.kos)], ["fouls", String(S.fouls)],
      ["last", S.lastStrike ? `${S.lastStrike.speed.toFixed(1)} m/s${S.lastStrike.palm ? " palm" : ""}` : "–"],
      ["phase", S.phase], ["feed", LIVE ? (S.live ? "live" : "connecting") : "local sim"],
    ])];
    if (S.impact) out.push(kv([["HIC15", String(S.impact.hic)], ["BRIC", String(S.impact.bric)], ["chain", (S.impact.chain || []).join(" · ")]]));
    if (!LIVE) out.push(row(btn(paused ? "Play" : "Pause", "pause", paused),
      btn("¼×", "speed", speed === 0.25, "0.25"), btn("1×", "speed", speed === 1, "1"), btn("2×", "speed", speed === 2, "2"),
      btn("Menu", "reset")));
    return out;
  },
  brains: () => {
    const out = [el("h3", "", "Matchup"), matchupLinks(), el("h3", "", "Athletes")];
    game.match.sides.forEach((s, i) => {
      const c = s.card, corner = game.matchup.corners[i], l = league[corner.brain];
      const box = el("div", "athlete");
      box.append(el("b", s.name === "red" ? "red" : "blue", c.display),
        el("span", "", `${c.height_m} m · ${c.mass_kg} kg · STR ${c.strength} · SPD ${c.speed}`),
        el("span", "", `brain ${corner.brain}${l ? ` · Elo ${Math.round(l.elo)} · KO ${Math.round(100 * l.ko_rate)}%` : ""}`));
      out.push(box);
    });
    return out;
  },
  view: () => [
    el("h3", "", "Camera"),
    row(...["auto", "wide", "side", "close", "free"].map(m => btn(m === "auto" ? `Auto${director.mode === "auto" ? ` · ${director.shot}` : ""}` : m[0].toUpperCase() + m.slice(1), "cam", director.mode === m, m)),
      btn("Physics", "phys", art.physics)),
    el("h3", "", "Picture and sound"),
    row(...["auto", ...TIERS.slice().reverse()].map(t => btn(t === "auto" ? `Auto (${look.tier})` : t[0].toUpperCase() + t.slice(1), "gfx", look.auto ? t === "auto" : t === look.tier, t)),
      btn("Shake", "shake", look.shakeOn), btn(sound.muted ? "Sound off" : "Sound on", "sound", !sound.muted)),
    el("div", "note", `${LIVE ? `Live: ${LIVE}. API on the same host: /api/match, /api/events.` : "Local match: the same rules as training/match.py."} Build ${BUILD} · OBS 136 · 200 Hz.`),
  ],
};
function renderSheet() {
  const panel = $("panel");
  panel.textContent = "";
  if (sheetOpen) panel.append(...TABS[tab]());
  renderTabBadges();
}
function renderTabBadges() {
  $("bMatch").textContent = `R${Math.min(3, S.round)}`;
  $("bBrains").textContent = MATCHUP.toUpperCase();
  const b = $("bView");
  b.textContent = LIVE ? (S.live ? "LIVE" : "…") : director.mode === "auto" ? "AUTO" : director.mode.toUpperCase();
  b.classList.toggle("hot", !!(LIVE && S.live));
}
function toggleSheet(force, t = tab) {
  tab = t;
  sheetOpen = force === undefined ? !sheetOpen : force;
  $("sheet").hidden = !sheetOpen;
  for (const b of $("tabs").children) b.setAttribute("aria-selected", String(sheetOpen && b.dataset.tab === tab));
  renderSheet();
  measureBottom();
}
$("tabs").addEventListener("click", e => {
  const b = e.target.closest("button[data-tab]");
  if (b) toggleSheet(!(sheetOpen && b.dataset.tab === tab), b.dataset.tab);
});
$("gear").onclick = () => toggleSheet(!(sheetOpen && tab === "view"), "view");
$("panel").addEventListener("click", e => {
  const b = e.target.closest("button[data-act]");
  if (!b) return;
  const act = b.dataset.act, val = b.dataset.val;
  if (act === "pause") paused = !paused;
  else if (act === "speed") speed = +val;
  else if (act === "cam") director.mode = val;
  else if (act === "phys") { art.physics = !art.physics; showArt(); }
  else if (act === "reset") { showMenu(); return; }
  else if (act === "sound") { sound.unlock(); sound.setMuted(!sound.muted); }
  else if (act === "shake") { look.shakeOn = !look.shakeOn; localStorage.setItem("poslap.shake", look.shakeOn ? "on" : "off"); }
  else if (act === "gfx") { look.setTier(val); localStorage.setItem("poslap.gfx", val); }
  renderSheet();
});
addEventListener("keydown", e => {
  if (e.key === "Tab") { e.preventDefault(); toggleDetail(); }   // TAB: the detailed stats
  else if (e.key === "Escape") { if (sheetOpen) toggleSheet(false); else if (S.phase !== "menu") showMenu(); }
});

// ---- the menu: menu -> gameplay -> result -> reset ---------------------------------------------------------------
const ROSTER_SLOTS = [
  { id: "grandpa", display: "GRANDPA" },
  { id: "kim", display: "KIM" },
  { id: "matt", display: "MATT" },   // no striking brain yet (L2 has not graduated): shown, not picked
];
while (ROSTER_SLOTS.length < 16) ROSTER_SLOTS.push({ id: null, display: null, empty: true });
// a character plays here when some pairing on this page has him in a corner
const playable = slot => !!slot.id && Object.values(MATCHUPS).some(m => m.corners.some(c => c.char === slot.id));
const selFighters = [0, 1];   // set from the loaded pairing once it is known (initMenu)
let activeCornerPick = 0;

// ---- the ratings: Elo per character (K 32, from 1000), saved in this browser as each match ends ---------------------
// (Unreal keeps its own in Saved/PoSlap/elo.json: the same rule, a different device)
const ELO_KEY = "poslap.elo";
const ratings = (() => { try { return JSON.parse(localStorage.getItem(ELO_KEY)) || {}; } catch { return {}; } })();
const rating = id => ratings[id] ??= { elo: 1000, w: 0, l: 0 };
let lastResult = null;   // { text, elo } for the menu, after a match
function rate(winnerCorner) {
  const ids = game.matchup.corners.map(c => c.char), w = winnerCorner, l = 1 - w;
  if (ids[0] === ids[1]) return `${ids[0].toUpperCase()} vs ${ids[1].toUpperCase()}: mirror match, Elo unchanged`;
  const W = rating(ids[w]), L = rating(ids[l]);
  const delta = 32 * (1 - 1 / (1 + 10 ** ((L.elo - W.elo) / 400)));
  W.elo += delta; L.elo -= delta; W.w++; L.l++;
  localStorage.setItem(ELO_KEY, JSON.stringify(ratings));
  return `Elo ${ids[w].toUpperCase()} ${Math.round(W.elo)} (+${Math.round(delta)}) · ${ids[l].toUpperCase()} ${Math.round(L.elo)} (−${Math.round(delta)})`;
}

function renderMenu() {
  $("lblRed").textContent = ROSTER_SLOTS[selFighters[0]].display || "OPEN";
  $("lblBlue").textContent = ROSTER_SLOTS[selFighters[1]].display || "OPEN";
  $("btnPickRed").classList.toggle("active", activeCornerPick === 0);
  $("btnPickBlue").classList.toggle("active", activeCornerPick === 1);
  const same = ROSTER_SLOTS[selFighters[0]].id === game?.matchup.corners[0].char && ROSTER_SLOTS[selFighters[1]].id === game?.matchup.corners[1].char;
  $("practice").textContent = everStarted && same ? "REMATCH" : "START MATCH";
  const res = $("menuResult");
  res.hidden = !lastResult; res.textContent = "";
  if (lastResult) res.append(el("b", "", lastResult.text), el("span", "", lastResult.elo));
  $("slotPrompt").textContent = `Tap a slot for ${activeCornerPick === 0 ? "RED" : "BLUE"}`;
  const grid = $("roster16");
  grid.textContent = "";
  ROSTER_SLOTS.forEach((slot, i) => {
    const b = el("button", slot.empty ? "empty" : "");
    const isRed = selFighters[0] === i, isBlue = selFighters[1] === i;
    b.classList.toggle("selBoth", isRed && isBlue); b.classList.toggle("selRed", isRed && !isBlue); b.classList.toggle("selBlue", isBlue && !isRed);
    if (slot.empty) { b.append(el("small", "", String(i + 1))); b.disabled = true; }
    else if (!playable(slot)) { b.append(el("b", "", slot.display), el("small", "", "training")); b.classList.add("empty"); b.disabled = true; }
    else {
      const tag = isRed && isBlue ? "P1·P2" : isRed ? "P1" : isBlue ? "P2" : "";
      const r = ratings[slot.id];
      b.append(el("b", "", slot.display), el("small", "", `${tag ? tag + " · " : ""}${r ? Math.round(r.elo) : 1000}`));
      b.title = r ? `Elo ${Math.round(r.elo)} · ${r.w}-${r.l}` : "Elo 1000";
      b.onclick = () => { selFighters[activeCornerPick] = i; activeCornerPick = 1 - activeCornerPick; renderMenu(); };
    }
    grid.appendChild(b);
  });
}
$("btnPickRed").onclick = () => { activeCornerPick = 0; renderMenu(); };
$("btnPickBlue").onclick = () => { activeCornerPick = 1; renderMenu(); };

function showMenu() {
  if (S.phase === "result") go("reset");
  if (!go("menu")) return;
  started = false;
  hideResult();
  if (sheetOpen) toggleSheet(false);   // the menu, not the stats behind it
  $("menu").hidden = false;
  renderMenu();
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
  if (!go("gameplay")) return;
  if (everStarted) {
    while (busy) await new Promise(r => setTimeout(r, 5));   // a physics batch in flight finishes first
    S.danger = { red: 0, blue: 0, source: "local" };
    game.match.newMatch();
  }
  hideResult();
  $("menu").hidden = true;
  started = everStarted = true;
}
function rematch() { if (go("reset")) startPractice(); }
$("practice").onclick = startPractice;
$("watchlive").onclick = () => { if (go("gameplay")) $("menu").hidden = true; };
function initMenu() {
  game.matchup.corners.forEach((c, i) => {   // the picks open on the pairing that is loaded
    const s = ROSTER_SLOTS.findIndex(x => x.id === c.char);
    if (s >= 0) selFighters[i] = s;
  });
  renderMenu();
  $("menuFoot").textContent = `build ${BUILD} · ${MATCHUPS[MATCHUP].name}`;
}

// ---- the layout: the dock's height is a CSS variable, so the HUD never sits under it -------------------------
function measureBottom() {
  document.documentElement.style.setProperty("--bottomh", `${Math.round($("bottom").getBoundingClientRect().height)}px`);
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
// a drag or a wheel on the view takes the camera (manual override); a tap does not
let dragFrom = null;
const takeCamera = () => { if (director.mode === "free") return; director.mode = "free"; if (sheetOpen) renderSheet(); renderTabBadges(); };
canvas.addEventListener("pointerdown", e => { dragFrom = [e.clientX, e.clientY]; });
canvas.addEventListener("pointermove", e => { if (dragFrom && e.buttons && Math.hypot(e.clientX - dragFrom[0], e.clientY - dragFrom[1]) > 6) takeCamera(); });
addEventListener("pointerup", () => { dragFrom = null; });
canvas.addEventListener("wheel", takeCamera, { passive: true });
// from -Y: red (-X) on the screen's left, under the HUD's red-first rows and the RED pick (Unreal's director does the same)
const WIDE = { pos: new THREE.Vector3(0, -4.6, 1.5), at: new THREE.Vector3(0, 0, 1.05) };
camera.position.copy(WIDE.pos); controls.target.copy(WIDE.at); controls.update();

scene.add(new THREE.HemisphereLight(0x8890a8, 0x201814, 1.1));
const key = new THREE.SpotLight(0xfff1dc, 60, 12, 0.55, 0.5, 1.2);
key.position.set(0.3, -1.2, 5.5); key.target.position.set(0, 0, 1); key.castShadow = true; key.shadow.mapSize.set(2048, 2048);
scene.add(key, key.target);
// the broadcast lights: a follow spot on the striker (no shadow: the key casts them) and a rim behind the defender
const follow = new THREE.SpotLight(0xfff4e6, 0, 10, 0.2, 0.65, 1.4);
follow.position.set(0, -2.4, 4.4); follow.target.position.set(0, 0, 1.2);
const rimDef = new THREE.PointLight(0xffffff, 0, 3.5, 1.6);
scene.add(follow, follow.target, rimDef);
const look = new Look(renderer, scene, camera, key);   // the post chain, the beam, the spray (fx.js)
look.focusAt = controls.target;   // the depth of field holds the director's subject sharp
look.setTier(QS.get("gfx") || localStorage.getItem("poslap.gfx") || "auto");
look.shakeOn = localStorage.getItem("poslap.shake") !== "off";
// the fps ladder and the report's shots (Web/fps_ladder.mjs) switch tiers and effects in place: display only
window.__poslapLook = { look, clip: true, set(tier, fx = {}) { look.override = fx; look.setTier(tier); }, cam(m) { director.mode = m; }, pause(p) { paused = p; } };
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
// lets it sink 1-7 cm into the head sphere, and the sphere does not cover the drawn jaw. With both meshes loaded the
// hand is pushed out of the drawn face (art.js clipPush: the mesh's own vertices and normals); before that, out of the
// head sphere along the centre line (drawing only)
const pushers = [], neckBody = [];
function buildPushers(m, sides) {
  sides.forEach((s, i) => {
    try { const b = m.body(`${s.name}_neck`); neckBody[i] = b.id; b.delete(); } catch { neckBody[i] = -1; }
    const o = sides[1 - i], r = m.geom_size[3 * o.head];
    const thick = g => Math.min(m.geom_size[3 * g], m.geom_size[3 * g + 1], m.geom_size[3 * g + 2]);
    const hands = { hand_l: s.hands[0], hand_r: s.hands[1] };
    pushers[i] = (name, p, d) => {
      const g = hands[name];
      if (g === undefined) return;
      const A = art.athletes[i]?.clip, B = art.athletes[1 - i]?.clip;
      if (A && B && window.__poslapLook?.clip !== false) {
        clipPush(A[name], B.head, d, m.geom_bodyid[g], o.headBody, p);
        if (neckBody[1 - i] >= 0) clipPush(A[name], B.neck, d, m.geom_bodyid[g], neckBody[1 - i], p);   // the jaw skinned to the neck
        return;
      }
      const P = d.geom_xpos, dx = P[3 * g] - P[3 * o.head], dy = P[3 * g + 1] - P[3 * o.head + 1], dz = P[3 * g + 2] - P[3 * o.head + 2];
      const dist = Math.hypot(dx, dy, dz), need = r + thick(g) + 0.004 - dist;
      if (need > 0 && dist > 1e-6) p.set(p.x + dx / dist * need, p.y + dy / dist * need, p.z + dz / dist * need);
    };
  });
}
// the skin's wear (Unreal's ASlapHUD): the cheek rings and is pushed in where the slap landed (and the CheekDent morph
// targets, if the mesh has them), flushes red round that spot (following the head) with the sting of the last slap,
// reddens wider as the neck damage builds, and greys with the lost stamina
const wear = SIDES.map(() => ({ local: null, at: -99, speed: 0, dir: new THREE.Vector3() }));
const headFrame = (d, b) => [new THREE.Vector3(d.xpos[3 * b], d.xpos[3 * b + 1], d.xpos[3 * b + 2]),
  new THREE.Matrix3().set(...Array.from(d.xmat.subarray(9 * b, 9 * b + 9)))];   // MuJoCo's xmat is row-major, as set() takes it
function struck(striker, pos, speed) {
  const sides = game.match.sides, j = sides.findIndex(s => s.name !== striker), si = 1 - j;
  const [c, R] = headFrame(game.d, sides[j].headBody), p = new THREE.Vector3(...pos);
  wear[j].local = p.clone().sub(c).applyMatrix3(R.transpose());   // (head frame: +y is the athlete's own left)
  wear[j].dir = c.clone().sub(p).normalize();   // the cheek is pushed toward the head's centre
  wear[j].at = performance.now() / 1000; wear[j].speed = speed;
  const momentumSize = (sides[si].card.strike_mass || 3) * speed / CLEAN_MOMENTUM;   // the burst: strike mass x palm speed
  look.hit(pos, wear[j].dir, speed, Math.max(0.3, Math.min(2, momentumSize)));
}
function skinTick(d) {
  const now = performance.now() / 1000;
  art.athletes.forEach((a, i) => {
    const s = game.match.sides[i], w = wear[i], f = S.fatigue[s.name];
    const P = w.local && (([c, R]) => w.local.clone().applyMatrix3(R).add(c))(headFrame(d, s.headBody));
    const age = now - w.at, damage = clamp01((1 - f.neck) / (1 - MIN_NECK));
    const sting = 0.5 * Math.min(1.5, w.speed / CLEAN) * Math.exp(-age / 2);
    for (const u of a.skins) {
      if (P) { u.flushPoint.value.copy(P); u.ripplePoint.value.copy(P); u.rippleDir.value.copy(w.dir); }
      u.rippleTime.value = age; u.rippleAmp.value = 0.2 * w.speed;
      u.flush.value = clamp01(0.55 * damage + sting);
      u.bruise.value = damage; u.flushRadius.value = 9 + 6 * damage;
      u.tired.value = clamp01((1 - f.stamina) / (1 - MIN_STAMINA));
    }
    if (a.hasCheek) {   // the dent: in over 30 ms, out over ~0.2 s, on the cheek that was hit
      const k = !w.local ? 0 : Math.min(1, w.speed / CLEAN) * (age < 0.03 ? age / 0.03 : Math.exp(-(age - 0.03) / 0.18));
      const left = w.local && w.local.y >= 0;
      a.cheek(left ? k : 0, left ? 0 : k);
    }
  });
}
// footsteps and skids from MuJoCo's own sole contacts: a sole that touches the floor after 0.1 s off it is a step; a
// planted sole sliding faster than 0.6 m/s squeaks or scrapes (at most every 0.3 s per foot)
const soles = new Map();   // foot geom -> { down, off, x, y, quiet }
function feetTick(d, dt) {
  if (!dt) return;
  const M = game.match, touching = new Set(), cs = d.contact, n = d.ncon;
  for (let i = 0; i < n; i++) {
    const c = cs.get(i), g0 = c.geom1, g1 = c.geom2;
    c.delete();
    if (g0 === M.floor && soles.has(g1)) touching.add(g1); else if (g1 === M.floor && soles.has(g0)) touching.add(g0);
  }
  cs.delete();
  for (const [g, f] of soles) {
    const x = d.geom_xpos[3 * g], y = d.geom_xpos[3 * g + 1], v = Math.hypot(x - f.x, y - f.y) / dt;
    f.quiet -= dt;
    if (touching.has(g)) {
      if (!f.down && f.off > 0.1) sound.step(x, Math.min(0.6, 0.2 + 0.25 * f.off), Math.hypot(x, y));
      else if (f.down && v > 0.6 && v < 5 && f.quiet <= 0) { f.quiet = 0.3; sound.skid(x, v); }
      f.down = true; f.off = 0;
    } else { f.down = false; f.off += dt; }
    f.x = x; f.y = y;
  }
}
function initSoles(d) { for (const s of game.match.sides) for (const g of s.feet) soles.set(g, { down: true, off: 0, x: d.geom_xpos[3 * g], y: d.geom_xpos[3 * g + 1], quiet: 0 }); }
function dust(athlete, part) {   // the knockdown: dust where the body met the floor (the part the referee called)
  try {
    const a = game.m.geom(`${athlete}_${part}`), g = a.id; a.delete();
    if (!(g >= 0)) return;
    const P = game.d.geom_xpos;
    look.knockdown([P[3 * g], P[3 * g + 1], Math.min(P[3 * g + 2], 1.0)], 1);
  } catch { /* an unknown part name (a live feed from another build): no dust */ }
}
const handXYZ = [];
function draw(d) {
  art.athletes.forEach((a, i) => a.update(d, pushers[i]));
  handXYZ.length = 0;   // the palms' paths for the motion blur (fx.js, display only)
  for (const s of game.match.sides) for (const g of s.hands) handXYZ.push(d.geom_xpos[3 * g], d.geom_xpos[3 * g + 1], d.geom_xpos[3 * g + 2]);
  look.track(handXYZ);
  if (art.athletes.length) skinTick(d);
  const P = d.geom_xpos, X = d.geom_xmat;
  for (const [mesh, g] of shapes) {
    const b = 9 * g;
    mesh.matrix.set(X[b], X[b + 1], X[b + 2], P[3 * g], X[b + 3], X[b + 4], X[b + 5], P[3 * g + 1], X[b + 6], X[b + 7], X[b + 8], P[3 * g + 2], 0, 0, 0, 1);
  }
}
// ---- the director (broadcast.js) and the broadcast lights, every frame ---------------------------------------------
const camGoal = { pos: WIDE.pos.clone(), at: WIDE.at.clone() };
const headOf = (d, x) => new THREE.Vector3(d.geom_xpos[3 * x.head], d.geom_xpos[3 * x.head + 1], d.geom_xpos[3 * x.head + 2]);
function directTick(d, dt) {
  const M = game.match, now = performance.now() / 1000;
  const si = M.sides.findIndex(x => x.name === S.striker), Sx = M.sides[si], Dx = M.sides[1 - si];
  const shot = director.pick(now, S.strikeAt, S.striker ? Math.max(read.value, S.tension && S.strikeAt < 0 ? 1 : 0) : 0, now - (S.downAt || -99) < 2.5);
  if (director.mode !== "free") {
    if (shot === "wide" || !Sx) { camGoal.pos.copy(WIDE.pos); camGoal.at.copy(WIDE.at); }
    else {
      const hs = headOf(d, Sx), hd = headOf(d, Dx), sx = Math.sign(hs.x - hd.x) || -1;   // the striker's side of the defender
      if (shot === "side") {   // over the striker's shoulder onto the contact
        camGoal.at.copy(hs).lerp(hd, 0.6);
        camGoal.pos.set(camGoal.at.x + sx * 1.5, -1.9, camGoal.at.z + 0.3);
      } else {                 // close: the defender's face as the palm comes
        camGoal.at.copy(hd);
        camGoal.pos.set(hd.x + sx * 0.55, hd.y - 1.05, hd.z + 0.04);
      }
    }
    const k = director.cut ? 1 : 1 - Math.exp(-dt * 3);
    camera.position.lerp(camGoal.pos, k); controls.target.lerp(camGoal.at, k);
  }
  // the follow spot finds the striker; the rim lights the defender from behind in his corner's colour
  if (Sx) {
    const hs = headOf(d, Sx), hd = headOf(d, Dx);
    follow.target.position.lerp(hs.clone().setZ(hs.z - 0.35), Math.min(1, dt * 6));
    follow.position.lerp(new THREE.Vector3(hs.x * 0.6, -2.4, 4.4), Math.min(1, dt * 3));
    follow.intensity += (35 - follow.intensity) * Math.min(1, dt * 4);
    rimDef.position.set(hd.x + Math.sign(hd.x) * 0.35, 0.55, hd.z + 0.15);
    rimDef.color.setHex(COLORS[Dx.name]);
    rimDef.intensity += (4 - rimDef.intensity) * Math.min(1, dt * 4);
    sound.setDistance(camera.position.distanceTo(hd));
  } else {
    follow.intensity *= Math.exp(-dt * 4); rimDef.intensity *= Math.exp(-dt * 4);
    sound.setDistance(camera.position.distanceTo(WIDE.at));
  }
}
function resize() { const w = innerWidth, h = innerHeight; look.resize(w, h); camera.aspect = w / h; camera.updateProjectionMatrix(); measureBottom(); fitNames(); }
addEventListener("resize", resize); resize();

// ---- the loop: physics in real time, drawing every frame ---------------------------------------------------
const get = async (p, kind) => { const r = await fetch("../" + p); if (!r.ok) throw new Error(`${p}: ${r.status}`); return kind === "text" ? r.text() : kind === "json" ? r.json() : new Uint8Array(await r.arrayBuffer()); };
const MATCHUP = MATCHUPS[QS.get("m")] ? QS.get("m") : "gg";
let league = {};   // training/models/league.json (training/scouting.py): Elo and tendencies per brain, if it has run
try { league = (await get("training/models/league.json", "json")).brains || {}; } catch { }
let game, speed = 1, paused = false, busy = false, debt = 0, prevT = performance.now();
let started = false;      // the menu gates the match: nothing runs until the spectator picks one
let fpsNow = 0, frames = 0, fpsT = performance.now();
const sampleClock = () => LIVE ? performance.now() / 1000 : game.match.k * game.match.dt;

try {
  $("loadmsg").textContent = "Loading MuJoCo…";
  const mj = await loadMujoco();
  $("loadmsg").textContent = "Loading the arena and both brains…";
  game = await loadMatch(mj, ort, get, onEvent, MATCHUP);
  buildShapes(game.m, game.match.sides); footBoxes(game.d, game.match.sides); buildPushers(game.m, game.match.sides); initSoles(game.d);
  game.match.autoNext = false;   // the round trip: after the result's hold, back to the menu (sim.js phase "over")
  loadArt(mj).catch(err => console.error("art:", err));   // the match starts on the shapes; the art swaps in when loaded
  for (const s of game.match.sides) NAMES[s.name] = s.card.display;
  sound.setVoices(Object.fromEntries(game.match.sides.map(s => [s.name, s.card.name])));   // grunts and breaths per athlete
  buildDangerMeters(); buildPlates(); renderStreak(); initMenu(); renderTabBadges(); renderCards(); measureBottom();
  if (LIVE) { $("watchlive").hidden = false; $("practice").hidden = true; connectLive(); }
  else if (QS.has("go")) startPractice();   // START picked this pairing on the last page
  if (game.match.phase === "turn") { S.striker = game.match.sides[game.match.s].name; cornerLine(S.striker, " strikes"); }   // ?nowalkin: the first turn began before the names were known
  $("loading").remove();
  requestAnimationFrame(loop);
} catch (err) {
  $("loadmsg").textContent = `Could not start: ${err.message}`; console.error(err);
}

async function advance(n) {
  busy = true;
  const M = game.match;
  try {
    for (let i = 0; i < n; i++) {
      await M.step(); S.steps++;
      if (M.phase === "turn") sampleBodies(M.k * M.dt, M.sides[M.s].name, M.speedNow || 0, M.k % 4 === 0);
    }
  } finally { busy = false; }
}
function loop(now) {
  const dt = Math.min(0.1, (now - prevT) / 1000); prevT = now;
  if (!paused && !LIVE && started) debt += dt * speed;
  const M = game.match, h = M.dt;
  if (!busy && debt >= h) { const n = Math.min(Math.floor(debt / h), 60); debt -= n * h; if (debt > 0.25) debt = 0; advance(n); }
  if (LIVE) {
    const age = (performance.now() - live.at) / 1000;
    $("clock").textContent = age > 3 ? "LIVE · waiting for Unreal" : `LIVE${live.walkIn ? " · walk-in" : ""}${live.slow < 1 ? " · slow motion" : ""}`;
    if (S.phase === "result" && now / 1000 - resultShownAt > RESULT_HOLD_S) hideResult();
  } else if (M.phase === "walkin") {
    $("pipsTrack").textContent = "RING WALK-IN";
    $("turn").textContent = `To the podium ${Math.round((M.walkInProg || 0) * 100)}%`;
    $("clock").textContent = `walk-in ${Math.max(0, (M.walkInLeft || 0) * h).toFixed(1)} s`;
  } else if (M.phase === "turn") {
    $("clock").textContent = `${M.touchK === null ? "clock" : "recovery"} ${(M.touchK === null ? Math.max(0, 30 - M.k * h) : Math.max(0, 5 - (M.k - M.touchK) * h)).toFixed(1)} s`;
  } else if (M.phase === "hold") {
    $("clock").textContent = `next turn in ${Math.max(0, M.holdLeft * h).toFixed(1)} s`;
  } else if (M.phase === "after") {
    $("clock").textContent = "final";
  } else if (M.phase === "over" && started && S.phase === "result" && now / 1000 - resultShownAt > RESULT_HOLD_S) {
    showMenu();   // the result has had its moment: reset, the menu, with the same two picked (REMATCH) or new ones
  }
  if (!LIVE) for (const x of M.sides) Object.assign(S.fatigue[x.name], { stamina: x.stamina, neck: x.neckStrength });
  read.update(paused ? 0 : dt);
  frames++;
  if (now - fpsT > 500) {
    fpsNow = Math.round(frames * 1000 / (now - fpsT)); frames = 0; fpsT = now;
    $("fps").textContent = `${fpsNow} fps`;
    $("steps").textContent = LIVE ? "live feed" : S.steps > 9999 ? `${(S.steps / 1000).toFixed(1)}k steps` : `${S.steps} steps`;
    S.simTime = S.steps * h;
    $("tel").textContent = LIVE ? `live · ${S.strikes} strikes` : `step ${S.steps} · ${S.simTime.toFixed(1)} s · ${look.tier}`;
    if (!LIVE) {   // the local capture point: the danger the browser's own match is in
      const dg = localDanger();
      S.danger.red = dg[0]; S.danger.blue = dg[1]; S.danger.source = "local";
    }
    updateDangerMeters(S.danger);
    $("swingLine").textContent = `swings: feint ${S.feints} · real ${S.reals}`;
    $("readLine").textContent = `AI read ${Math.round(100 * read.value)}% · ${read.kind || "quiet"}`;   // (on the 0.12 s late trace the brain sees)
    $("camLine").textContent = `camera ${director.mode}${director.mode === "auto" ? ` · ${director.shot}` : ""}`;
    renderTabBadges();
  }
  renderPlates();
  const sparkOn = now / 1000 < sparkOnUntil;
  $("sparkbox").classList.toggle("on", sparkOn);
  $("sparkbox").classList.toggle("has", snap.points.length > 0);
  if (sparkOn || (detail && snap.recording)) drawSpark();
  draw(game.d); directTick(game.d, dt); controls.update();
  feetTick(game.d, paused ? 0 : dt);
  sound.tick(Math.max(S.danger.red || 0, S.danger.blue || 0), S.fatigue.red.stamina, S.fatigue.blue.stamina);
  look.render(dt * (paused ? 0 : 1), fpsNow);
  requestAnimationFrame(loop);
}

// ---- second screen ----------------------------------------------------------------------------------------------
const live = { at: 0, slow: 1, walkIn: false, handPrev: null };
function connectLive() {
  const azure = LIVE.includes("webpubsub.azure.com");
  const ws = new WebSocket(LIVE, azure ? "json.webpubsub.azure.v1" : undefined);
  ws.onopen = () => { S.live = true; renderTabBadges(); if (azure) ws.send(JSON.stringify({ type: "joinGroup", group: "live" })); };
  ws.onclose = () => { S.live = false; renderTabBadges(); setTimeout(connectLive, 3000); };   // Unreal or the relay restarted: keep trying
  ws.onmessage = ev => {
    let msg = JSON.parse(ev.data);
    if (azure) { if (msg.type !== "message") return; msg = typeof msg.data === "string" ? JSON.parse(msg.data) : msg.data; }
    if (msg.t === "ev") { onEvent(msg.e); return; }
    if (msg.t !== "pose") return;
    // another pairing in Unreal: reload on the matchup whose two characters match, if this page has it
    const corners = game.matchup.corners.map(c => c.char);
    if (corners[0] !== msg.c[0] || corners[1] !== msg.c[1]) {
      const k = Object.keys(MATCHUPS).find(m => MATCHUPS[m].corners[0].char === msg.c[0] && MATCHUPS[m].corners[1].char === msg.c[1]);
      if (k) { location.search = `?m=${k}&live=${encodeURIComponent(LIVE)}`; return; }
      $("clock").textContent = `LIVE · ${msg.c.join(" vs ")} is not on this page`; return;
    }
    const q = game.d.qpos, n = 7 + game.match.sides[0].qa.length;
    game.match.sides.forEach((s, i) => {   // [root (7), joints in fingerprint order] per athlete, red then blue
      for (let k = 0; k < 7; k++) q[s.rq + k] = msg.q[n * i + k];
      s.qa.forEach((a, j) => { q[a] = msg.q[n * i + 7 + j]; });
    });
    game.match.mj.mj_forward(game.m, game.d);
    const t = performance.now() / 1000;
    Object.assign(live, { at: performance.now(), slow: msg.slow, walkIn: msg.w });
    if (msg.w) $("turn").textContent = "To the podium";
    else if (isSide(msg.s)) {
      cornerLine(msg.s, " strikes"); S.striker = msg.s;
      const x = game.match.sides.find(s => s.name === msg.s), G = game.d.geom_xpos;   // the palm's speed between poses
      const hand = [G[3 * x.hand], G[3 * x.hand + 1], G[3 * x.hand + 2]], p = live.handPrev;
      const v = p && t > p[0] ? Math.hypot(hand[0] - p[1][0], hand[1] - p[1][1], hand[2] - p[1][2]) / (t - p[0]) : 0;
      live.handPrev = [t, hand];
      sampleBodies(t, msg.s, v);
    }
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
