// The broadcast sound in the browser. Two engines behind one set of cues (the ones ASlapHUD plays):
//
// samples (phones, the low tier, or the user's pick): the synthesised takes Unreal plays (Scripts/broadcast_audio.py
//   writes both), stereo-panned by where they happened, filtered by the air, sent to the arena's impulse response.
// synth (desktop, 2026-11-04): every impact is synthesised the moment it happens from what MuJoCo says about it. A
//   material matrix (MATERIALS: palm-cheek, palm-jaw, back of the hand on the face, sole-mat, sole-deck, body-mat,
//   body-deck, body-podium, palm-podium, body-body) gives each pair its resonator bank (modes: frequency, T60,
//   amplitude) and its contact noise; the hit excites it with a half-sine force pulse whose length follows Hertz
//   contact (tau ~ v^-1/5: a harder hit is shorter, so brighter: each mode is weighted by the pulse's spectrum) and
//   whose strength follows the collision energy (1/2 m v^2: the strike mass x palm speed for a slap; the contact
//   probe's approach speed and effective mass for the rest, Web/contacts.js). Planted soles that slide drive a
//   continuous friction voice per athlete (band noise whose level and centre follow the shear speed and the normal
//   force, and a rubber squeak from stick-slip on the deck). Every source is a PannerNode with the HRTF model at its
//   place in the arena, heard from the camera (binaural on headphones): the impacts at the contact, the friction at
//   the sliding sole, the breath and grunts at each athlete's head. Early reflections are image sources off the
//   arena's own surfaces (the floor, mat or deck under the reflection point, the podium's faces when the path really
//   meets them, the bowl's walls and the rig above), each its own delayed, absorbed, HRTF-placed copy; the ArenaIR
//   convolution supplies the late tail.
// Both: the crowd loop and its granular layer, the PA chime, the bell, a limiter on the master. Browsers only start
// audio after a click, so the context opens on the first START / gesture.
const NAMES = ["Slap", "Slap1", "Slap2", "Slap3", "Thud", "Squeak0", "Squeak1", "Scrape", "Step", "StepDeck", "Breath",
  "Breath_kim", "Breath_matt", "Grunt_grandpa", "Grunt_kim", "Grunt_matt", "CrowdReact", "CrowdNear", "CrowdLoop", "Bell",
  "PAChime", "ArenaIR"];
const MAT_RADIUS = 2.7;   // m: inside it the athletes stand on the padded mat, outside on the hard stage deck
const C_AIR = 343;        // m/s

