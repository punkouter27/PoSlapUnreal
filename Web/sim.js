// PoSlap match in the browser: MuJoCo (WASM, the version we train with) moves the athletes, the trained ONNX brains
// decide, the referee and judges are training/match.py's (Unreal's USlapController mirrors the same rules).
// Each corner reads its own fingerprint and character card, as Unreal does for Grandpa vs Kim.

export const OBS = 136;                    // DOCS/BRAIN_IO.md
const LAGS = [5, 10, 15];                  // memory: inputs 102-107 as they were 0.1, 0.2, 0.3 s ago
const MIN_STAMINA = 0.7, MIN_NECK = 0.5;   // fatigue floors
export const ROUNDS = 3, STRIKE_CLOCK = 30, RECOVERY = 5, HOLD = 2, WALKIN = 3;
const BOX = [0.20, 0.25], KICK_STEPS = 2;
const WEIGHTS = [[2, 1, 1, 1], [1, 2, 1, 1], [1, 1, 2, 2]];      // judges: speed, head, stagger, defence

const quatMat = (w, x, y, z) => [
  1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y),
  2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x),
  2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)];
const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1], a[2] - b[2]);
const v3 = (arr, i) => [arr[3 * i], arr[3 * i + 1], arr[3 * i + 2]];
const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

class Athlete {
  constructor(m, fp, card, prefix, policy) {
    const id = (kind, n) => { const a = m[kind](prefix + n), i = a.id; a.delete(); return i; };
    this.name = prefix.replace(/_$/, ""); this.card = card; this.policy = policy;
    this.order = fp.joint_order;
    const J = this.order.map(n => id("jnt", n));
    this.act = this.order.map(n => id("actuator", n));
    this.qa = J.map(j => m.jnt_qposadr[j]); this.va = J.map(j => m.jnt_dofadr[j]);
    const root = id("jnt", "root");
    this.rq = m.jnt_qposadr[root]; this.rv = m.jnt_dofadr[root];
    this.pelvis = id("body", "pelvis");
    this.head = id("geom", "head"); this.hand = id("geom", "hand_r"); this.headBody = m.geom_bodyid[this.head];
    this.feet = [id("geom", "foot_l"), id("geom", "foot_r")]; this.hands = [id("geom", "hand_l"), this.hand];
    this.geoms = new Set();
    for (let g = 0; g < m.ngeom; g++) if (m.body_rootid[m.geom_bodyid[g]] === this.pelvis) this.geoms.add(g);
    this.box = [m.qpos0[this.rq], m.qpos0[this.rq + 1]];   // foot-box centre = start pelvis x, y
    this.standPose = this.order.map(n => fp.stand_pose_rad[n] ?? 0);
    this.lo = fp.ctrl_range_rad.map(r => r[0]); this.hi = fp.ctrl_range_rad.map(r => r[1]);
    const s = fp.max_joint_speed_rad_s;
    this.maxStep = this.order.map((_, i) => (Array.isArray(s) ? s[i] : s) * 0.02);
    this.neck = this.act.filter((_, i) => this.order[i].startsWith("neck"));
    this.fr0 = this.neck.map(a => [m.actuator_forcerange[2 * a], m.actuator_forcerange[2 * a + 1]]);
    this.hipL = this.order.indexOf("hip_flex_l"); this.hipR = this.order.indexOf("hip_flex_r");
    this.kneeL = this.order.indexOf("knee_l"); this.kneeR = this.order.indexOf("knee_r");
    this.ankleL = this.order.indexOf("ankle_flex_l"); this.ankleR = this.order.indexOf("ankle_flex_r");
    this.shL = this.order.indexOf("shoulder_swing_l"); this.shR = this.order.indexOf("shoulder_swing_r");
    this.fp = fp; this.mem = [0, 0]; this.stamina = 1; this.neckStrength = 1; this.reset();
  }
  reset() { this.goal = this.standPose.slice(); this.prev = new Float32Array(this.order.length); this.hist = []; }

