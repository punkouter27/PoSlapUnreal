// The broadcast's reading of the match: what a commentator would call, computed from the bodies MuJoCo already
// moved. DISPLAY ONLY: nothing here is fed back into sim.js, the brains or the referee (Blueprint: MuJoCo moves the
// athletes, the page only draws them). Shared by the page (app.js) and the Node check (check.mjs).

// ---- F5 the swing tag: FEINT or REAL (Source/PoSlapUnreal/SlapHUD.cpp, the F10 feint classifier) ------------------
// The same rule and names as training/constants.py FEINT_* (match.py emits "feint" events from it). A swing that
// peaks above FEINT_MIN_PEAK and, FEINT_AFTER_S after the peak, has pulled back below FEINT_PULLBACK of it
// without contact is a feint; a swing that lands is real. A new swing (FEINT_REARM of the old peak, FEINT_REARM_S
// after it) re-arms the classifier.
// (the numbers: shared.gen.js, written by training/gen_constants.py - the same ones sim.js and Unreal read)
import { FEINT_MIN_PEAK, FEINT_PULLBACK, FEINT_AFTER_S, FEINT_REARM, FEINT_REARM_S, REACT, CONTROL_HZ } from "./shared.gen.js";
export { FEINT_MIN_PEAK, FEINT_PULLBACK, FEINT_AFTER_S, FEINT_REARM, FEINT_REARM_S };

export class SwingTagger {
  constructor() { this.reset(); }
  reset() { this.peak = 0; this.peakAt = 0; this.swing = false; this.tagged = false; this.feints = 0; this.done = false; }
  // one sample of the striker's palm speed (m/s) at time t (s); returns "FEINT" when this sample tags one
  sample(t, speed) {
    if (this.done) return null;
    if (speed > this.peak) { this.peak = speed; this.peakAt = t; this.swing = true; }
    let tag = null;
    if (this.swing && !this.tagged && t - this.peakAt > FEINT_AFTER_S && speed < FEINT_PULLBACK * this.peak && this.peak > FEINT_MIN_PEAK) {
      this.tagged = true; this.feints++; tag = "FEINT";
    }
    if (this.tagged && speed > this.peak * FEINT_REARM && t - this.peakAt > FEINT_REARM_S) { this.swing = false; this.tagged = false; this.peak = 0; }
    return tag;
  }
  landed() { this.done = true; return "REAL"; }   // contact: the swing was real; the turn's tagging stops here
}

// ---- F3 the AI read: what the defender's brain can see of the striker --------------------------------------------
// The defender's observation shows the striker's hand REACT_S late (sim.js REACT = 6 decisions at 50 Hz), so the read
// is taken on the delayed trace. WIND-UP: the palm drawing back away from the face, or the shoulder loading fast;
// INCOMING: the palm closing on the face (Unreal's tell: within TELL_DIST, faster than 4 x the distance per second).
export const REACT_S = REACT / CONTROL_HZ;
export const WINDUP_AWAY = 0.4;       // m/s: the palm moving away from the defender's face (drawing back)
export const WINDUP_SHOULDER = 2.0;   // rad/s: the striking shoulder's swing speed that reads as loading
export const TELL_DIST = 0.6, TELL_SPEED = 3.0;   // m, m/s (SlapHUD.cpp F3)

export class Read {
  constructor() { this.reset(); }
  reset() { this.trace = []; this.value = 0; this.kind = ""; }
  // t (s), hand and head: [x, y, z] (the striker's palm, the defender's face), shoulder: the swing joint's rad/s
  sample(t, hand, head, shoulder) {
    this.trace.push([t, hand, head, shoulder]);
    while (this.trace.length > 2 && this.trace[1][0] <= t - REACT_S) this.trace.shift();
  }
  // the read as the defender has it now (0..1) and what it is; eased toward its target at `rate` per second
  update(dt, rate = 10) {
    let want = 0, kind = "";
    const T = this.trace;
    if (T.length >= 2) {
      const [t0, h0, f0, sh] = T[0], [t1, h1] = T[1];
      const step = Math.max(t1 - t0, 1e-4);
      const d0 = Math.hypot(h0[0] - f0[0], h0[1] - f0[1], h0[2] - f0[2]);
      const d1 = Math.hypot(h1[0] - f0[0], h1[1] - f0[1], h1[2] - f0[2]);
      const radial = (d1 - d0) / step;                       // + away from the face, - closing
      const speed = Math.hypot(h1[0] - h0[0], h1[1] - h0[1], h1[2] - h0[2]) / step;
      if (d0 < TELL_DIST && speed > TELL_SPEED && -radial > 4 * Math.max(d0, 0.05)) {
        want = Math.min(1, (TELL_DIST - d0) / 0.5) * Math.min(1, speed / 8); kind = "INCOMING";
      } else {
        const away = Math.max(0, (radial - WINDUP_AWAY) / 1.2), load = Math.max(0, (Math.abs(sh || 0) - WINDUP_SHOULDER) / 3);
        want = Math.min(1, Math.max(away, load)); kind = want > 0 ? "WIND-UP" : "";
      }
    }
    this.value += (want - this.value) * Math.min(1, dt * rate);
    if (want > 0.05) this.kind = kind; else if (this.value < 0.05) this.kind = "";
    return this.value;
  }
}