// ---- the material matrix -------------------------------------------------------------------------------------------
// modes: [Hz, T60 s, amplitude]; noise: [low Hz, high Hz, decay s, level]; tau: contact time (s) at v0 (m/s); eref:
// the collision energy (J) that plays at `gain` (louder hits grow as sqrt(E/eref), up to 2.5x); glide: how far the
// modes start sharp and fall (a soft contact stiffens as it compresses, so a flesh hit "drops" in pitch)
export const MATERIALS = {
  "palm-cheek":  { modes: [[175, 0.05, 1.0], [320, 0.04, 0.6], [540, 0.03, 0.35], [910, 0.02, 0.2]], noise: [1600, 5200, 0.016, 1.3],
                   tau: 0.0032, v0: 9, eref: 120, gain: 0.9, glide: 0.12 },
  "palm-jaw":    { modes: [[230, 0.06, 0.8], [700, 0.04, 0.55], [1480, 0.025, 0.35], [2650, 0.016, 0.2]], noise: [2000, 6500, 0.011, 1.1],
                   tau: 0.0021, v0: 9, eref: 120, gain: 0.9, glide: 0.05 },
  "hand-face":   { modes: [[140, 0.06, 1.0], [430, 0.04, 0.5], [1100, 0.02, 0.3]], noise: [1000, 3200, 0.012, 0.7],
                   tau: 0.0045, v0: 8, eref: 100, gain: 0.8, glide: 0.08 },
  "sole-mat":    { modes: [[85, 0.08, 1.0], [190, 0.05, 0.5], [420, 0.03, 0.2]], noise: [250, 1400, 0.02, 0.6],
                   tau: 0.012, v0: 0.8, eref: 3, gain: 0.35, glide: 0.0 },
  "sole-deck":   { modes: [[110, 0.18, 1.0], [235, 0.14, 0.7], [480, 0.09, 0.45], [1020, 0.05, 0.3], [2100, 0.03, 0.15]], noise: [700, 4200, 0.01, 0.7],
                   tau: 0.004, v0: 0.8, eref: 3, gain: 0.45, glide: 0.0 },
  "body-mat":    { modes: [[58, 0.14, 1.0], [128, 0.08, 0.6], [300, 0.04, 0.3]], noise: [140, 900, 0.05, 0.7],
                   tau: 0.02, v0: 2, eref: 40, gain: 1.1, glide: 0.04 },
  "body-deck":   { modes: [[72, 0.25, 1.0], [160, 0.2, 0.7], [350, 0.12, 0.4], [800, 0.06, 0.2]], noise: [300, 2600, 0.03, 0.6],
                   tau: 0.008, v0: 2, eref: 40, gain: 1.1, glide: 0.03 },
  "body-podium": { modes: [[95, 0.3, 1.0], [205, 0.22, 0.8], [410, 0.14, 0.5], [870, 0.08, 0.3], [1700, 0.04, 0.15]], noise: [400, 3200, 0.02, 0.5],
                   tau: 0.006, v0: 1.5, eref: 20, gain: 0.9, glide: 0.0 },
  "palm-podium": { modes: [[180, 0.2, 0.8], [400, 0.14, 0.6], [900, 0.08, 0.4], [1900, 0.04, 0.2]], noise: [900, 4800, 0.008, 0.8],
                   tau: 0.003, v0: 1, eref: 2, gain: 0.45, glide: 0.0 },
  "body-body":   { modes: [[110, 0.06, 1.0], [260, 0.04, 0.4]], noise: [400, 1800, 0.02, 0.5],
                   tau: 0.01, v0: 1.5, eref: 10, gain: 0.5, glide: 0.06 },
};
// the arena's reflecting surfaces for the image sources (MuJoCo frame, metres): the plane (axis, offset), how much it
// gives back, how much treble it keeps, and the face's extent (a reflection counts only where it lands on the face)
const REFLECTORS = [
  { axis: 2, at: 0, floor: true },                                                         // the floor: mat or deck by where it lands
  { axis: 0, at: -0.1, coef: 0.6, lp: 5000, lim: [[-0.35, 0.35], [0, 1.0]] },              // the podium's red face
  { axis: 0, at: 0.1, coef: 0.6, lp: 5000, lim: [[-0.35, 0.35], [0, 1.0]] },               // the podium's blue face
  { axis: 0, at: -15, coef: 0.3, lp: 2200 }, { axis: 0, at: 15, coef: 0.3, lp: 2200 },     // the bowl: people absorb
  { axis: 1, at: -15, coef: 0.3, lp: 2200 }, { axis: 1, at: 15, coef: 0.3, lp: 2200 },
  { axis: 2, at: 12, coef: 0.45, lp: 3000 },                                               // the lighting rig and roof
];

function hallIR(ctx, seconds = 1.8, hfRatio = 0.7) {   // the fallback room when ArenaIR.wav is missing
  const n = Math.round(seconds * ctx.sampleRate), ir = ctx.createBuffer(2, n, ctx.sampleRate);
  for (let c = 0; c < 2; c++) {
    const x = ir.getChannelData(c);
    let lp = 0;
    for (let i = 0; i < n; i++) {
      const t = i / ctx.sampleRate, w = Math.random() * 2 - 1;
      const k = Math.min(0.95, 0.2 + t / (seconds * hfRatio));   // the treble dies faster: a one-pole that closes with time
      lp = lp * k + w * (1 - k);
      x[i] = t < 0.02 ? 0 : lp * Math.exp(-6.9 * t / seconds) * (1 + 2 * k);
    }
  }
  return ir;
}