  obs(d, role = 0, clock = 0, target = null, hand = null) {
    const o = new Float64Array(OBS), N = this.order.length, q = d.qpos, v = d.qvel;
    for (let i = 0; i < N; i++) { o[i] = q[this.qa[i]] - this.standPose[i]; o[N + i] = v[this.va[i]] * 0.1; }
    const R = quatMat(q[this.rq + 3], q[this.rq + 4], q[this.rq + 5], q[this.rq + 6]);
    const yaw = Math.atan2(R[3], R[0]), c = Math.cos(yaw), s = Math.sin(yaw);
    const hd = (x, y, z) => [c * x + s * y, -s * x + c * y, z];   // world -> heading frame
    const P = v3(d.xpos, this.pelvis);
    o[60] = P[2];
    o.set([-R[6], -R[7], -R[8]], 61);
    o.set(hd(v[this.rv], v[this.rv + 1], v[this.rv + 2]), 64);
    o.set([v[this.rv + 3] * 0.25, v[this.rv + 4] * 0.25, v[this.rv + 5] * 0.25], 67);
    o.set(this.prev, 70);
    o[100] = role;
    if (role === 0) o.set(hd(this.box[0] - q[this.rq], this.box[1] - q[this.rq + 1], 0), 102);
    else { o[101] = clock; o.set(hd(target[0] - P[0], target[1] - P[1], target[2] - P[2]), 102); }
    if (hand) o.set(hd(hand[0] - P[0], hand[1] - P[1], hand[2] - P[2]), 105);
    const cur = Array.from(o.subarray(102, 108));
    this.hist = (this.hist.length ? this.hist : Array(16).fill(cur)).slice(-15).concat([cur]);
    LAGS.forEach((lag, i) => o.set(this.hist[this.hist.length - 1 - lag], 108 + 6 * i));
    o[126] = this.mem[0]; o[127] = this.mem[1]; o[128] = this.stamina; o[129] = this.neckStrength;
    const H = v3(d.geom_xpos, this.hand), X = d.geom_xmat, b = 9 * this.hand;
    o.set(hd(H[0] - P[0], H[1] - P[1], H[2] - P[2]), 130);
    o.set(hd(-X[b + 2], -X[b + 5], -X[b + 8]), 133);   // own palm: the hand box's -z axis
    return Float32Array.from(o);
  }

  decide(action) {
    for (let i = 0; i < this.order.length; i++) {
      const a = clamp(action[i], -1, 1), st = this.standPose[i];
      const t = clamp(st + (a > 0 ? a * (this.hi[i] - st) : a * (st - this.lo[i])), this.lo[i], this.hi[i]);
      const step = this.maxStep[i] * this.stamina;   // fatigue: a tired athlete moves slower
      this.goal[i] = clamp(t, this.goal[i] - step, this.goal[i] + step);
      this.prev[i] = a;
    }
  }
}

export class Match {
  // defenderRole: -1 absorb (brains from L3 on: they see the striker's head and hand), 0 guard and brace (earlier brains)
  constructor(mj, m, d, corners, emit, defenderRole = 0) {
    this.mj = mj; this.m = m; this.d = d; this.emit = emit; this.defenderRole = defenderRole;
    this.sides = corners.map(c => new Athlete(m, c.fp, c.card, c.prefix, c.policy));
    const gid = n => { const a = m.geom(n); const i = a.id; a.delete(); return i; };
    this.floor = gid("floor"); this.podium = gid("podium"); this.surface = new Set([this.floor, this.podium]);
    this.dt = m.opt.timestep; this.series = [0, 0]; this.matchNo = 0;
    this.newMatch();
  }

  newMatch() {
    this.matchNo++; this.first = Math.random() < 0.5 ? 0 : 1; this.round = 1; this.turnInRound = 0;
    this.cards = WEIGHTS.map(() => [0, 0]); this.recs = []; this.result = null; this.lastWinner = null;
    for (const x of this.sides) { x.mem = [0, 0]; x.stamina = 1; x.neckStrength = 1; }
    this.emit({ type: "match_start", match: this.matchNo, first: this.sides[this.first].name });
    if (typeof window !== "undefined" && !new URLSearchParams(location.search).has("nowalkin")) {
      this.stand();
      this.walkInTotal = this.walkInLeft = Math.round(WALKIN / this.dt);
      this.walkInProg = 0;
      this.phase = "walkin";
      return;
    }
    this.startTurn();
  }

  stand() {   // brain_cpu.stand(): reset, each corner's stand pose, forward
    const { mj, m, d } = this;
    mj.mj_resetData(m, d);
    for (const x of this.sides) x.order.forEach((n, i) => { d.qpos[x.qa[i]] = x.standPose[i]; });
    mj.mj_forward(m, d);
  }