// ---- F8 the head-snap trace: the defender's head speed for SNAP_S after the touch -----------------------------------
export const SNAP_S = 2.0;
export class Snap {
  constructor() { this.points = []; this.t0 = null; this.peak = 0; this.prev = null; }
  start(t) { this.points = []; this.t0 = t; this.peak = 0; }
  // the defender's head position each sample; records while inside the window
  sample(t, head) {
    const p = this.prev; this.prev = [t, head];
    if (this.t0 === null || !p || t <= p[0] || t - this.t0 > SNAP_S) return;
    const v = Math.hypot(head[0] - p[1][0], head[1] - p[1][1], head[2] - p[1][2]) / (t - p[0]);
    if (!Number.isFinite(v) || v > 60) return;               // a reset teleport, not a snap
    this.points.push([t - this.t0, v]); this.peak = Math.max(this.peak, v);
  }
  get recording() { return this.t0 !== null && this.points.length > 0 && this.points[this.points.length - 1][0] < SNAP_S - 0.02; }
}

// ---- F10 the director: wide / side / close, cut on the wind-up and on the impact ------------------------------------
// Escalations cut at once; the way back waits DIRECTOR_HOLD_S (SlapHUD.cpp Direct). Manual picks override it.
export const SHOTS = ["wide", "side", "close"];
export const DIRECTOR_HOLD_S = 1.0, IMPACT_SHOT_S = 1.6, READ_CUT = 0.35;
const RANK = { wide: 0, close: 1, side: 2 };
export class Director {
  constructor() { this.mode = "auto"; this.shot = "wide"; this.since = 0; this.cut = true; }
  // now (s), strikeAt (s of the last touch, or -1), read (0..1), down (a knockdown is on)
  pick(now, strikeAt, read, down) {
    if (this.mode !== "auto") { const s = this.mode === "free" ? this.shot : this.mode; this.cut = s !== this.shot; this.shot = s; return s; }
    let want = "wide";
    if (down || (strikeAt >= 0 && now - strikeAt < IMPACT_SHOT_S)) want = "side";
    else if (read > READ_CUT) want = "close";
    this.cut = false;
    if (want !== this.shot && (RANK[want] > RANK[this.shot] || now - this.since > DIRECTOR_HOLD_S)) {
      this.cut = RANK[want] > RANK[this.shot];   // an escalation cuts; a return eases
      this.shot = want; this.since = now;
    }
    return this.shot;
  }
}

// ---- the flinch: the defender's head moving away from the incoming palm before it lands ---------------------------
// A flinch is the head's velocity along the palm-to-head line (+ away from the palm) above FLINCH_SPEED while a swing
// is on (the palm faster than FEINT_MIN_PEAK), before the touch. Measured 2026-11-04 on the shipped brains (node, one
// match each, brain-step samples after the turn's first second): with no swing on the head drifts at most 3 cm/s
// (Grandpa) to 16 cm/s (Kim) away from the palm; during Kim-vs-Grandpa swings the defender's head pulls away at up to
// 0.55 m/s (p90 0.39). 0.15 m/s over FLINCH_MIN_S separates the two. One flinch per swing: a FEINT re-arms it.
export const FLINCH_SPEED = 0.15, FLINCH_MIN_S = 0.03, FLINCH_DT = 0.018;
export class Flinch {
  constructor() { this.reset(); }
  reset() { this.prev = null; this.over = 0; this.peak = 0; this.armed = true; this.n = 0; this.done = false; this.at = -1; }
  rearm() { this.armed = true; this.over = 0; this.peak = 0; }
  landed() { this.done = true; }
  // t (s), hand / head [x, y, z], palm: the striker's palm speed (m/s). Returns the away speed when this sample tags one.
  sample(t, hand, head, palm) {
    if (this.done) return null;
    const p = this.prev;
    if (p && t - p[0] < FLINCH_DT) return null;   // a brain-step cadence at 200 Hz, a pose cadence live
    this.prev = [t, head.slice()];
    if (!p || t <= p[0]) return null;
    const u = [head[0] - hand[0], head[1] - hand[1], head[2] - hand[2]], n = Math.hypot(u[0], u[1], u[2]) || 1;
    const away = ((head[0] - p[1][0]) * u[0] + (head[1] - p[1][1]) * u[1] + (head[2] - p[1][2]) * u[2]) / n / (t - p[0]);
    if (!this.armed || palm < FEINT_MIN_PEAK || !(away > FLINCH_SPEED) || away > 20) { this.over = 0; return null; }
    this.over += t - p[0]; this.peak = Math.max(this.peak, away);
    if (this.over < FLINCH_MIN_S) return null;
    this.armed = false; this.n++; this.at = t;
    return this.peak;
  }
}

// ---- derived spectator numbers (the relay's /api/match uses the same rules: Web/live.py) ----------------------------
// health: 1.0 at the start of a match, -0.5 per knockdown, -0.25 per faint, 0 on a knockout (a spectator estimate)
export const health = (kd, faints, ko) => ko ? 0 : Math.max(0, 1 - 0.5 * kd - 0.25 * faints);
// the KO danger: the balance danger now (the capture point outside the soles, or the last head snap live) combined
// with the neck damage the defender has absorbed (sim.js: neck strength falls by neck_damage_per_snap x head snap per
// strike, to MIN_NECK). A worn neck snaps further on the next slap: NECK_WEIGHT of the danger is already banked.
export const NECK_WEIGHT = 0.6;
export const neckDamage = (neck, minNeck) => Math.max(0, Math.min(1, (1 - neck) / (1 - minNeck)));
export const koDanger = (balance, damage) => 1 - (1 - Math.max(0, Math.min(1, balance))) * (1 - NECK_WEIGHT * damage);