// ---- modal impact synthesis: one hit rendered into a buffer -------------------------------------------------------
// v: approach speed (m/s), energy: J. Deterministic for a given seed (the report renders the same match twice).
export function synthImpact(sr, mat, v, energy, seed = Math.random()) {
  let st = Math.floor(seed * 2147483646) + 1;
  const rnd = () => (st = st * 16807 % 2147483647) / 2147483647;
  const tau = mat.tau * Math.pow(Math.max(0.05, v) / mat.v0, -0.2);
  const A = mat.gain * Math.min(2.5, Math.sqrt(Math.max(0, energy) / mat.eref));
  const tail = Math.max(...mat.modes.map(m => m[1]), mat.noise[2] * 4);
  const n = Math.min(Math.round(sr * 1.5), Math.round(sr * (tail * 1.1 + 0.02))), out = new Float32Array(n);
  const spec = f => { const x = 2 * f * tau; return Math.abs(1 - x * x) < 1e-3 ? Math.PI / 4 : Math.abs(Math.cos(Math.PI * f * tau) / (1 - x * x)); };
  const bright = 1 + 0.06 * Math.log2(Math.max(0.1, v / mat.v0));   // a harder hit stiffens the contact: modes ride up
  const att = Math.max(1e-4, tau * 0.5);
  for (const [f0, t60, a] of mat.modes) {
    const f = f0 * bright * (1 + 0.04 * (rnd() - 0.5)), w = a * spec(f), k = 6.91 / t60;
    let ph = rnd() * 0.3;
    for (let i = 0; i < n; i++) {
      const t = i / sr, fi = f * (1 + mat.glide * Math.exp(-t / 0.015));
      ph += 2 * Math.PI * fi / sr;
      out[i] += w * Math.sin(ph) * Math.exp(-k * t) * (1 - Math.exp(-t / att));
    }
  }
  // the contact noise: white noise through a band-pass (RBJ biquad) that opens with a shorter (harder) contact
  const [lo, hi0, dec, lvl] = mat.noise, hi = Math.min(sr * 0.45, hi0 * Math.sqrt(mat.tau / tau));
  const fc = Math.sqrt(lo * hi), Q = fc / Math.max(1, hi - lo), w0 = 2 * Math.PI * fc / sr, al = Math.sin(w0) / (2 * Q), a0 = 1 + al;
  const b0 = al / a0, b2 = -al / a0, a1 = -2 * Math.cos(w0) / a0, a2 = (1 - al) / a0;
  let x1 = 0, x2 = 0, y1 = 0, y2 = 0;
  const nn = Math.min(n, Math.round(sr * (tau + dec * 6)));
  for (let i = 0; i < nn; i++) {
    const t = i / sr, x = rnd() * 2 - 1, y = b0 * x + b2 * x2 - a1 * y1 - a2 * y2;
    x2 = x1; x1 = x; y2 = y1; y1 = y;
    const env = (t < tau ? Math.sin(Math.PI * 0.5 * t / tau) : Math.exp(-(t - tau) / dec));
    out[i] += y * env * lvl * 2.2;
  }
  for (let i = 0; i < n; i++) out[i] = Math.tanh(out[i] * A * 0.6);   // the mic and the preamp: soft saturation
  return out;
}

// which material pair a contact probe impact is (null: not voiced here: the palm on the face is the strike cue)
function pairOf(imp) {
  const { part, surf, other } = imp;
  if (surf === "body") {
    if ((part === "hand" && other === "head") || (part === "head" && other === "hand")) return null;
    return "body-body";
  }
  if (part === "sole") return surf === "mat" ? "sole-mat" : surf === "deck" ? "sole-deck" : surf === "podium" ? "body-podium" : null;
  if (part === "hand" && surf === "podium") return "palm-podium";
  return surf === "mat" ? "body-mat" : surf === "deck" ? "body-deck" : surf === "podium" ? "body-podium" : null;
}