  startTurn() {
    const { m, d } = this;
    this.s = this.turnInRound === 0 ? this.first : 1 - this.first;
    this.stand();
    for (const x of this.sides) {
      x.reset();
      x.neck.forEach((a, i) => { m.actuator_forcerange[2 * a] = x.fr0[i][0] * x.neckStrength; m.actuator_forcerange[2 * a + 1] = x.fr0[i][1] * x.neckStrength; });
    }
    this.mj.mj_forward(m, d);
    this.feet0 = this.sides.map(x => x.feet.map(f => [d.geom_xpos[3 * f], d.geom_xpos[3 * f + 1]]));
    const S = this.sides[this.s], D = this.sides[1 - this.s];
    this.handPrev = v3(d.geom_xpos, S.hand); this.headPrev = v3(d.geom_xpos, D.head);
    this.rec = { round: this.round, striker: S.name, touch: null, part: null, palm: false, speed: 0, head_peak: 0, stagger: false, fouls: [], ko: false, end: null };
    this.touchK = null; this.com0 = null; this.kick = [0, 0, 0, 0, 0, 0]; this.k = 0; this.phase = "turn";
    this.emit({ type: "turn_start", round: this.round, striker: S.name, defender: D.name });
  }

  down(x, defender) {   // Blueprint 9: a non-sole geom on floor or podium; the defender's palms may rest on the podium
    const cs = this.d.contact, n = this.d.ncon;
    let part = null;
    for (let i = 0; i < n && !part; i++) {
      const c = cs.get(i), g0 = c.geom1, g1 = c.geom2; c.delete();
      const sg = this.surface.has(g0) ? g0 : this.surface.has(g1) ? g1 : -1;
      if (sg < 0) continue;
      const o = sg === g0 ? g1 : g0;
      if (this.surface.has(o) || !x.geoms.has(o) || x.feet.includes(o)) continue;
      if (defender && sg === this.podium && x.hands.includes(o)) continue;
      part = this.m.geom(o); const nm = part.name; part.delete(); part = nm.replace(/^.*?(red|blue)_/, "");
    }
    cs.delete();
    return part;
  }

  // one physics step (200 Hz) of the current turn or hold; brains decide every 4th step
  async step() {
    if (this.phase === "walkin") return this.walkinStep();
    if (this.phase === "hold" || this.phase === "after") return this.holdStep();
    if (this.phase !== "turn") return;
    const { mj, m, d, dt } = this, S = this.sides[this.s], D = this.sides[1 - this.s], rec = this.rec, t = this.k * dt;
    if (this.k % 4 === 0) {
      for (const x of [D, S]) {
        const part = this.down(x, x === D);
        if (!part) continue;
        this.emit({ type: "knockdown", t, athlete: x.name, part });
        if (x === D && this.touchK !== null) { rec.ko = true; rec.end = "KO"; }
        else { rec.fouls.push([x.name, x === S ? "knocked down" : "down before the strike"]); rec.end = "knockdown"; }
        return this.finish();
      }
      for (let a = 0; a < 2; a++) {
        const x = this.sides[a];
        const out = x.feet.some((f, j) => Math.abs(d.geom_xpos[3 * f] - this.feet0[a][j][0]) > BOX[0] || Math.abs(d.geom_xpos[3 * f + 1] - this.feet0[a][j][1]) > BOX[1]);
        if (out) { rec.fouls.push([x.name, "out of the box"]); rec.end = "out of the box"; this.emit({ type: "foul", t, athlete: x.name, why: "out of the box" }); return this.finish(); }
      }
    }
    if (this.touchK === null && t >= STRIKE_CLOCK) { rec.fouls.push([S.name, "no strike in 30 s"]); rec.end = "clock"; return this.finish(); }
    if (this.touchK !== null && (this.k - this.touchK) * dt >= RECOVERY) { rec.end = "recovered"; return this.finish(); }
    if (this.k % 4 === 0) {
      mj.mj_kinematics(m, d);
      const clock = 1 - t / STRIKE_CLOCK, hd = v3(d.geom_xpos, D.head);
      // one brain at a time: the browser's ONNX Runtime refuses a second run while one is in flight
      const dObs = this.defenderRole < 0 ? D.obs(d, this.defenderRole, clock, v3(d.geom_xpos, S.head), v3(d.geom_xpos, S.hand)) : D.obs(d, 0);
      const aS = await S.policy(S.obs(d, 1, clock, hd)), aD = await D.policy(dObs);
      S.decide(aS); D.decide(aD);
      this.clock = STRIKE_CLOCK - t;
    }
    this.applyCtrl();
    for (const x of this.sides) d.xfrc_applied.fill(0, 6 * x.headBody, 6 * x.headBody + 6);
    if (this.touchK !== null && this.k - this.touchK < KICK_STEPS) d.xfrc_applied.set(this.kick, 6 * D.headBody);
    mj.mj_step(m, d);
    this.k++;
    if (!d.qpos.every(Number.isFinite)) { rec.end = "blow-up"; this.emit({ type: "blowup" }); this.phase = "over"; return; }
    const hand = v3(d.geom_xpos, S.hand), speed = dist(hand, this.handPrev) / dt;
    const vel = hand.map((h, i) => (h - this.handPrev[i]) / dt); this.handPrev = hand; this.speedNow = speed;
    const head = v3(d.geom_xpos, D.head);
    if (this.touchK !== null) rec.head_peak = Math.max(rec.head_peak, dist(head, this.headPrev) / dt);
    this.headPrev = head;
    if (this.touchK === null) this.checkTouch(S, D, speed, vel);
  }

