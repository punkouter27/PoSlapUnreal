// The broadcast's reading of the match: what a commentator would call, computed from the bodies MuJoCo already
// moved. DISPLAY ONLY: nothing here is fed back into sim.js, the brains or the referee (Blueprint: MuJoCo moves the
// athletes, the page only draws them). Shared by the page (app.js) and the Node check (check.mjs).

// ---- F5 the swing tag: FEINT or REAL (Source/PoSlapUnreal/SlapHUD.cpp, the F10 feint classifier) ------------------
// The same rule and names as training/constants.py FEINT_* (match.py emits "feint" events from it). A swing that
// peaks above FEINT_MIN_PEAK and, FEINT_AFTER_S after the peak, has pulled back below FEINT_PULLBACK of it
// without contact is a feint; a swing that lands is real. A new swing (FEINT_REARM of the old peak, FEINT_REARM_S
// after it) re-arms the classifier.
export const FEINT_MIN_PEAK = 2.5;        // m/s: the palm's peak speed that counts as a swing
export const FEINT_PULLBACK = 0.55;   // fraction of the peak the palm has to fall below
export const FEINT_AFTER_S = 0.12;     // s after the peak before the pull-back is judged
export const FEINT_REARM = 0.95, FEINT_REARM_S = 0.2;

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
export const REACT_S = 0.12;
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