export class Sound {
  constructor() {
    this.ctx = null; this.buf = {}; this.loops = {}; this.breaths = {};
    this.muted = localStorage.getItem("poslap.sound") === "off";
    this.duckUntil = 0; this.tension = 0; this.grainAt = 0;
    this.room = 0.5;   // 0 ringside (dry) .. 1 the far seats (the hall rings out): setDistance
    this.voice = { red: "grandpa", blue: "grandpa" };
    // the engine: ?audio=synth|samples, else the saved pick, else auto (synth on a desktop above the low tier)
    this.mode = new URLSearchParams(location.search).get("audio") || localStorage.getItem("poslap.audio") || "auto";
    this.coarse = matchMedia("(pointer: coarse)").matches;
    this.tierHint = "high";
    this.heads = [[-0.42, 0, 1.6], [0.42, 0, 1.6]]; this.lastHit = [0, 0]; this.fric = null;
  }
  get synth() { return this.mode === "synth" || (this.mode === "auto" && !this.coarse && this.tierHint !== "low"); }
  setMode(m) { this.mode = m; localStorage.setItem("poslap.audio", m); if (this.ctx) this.startBreaths(true); }
  // the camera's distance to the action (m) sets how much hall a one-shot gets (SlapHUD.cpp ReverbMix: 1.5 m dry, 5 m
  // wet) and how much treble the air takes off the direct sound (~ -1 dB/m above 8 kHz at this range: the cutoff falls)
  setDistance(m) {
    this.room = Math.max(0, Math.min(1, (m - 1.5) / 3.5));
    if (this.air) this.air.frequency.setTargetAtTime(Math.max(6000, 18000 - 2200 * Math.max(0, m - 1.5)), this.ctx.currentTime, 0.2);
  }
  // the athletes' voices: { red: "<card name>", blue: ... } (Grunt_<name>, Breath_<name>; Grandpa's breath is Breath)
  setVoices(v) { Object.assign(this.voice, v); if (this.ctx) this.startBreaths(); }
  // ctx: an AudioContext to use (the report renders a match into an OfflineAudioContext); default a new real-time one
  async unlock(ctxIn) {
    if (this.ctx) { if (this.ctx.state === "suspended" && this.ctx.resume) await this.ctx.resume(); return; }
    const ctx = this.ctx = ctxIn || new AudioContext();
    this.master = ctx.createGain(); this.master.gain.value = this.muted && !ctxIn ? 0 : 0.9;
    const limit = ctx.createDynamicsCompressor();
    limit.threshold.value = -10; limit.knee.value = 6; limit.ratio.value = 12; limit.attack.value = 0.002; limit.release.value = 0.2;
    this.master.connect(limit).connect(ctx.destination);
    this.air = ctx.createBiquadFilter(); this.air.type = "lowpass"; this.air.frequency.value = 16000; this.air.Q.value = 0.5;
    this.air.connect(this.master);   // the direct sound of the one-shots
    this.hall = ctx.createConvolver();
    this.hall.connect(this.master);
    await Promise.all(NAMES.map(async n => {
      try { this.buf[n] = await ctx.decodeAudioData(await (await fetch(`./assets/audio/${n}.wav`)).arrayBuffer()); }
      catch (err) { console.warn(`sound ${n}:`, err); }
    }));
    this.hall.buffer = this.buf.ArenaIR || hallIR(ctx);
    const nb = this.noiseBuf = ctx.createBuffer(1, ctx.sampleRate * 2, ctx.sampleRate), nd = nb.getChannelData(0);
    for (let i = 0; i < nd.length; i++) nd[i] = Math.random() * 2 - 1;
    this.loops.crowd = this.loop("CrowdLoop", 0.3);
    this.startBreaths();
  }
  // a PannerNode at p (MuJoCo frame, m): HRTF for the direct sound, equal-power for the far reflections (cheaper)
  panner(p, hrtf = true) {
    const pn = this.ctx.createPanner();
    pn.panningModel = hrtf ? "HRTF" : "equalpower"; pn.distanceModel = "inverse"; pn.refDistance = 2; pn.rolloffFactor = 1; pn.maxDistance = 100;
    this.place(pn, p);
    return pn;
  }
  place(pn, p, tc = 0) {
    if (pn.positionX) {
      if (tc) { const t = this.ctx.currentTime; pn.positionX.setTargetAtTime(p[0], t, tc); pn.positionY.setTargetAtTime(p[1], t, tc); pn.positionZ.setTargetAtTime(p[2], t, tc); }
      else { pn.positionX.value = p[0]; pn.positionY.value = p[1]; pn.positionZ.value = p[2]; }
    } else pn.setPosition(p[0], p[1], p[2]);
  }
  // the listener is the camera (MuJoCo's right-handed Z-up frame is used as is: the panner only needs it consistent)
  listen(camera) {
    if (!this.ctx || !camera) return;
    const L = this.ctx.listener, p = camera.position, e = camera.matrixWorld.elements;
    const f = [-e[8], -e[9], -e[10]], u = [e[4], e[5], e[6]];
    this.ear = [p.x, p.y, p.z];
    if (L.positionX) {
      L.positionX.value = p.x; L.positionY.value = p.y; L.positionZ.value = p.z;
      L.forwardX.value = f[0]; L.forwardY.value = f[1]; L.forwardZ.value = f[2]; L.upX.value = u[0]; L.upY.value = u[1]; L.upZ.value = u[2];
    } else { L.setPosition(p.x, p.y, p.z); L.setOrientation(...f, ...u); }
  }
  startBreaths(rebuild = false) {   // one breathing loop per athlete, in his voice, from his head (synth) or his side (samples)
    for (const side of ["red", "blue"]) {
      const want = this.voice[side] === "grandpa" ? "Breath" : `Breath_${this.voice[side]}`;
      const name = this.buf[want] ? want : "Breath";
      if (!rebuild && this.breaths[side]?.name === name) continue;
      try { this.breaths[side]?.src.stop(); } catch { }
      const b = this.loop(name, 0, side === "red" ? -0.45 : 0.45, this.synth ? this.heads[side === "red" ? 0 : 1] : null);
      if (b) { b.name = name; b.src.playbackRate.value = 0.94 + 0.12 * Math.random(); this.breaths[side] = b; }   // no two in step
    }
  }
  setMuted(m) {
    this.muted = m; localStorage.setItem("poslap.sound", m ? "off" : "on");
    if (this.ctx) this.master.gain.setTargetAtTime(m ? 0 : 0.9, this.ctx.currentTime, 0.05);
  }
  loop(name, gain, pan = 0, at = null) {
    if (!this.buf[name]) return null;
    const src = this.ctx.createBufferSource(), g = this.ctx.createGain(), p = at ? this.panner(at) : this.ctx.createStereoPanner();
    src.buffer = this.buf[name]; src.loop = true; g.gain.value = gain; if (!at) p.pan.value = pan;
    src.connect(g).connect(p).connect(this.master); src.start(0, Math.random() * src.buffer.duration);
    return { src, g, p };
  }
  // a one-shot: gain, playback rate (pitch), pan (-1 left .. 1 right), wet (the hall send), dry (through the air filter);
  // at: a place in the arena (synth engine: HRTF from there instead of the stereo pan)
  play(name, { gain = 1, rate = 1, pan = 0, wet = 0.25, delay = 0, air = true, at = null } = {}) {
    const b = this.buf[name];
    if (!this.ctx || !b) return;
    const src = this.ctx.createBufferSource(), g = this.ctx.createGain(), p = at ? this.panner(at) : this.ctx.createStereoPanner(), send = this.ctx.createGain();
    src.buffer = b; src.playbackRate.value = rate; g.gain.value = gain; if (!at) p.pan.value = Math.max(-1, Math.min(1, pan));
    send.gain.value = wet * (0.35 + 1.3 * this.room);   // the reverb follows the camera: a close-up is dry, the wide shot rings
    src.connect(g).connect(p).connect(air ? this.air : this.master); g.connect(send).connect(this.hall);
    src.start(this.ctx.currentTime + delay);
  }
  // a synthesised hit at p: the direct sound, the image-source early reflections, the hall tail
  playAt(data, p, { gain = 1, wet = 0.2, delay = 0 } = {}) {
    const ctx = this.ctx, b = ctx.createBuffer(1, data.length, ctx.sampleRate);
    b.getChannelData(0).set(data);
    const src = ctx.createBufferSource(), g = ctx.createGain(), send = ctx.createGain(), t = ctx.currentTime + delay;
    src.buffer = b; g.gain.value = gain;
    src.connect(g); g.connect(this.panner(p)).connect(this.air);
    send.gain.value = wet * (0.35 + 1.3 * this.room); g.connect(send).connect(this.hall);
    const L = this.ear || [0, -4.6, 1.5], d0 = Math.hypot(p[0] - L[0], p[1] - L[1], p[2] - L[2]);
    for (const R of REFLECTORS) {   // image sources: mirror the hit in the plane, keep it if the path meets the face
      const I = p.slice(); I[R.axis] = 2 * R.at - p[R.axis];
      const s = (R.at - L[R.axis]) / (I[R.axis] - L[R.axis]);
      if (!(s > 0 && s < 1)) continue;
      const hit = [0, 1, 2].map(k => L[k] + (I[k] - L[k]) * s);
      if (R.lim) { const o = [0, 1, 2].filter(k => k !== R.axis); if (o.some((k, j) => hit[k] < R.lim[j][0] || hit[k] > R.lim[j][1])) continue; }
      let coef = R.coef, lp = R.lp;
      if (R.floor) { const onMat = Math.hypot(hit[0], hit[1]) < MAT_RADIUS; coef = onMat ? 0.35 : 0.8; lp = onMat ? 1800 : 9000; }
      const di = Math.hypot(I[0] - L[0], I[1] - L[1], I[2] - L[2]);
      const dl = ctx.createDelay(1), rg = ctx.createGain(), f = ctx.createBiquadFilter();
      dl.delayTime.value = Math.max(0, (di - d0) / C_AIR); rg.gain.value = coef; f.type = "lowpass"; f.frequency.value = lp;
      g.connect(dl).connect(rg).connect(f).connect(this.panner(I, R.floor || !!R.lim)).connect(this.air);
    }
    src.start(t);
  }
  // one grain of crowd: a 90-350 ms slice of the loop at a random point, its own pitch and place in the bowl
  grain(gain) {
    const b = this.buf.CrowdLoop;
    if (!b) return;
    const ctx = this.ctx, src = ctx.createBufferSource(), g = ctx.createGain(), p = ctx.createStereoPanner(), t = ctx.currentTime;
    const dur = 0.09 + Math.random() * 0.26, at = Math.random() * (b.duration - dur);
    src.buffer = b; src.playbackRate.value = 0.92 + Math.random() * 0.16; p.pan.value = Math.random() * 1.8 - 0.9;
    g.gain.setValueAtTime(0, t); g.gain.linearRampToValueAtTime(gain, t + dur * 0.3); g.gain.linearRampToValueAtTime(0, t + dur);
    src.connect(g).connect(p).connect(this.master); src.start(t, at, dur + 0.02);
  }
  // MuJoCo x (red at -X, on the left from the -Y camera) to a stereo position
  static pan(x) { return Math.max(-0.7, Math.min(0.7, x * 0.6)); }
  side(name) { return name === "blue" ? 1 : 0; }