  checkTouch(S, D, speed, vel) {
    const { m, d, dt } = this, cs = d.contact, n = d.ncon;
    for (let i = 0; i < n; i++) {
      const c = cs.get(i), g0 = c.geom1, g1 = c.geom2;
      if ((g0 === D.head || g1 === D.head) && S.geoms.has(g0 === D.head ? g1 : g0)) {
        const other = g0 === D.head ? g1 : g0, sign = g0 === other ? 1 : -1, F = c.frame, b = 9 * S.hand, X = d.geom_xmat;
        const palm = other === S.hand && -sign * (F[0] * X[b + 2] + F[1] * X[b + 5] + F[2] * X[b + 8]) > 0.5;
        const pos = [c.pos[0], c.pos[1], c.pos[2]];
        c.delete();
        const a = m.geom(other), part = a.name.replace(/^.*?(red|blue)_/, ""); a.delete();
        this.touchK = this.k; this.com0 = [d.subtree_com[3 * D.pelvis], d.subtree_com[3 * D.pelvis + 1]];
        Object.assign(this.rec, { touch: this.k * dt, part, palm, speed: Math.round(speed * 100) / 100 });
        this.emit({ type: "strike", t: this.k * dt, striker: S.name, part, palm, speed, pos });
        // the body behind the palm (card strike_mass): an impulse on the head over KICK_STEPS physics steps
        const k = S.card.strike_mass / (KICK_STEPS * dt), f = vel.map(v => v * k), C = v3(d.xipos, D.headBody);
        const r = [pos[0] - C[0], pos[1] - C[1], pos[2] - C[2]];
        this.kick = [f[0], f[1], f[2], r[1] * f[2] - r[2] * f[1], r[2] * f[0] - r[0] * f[2], r[0] * f[1] - r[1] * f[0]];
        break;
      }
      c.delete();
    }
    cs.delete();
  }

  applyCtrl() { for (const x of this.sides) x.act.forEach((a, i) => { this.d.ctrl[a] = x.goal[i]; }); }

  finish() {
    const { d } = this, rec = this.rec, S = this.sides[this.s], D = this.sides[1 - this.s];
    if (this.com0) rec.stagger = Math.hypot(d.subtree_com[3 * D.pelvis] - this.com0[0], d.subtree_com[3 * D.pelvis + 1] - this.com0[1]) > 0.05;
    rec.head_peak = Math.round(rec.head_peak * 100) / 100;
    S.stamina = Math.max(MIN_STAMINA, S.stamina - S.card.stamina_per_strike);
    if (rec.touch !== null) {
      D.mem = [rec.touch / 30, rec.speed / 10];
      if (!rec.ko) D.neckStrength = Math.max(MIN_NECK, D.neckStrength - D.card.neck_damage_per_snap * rec.head_peak);
    }
    this.recs.push([this.s, rec]);
    this.emit({ type: "turn_end", ...rec });
    if (rec.ko) { this.endMatch("KO", this.s); return; }
    this.turnInRound++;
    if (this.turnInRound === 2) { this.scoreRound(); this.turnInRound = 0; this.round++; }
    if (this.round > ROUNDS) { this.decision(); return; }
    this.phase = "hold"; this.holdLeft = Math.round(HOLD / this.dt);
  }