  // ---- the cues (ASlapHUD's): what the match does, the room hears --------------------------------------------------
  // the strike: a slap take pitched by speed (samples), or the palm-cheek / palm-jaw / hand-face bank excited by the
  // strike's energy (synth); the striker's grunt a beat before it lands, in his own voice
  // opt: { pos: the contact [x, y, z], mass: the card's strike mass, jaw: below the head's centre, palm: the open hand }
  strike(speed, x, striker, opt = {}) {
    if (!this.ctx) return;
    const v = striker && this.voice[striker], head = striker ? this.heads[this.side(striker)] : null;
    if (this.synth) {
      const pair = opt.palm === false ? "hand-face" : opt.jaw ? "palm-jaw" : "palm-cheek", mass = opt.mass || 3;
      this.playAt(synthImpact(this.ctx.sampleRate, MATERIALS[pair], speed, 0.5 * mass * speed * speed), opt.pos || [x, 0, 1.5], { wet: 0.18 });
    } else {
      const k = Math.max(0.25, Math.min(1.5, speed / 9));
      this.play(["Slap", "Slap1", "Slap2", "Slap3"][Math.floor(Math.random() * 4)], { gain: k, rate: 0.94 + 0.12 * Math.random(), pan: Sound.pan(x), wet: 0.18 });
    }
    if (v && speed > 4) this.play(`Grunt_${v}`, { gain: 0.25 + 0.25 * Math.min(1, speed / 10), rate: 0.97 + 0.06 * Math.random(),
      pan: striker === "red" ? -0.35 : 0.35, wet: 0.12, delay: 0.01, at: this.synth ? head : null });
    this.duckUntil = this.ctx.currentTime + 0.2;
  }
  reaction(staggered, landed, k) {   // the crowd after a turn: a roar for a stagger, a groan for a slap that moved nobody
    const roar = 0.3 + 0.5 * k;
    if (staggered) this.play("CrowdReact", { gain: roar, wet: 0, air: false });
    else if (landed) this.play("CrowdNear", { gain: roar * 0.9, wet: 0, air: false });
  }
  // a near miss (a feint, or a palm that went past the face): the crowd's "ooooh", at most every 2.5 s
  nearMiss(k = 1) {
    const t = this.ctx?.currentTime || 0;
    if (t < (this.oohAt || 0) + 2.5) return;
    this.oohAt = t;
    this.play("CrowdNear", { gain: 0.45 + 0.4 * Math.min(1, k), rate: 1.08 + 0.06 * Math.random(), wet: 0, air: false });
  }
  knockdown(x) {   // synth: the body's own impacts come from the contact probe (body-mat, body-podium); the crowd here
    if (!this.synth) this.play("Thud", { gain: 1.2, rate: 0.9, pan: Sound.pan(x), wet: 0.3 });
    this.play("CrowdReact", { gain: 0.5, rate: 1.25, wet: 0, air: false });
  }
  bell(final = false) {
    this.play("Bell", { gain: 0.8, wet: 0.35 });
    if (final) this.play("CrowdReact", { gain: 1.4, rate: 0.9, wet: 0, air: false });
  }
  chime() { this.play("PAChime", { gain: 0.55, wet: 0.2, air: false }); }   // the PA before the bout (it carries its own hall)
  // a footstep (samples engine): the padded mat inside MAT_RADIUS (m from the centre), the hard deck outside it
  step(x, gain, r = 0) {
    if (this.synth) return;   // synth: the contact probe's sole impacts
    const deck = r > MAT_RADIUS && this.buf.StepDeck;
    this.play(deck ? "StepDeck" : "Step", { gain: gain * (deck ? 0.8 : 1), rate: 0.9 + 0.2 * Math.random(), pan: Sound.pan(x), wet: deck ? 0.22 : 0.15 });
  }
  skid(x, speed) {
    if (this.synth) return;   // synth: the friction voices follow the probe's shear speeds
    const snd = speed > 0.9 ? (Math.random() < 0.5 ? "Squeak0" : "Squeak1") : "Scrape";
    this.play(snd, { gain: Math.max(0.3, Math.min(1.2, speed / 1.5)), rate: 0.9 + 0.2 * Math.random(), pan: Sound.pan(x), wet: 0.15 });
  }
  // the contact probe's frame (Web/contacts.js): impacts voiced from the matrix, slides drive the friction voices,
  // the heads move the voices
  contacts(cx) {
    if (!this.ctx || !cx) return;
    for (let i = 0; i < 2; i++) { const h = cx.heads[i]; this.heads[i] = [h[0], h[1], h[2]]; const b = this.breaths[i ? "blue" : "red"]; if (b?.p?.positionX) this.place(b.p, this.heads[i], 0.05); }
    if (!this.synth) return;
    const t = this.ctx.currentTime;
    for (const imp of cx.impacts) {
      const pair = pairOf(imp);
      if (!pair || imp.vn < 0.15 || imp.energy < (imp.part === "sole" ? 0.04 : 0.2)) continue;
      if (t - this.lastHit[imp.side] < 0.03) continue;   // one voice per athlete per 30 ms (a box landing touches at 4 corners)
      this.lastHit[imp.side] = t;
      this.playAt(synthImpact(this.ctx.sampleRate, MATERIALS[pair], imp.vn, imp.energy), imp.pos, { wet: pair.startsWith("sole") ? 0.15 : 0.25 });
    }
    this.friction(cx.slides, cx.dt);
  }
  // the friction voices: per athlete, band noise (the scuff) and a rubber squeak (stick-slip), at the sliding sole
  friction(slides, dt) {
    const ctx = this.ctx, t = ctx.currentTime;
    if (!this.fric) {
      this.fric = [0, 1].map(i => {
        const src = ctx.createBufferSource(); src.buffer = this.noiseBuf; src.loop = true;
        const bp = ctx.createBiquadFilter(); bp.type = "bandpass"; bp.Q.value = 0.9; bp.frequency.value = 800;
        const gN = ctx.createGain(); gN.gain.value = 0;
        const osc = ctx.createOscillator(); osc.type = "sawtooth"; osc.frequency.value = 900;
        const sq = ctx.createBiquadFilter(); sq.type = "bandpass"; sq.Q.value = 9; sq.frequency.value = 1800;
        const gS = ctx.createGain(); gS.gain.value = 0;
        const pn = this.panner(this.heads[i].map((v, k) => k === 2 ? 0.02 : v));
        src.connect(bp).connect(gN).connect(pn); osc.connect(sq).connect(gS).connect(pn); pn.connect(this.air);
        src.start(0, Math.random() * 2); osc.start();
        return { bp, gN, osc, sq, gS, pn };
      });
    }
    for (let i = 0; i < 2; i++) {
      const s = slides[i], F = this.fric[i];
      const vt = s && dt > 0 ? s.vt : 0, load = s ? Math.sqrt(Math.max(0.05, Math.min(2, s.fn / 400))) : 0, deck = s?.surf === "deck";
      const scuff = vt > 0.04 ? Math.min(1, vt / 1.2) * load * (deck ? 0.32 : 0.22) : 0;
      F.gN.gain.setTargetAtTime(scuff, t, 0.03);
      F.bp.frequency.setTargetAtTime(deck ? 600 + 2400 * Math.min(1.5, vt) : 250 + 900 * Math.min(1.5, vt), t, 0.03);
      const stick = vt > 0.25 && vt < 2.2 && s.fn > 80 && Math.random() < 0.75;   // rubber grips and lets go: an intermittent squeal
      F.gS.gain.setTargetAtTime(stick ? (deck ? 0.09 : 0.05) * Math.min(1, (vt - 0.2) / 0.6) * load : 0, t, 0.015);
      if (stick) { const f = (deck ? 750 : 600) + 650 * vt; F.osc.frequency.setTargetAtTime(f * (0.97 + 0.06 * Math.random()), t, 0.01); F.sq.frequency.setTargetAtTime(f * 2, t, 0.02); }
      if (s) this.place(F.pn, s.pos, 0.02);
    }
  }
  // every frame: the murmur swells with the tension and ducks under a slap, the grains thicken with it; each athlete's
  // breathing follows his own stamina (staminaBlue absent: both follow `stamina`)
  tick(tension, stamina, staminaBlue) {
    if (!this.ctx) return;
    const t = this.ctx.currentTime, duck = t < this.duckUntil ? 0.3 : 1;
    if (this.loops.crowd) this.loops.crowd.g.gain.setTargetAtTime(0.3 * (1 + 1.4 * tension) * duck, t, 0.08);
    const rate = 6 + 26 * tension;   // grains per second
    if (t > this.grainAt) { this.grain((0.05 + 0.08 * tension) * duck); this.grainAt = t - Math.log(Math.random() + 1e-6) / rate; }
    const st = { red: stamina, blue: staminaBlue ?? stamina };
    for (const side of ["red", "blue"]) {
      const b = this.breaths[side];
      if (!b) continue;
      const need = Math.max(0, Math.min(1, (1 - st[side]) / 0.3));   // stamina floors at 0.7
      b.g.gain.setTargetAtTime(0.01 + 0.45 * need * need, t, 0.3);
      b.src.playbackRate.setTargetAtTime((0.94 + 0.12 * (side === "red" ? 0.3 : 0.7)) * (1 + 0.45 * need), t, 0.3);
    }
  }
}