  async walkinStep() {
    const { mj, m, d } = this;
    const prog = 1 - this.walkInLeft / Math.max(1, this.walkInTotal);
    this.walkInProg = prog;
    const s = clamp(prog / 0.85, 0, 1), u = s * s * (3 - 2 * s);
    const offset = 1.60 * (1 - u), env = prog < 0.85 ? Math.sin(Math.PI * s) : 0;
    const phase = 2 * Math.PI * 1.8 * prog * WALKIN;
    this.sides.forEach((x, a) => {
      const sign = a === 0 ? -1 : 1;
      d.qpos[x.rq] = m.qpos0[x.rq] + sign * offset;
      d.qpos[x.rq + 1] = m.qpos0[x.rq + 1];
      d.qpos[x.rq + 2] = m.qpos0[x.rq + 2] + 0.015 * env * Math.abs(Math.sin(phase));
      for (let k = 3; k < 7; k++) d.qpos[x.rq + k] = m.qpos0[x.rq + k];
      for (let k = 0; k < 6; k++) d.qvel[x.rv + k] = 0;
      const sinP = Math.sin(phase + (a === 1 ? Math.PI * 0.5 : 0));
      const cosP = Math.cos(phase + (a === 1 ? Math.PI * 0.5 : 0));
      const setJ = (idx, delta) => {
        if (idx < 0) return;
        const val = clamp(x.standPose[idx] + env * delta, x.lo[idx], x.hi[idx]);
        d.qpos[x.qa[idx]] = val; x.goal[idx] = val;
      };
      setJ(x.hipL, 0.38 * sinP); setJ(x.hipR, -0.38 * sinP);
      setJ(x.kneeL, 0.55 * Math.max(0, sinP)); setJ(x.kneeR, 0.55 * Math.max(0, -sinP));
      setJ(x.ankleL, 0.18 * cosP); setJ(x.ankleR, -0.18 * cosP);
      setJ(x.shL, -0.30 * sinP); setJ(x.shR, 0.30 * sinP);
    });
    mj.mj_forward(m, d);
    if (--this.walkInLeft <= 0) this.startTurn();
  }

  async holdStep() {   // the crowd sees the result: both stand (guard role) before the next turn resets them
    const { mj, m, d } = this;
    if (this.holdLeft % 4 === 0) {
      mj.mj_kinematics(m, d);
      for (const x of this.sides) x.decide(await x.policy(x.obs(d, 0)));
      if (this.phase === "after" && this.lastWinner !== null) {
        const w = this.sides[this.lastWinner];
        if (w.shL >= 0) w.goal[w.shL] = clamp(w.standPose[w.shL] + 0.85, w.lo[w.shL], w.hi[w.shL]);
        if (w.shR >= 0) w.goal[w.shR] = clamp(w.standPose[w.shR] + 0.85, w.lo[w.shR], w.hi[w.shR]);
      }
    }
    this.applyCtrl(); mj.mj_step(m, d);
    if (--this.holdLeft <= 0) { if (this.phase === "hold") this.startTurn(); else this.newMatch(); }
  }

  scoreRound() {   // match.py play(): three judges, 10-point must, the defender scores for absorbing
    const recs = this.recs.slice(-2), names = this.sides.map(x => x.name);
    WEIGHTS.forEach((w, j) => {
      const eff = [0, 0], stag = [false, false];
      for (const [s, r] of recs) {
        if (r.touch !== null) {
          eff[s] += w[0] * r.speed + w[1] * 5 * r.head_peak + w[2] * 10 * r.stagger;
          stag[s] = r.stagger;
          if (!r.ko) eff[1 - s] += w[3] * (10 * !r.stagger + Math.max(0, 10 - 5 * r.head_peak));
        }
      }
      const hi = eff[1] > eff[0] ? 1 : 0, pts = [10, 10];
      if (Math.abs(eff[0] - eff[1]) > 0.05 * Math.max(...eff)) pts[1 - hi] = stag[hi] && eff[hi] >= 2 * eff[1 - hi] ? 8 : 9;
      for (const [, r] of recs) for (const [who] of r.fouls) pts[names.indexOf(who)] -= 1;
      this.cards[j][0] += pts[0]; this.cards[j][1] += pts[1];
    });
    this.emit({ type: "round", round: this.round, cards: this.cards.map(c => c.slice()) });
  }

  decision() {
    const votes = this.cards.map(c => c[0] > c[1] ? 0 : c[1] > c[0] ? 1 : null), wins = [0, 1].map(s => votes.filter(v => v === s).length);
    const best = wins[0] === wins[1] ? null : wins[0] > wins[1] ? 0 : 1;
    const how = best === null || wins[best] < 2 ? "draw" : wins[best] === 3 ? "unanimous decision" : wins[1 - best] === 1 ? "split decision" : "majority decision";
    this.endMatch(how, how === "draw" ? null : best);
  }

  endMatch(how, winner) {
    this.lastWinner = winner;
    if (winner !== null) this.series[winner]++;
    this.result = { how, winner: winner === null ? null : this.sides[winner].name, cards: this.cards };
    this.emit({ type: "result", ...this.result });
    this.phase = "after"; this.holdLeft = Math.round(5 / this.dt);   // 5 s on the result, then a new match
  }
}
