// The broadcast sound in the browser: the same synthesised sounds Unreal plays (Scripts/broadcast_audio.py writes both),
// cued the way ASlapHUD cues them. Each one-shot is panned by where it happened, filtered by the air between it and
// the camera (the treble goes first with distance), sent to the arena's impulse response (ArenaIR.wav: the floor, the
// podium and the judges' table in the first milliseconds, the stands at 40-70 ms, then a tail that dies 2.4 s low /
// 0.9 s high) and summed through a limiter. The crowd is the loop plus a granular layer cut from it (short grains at
// random points, each panned across the bowl at its own pitch) that thickens with the tension, so the 12 s loop never
// repeats audibly. Each athlete breathes in his own voice on his own side as his stamina goes, and grunts as he
// strikes. Browsers only start audio after a click, so the context opens on the first START / gesture.
const NAMES = ["Slap", "Slap1", "Slap2", "Slap3", "Thud", "Squeak0", "Squeak1", "Scrape", "Step", "StepDeck", "Breath",
  "Breath_kim", "Breath_matt", "Grunt_grandpa", "Grunt_kim", "Grunt_matt", "CrowdReact", "CrowdNear", "CrowdLoop", "Bell",
  "PAChime", "ArenaIR"];
const MAT_RADIUS = 2.7;   // m: inside it the athletes stand on the padded mat, outside on the hard stage deck

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

export class Sound {
  constructor() {
    this.ctx = null; this.buf = {}; this.loops = {}; this.breaths = {};
    this.muted = localStorage.getItem("poslap.sound") === "off";
    this.duckUntil = 0; this.tension = 0; this.grainAt = 0;
    this.room = 0.5;   // 0 ringside (dry) .. 1 the far seats (the hall rings out): setDistance
    this.voice = { red: "grandpa", blue: "grandpa" };
  }
  // the camera's distance to the action (m) sets how much hall a one-shot gets (SlapHUD.cpp ReverbMix: 1.5 m dry, 5 m
  // wet) and how much treble the air takes off the direct sound (~ -1 dB/m above 8 kHz at this range: the cutoff falls)
  setDistance(m) {
    this.room = Math.max(0, Math.min(1, (m - 1.5) / 3.5));
    if (this.air) this.air.frequency.setTargetAtTime(Math.max(6000, 18000 - 2200 * Math.max(0, m - 1.5)), this.ctx.currentTime, 0.2);
  }
  // the athletes' voices: { red: "<card name>", blue: ... } (Grunt_<name>, Breath_<name>; Grandpa's breath is Breath)
  setVoices(v) { Object.assign(this.voice, v); if (this.ctx) this.startBreaths(); }
  async unlock() {
    if (this.ctx) { if (this.ctx.state === "suspended") await this.ctx.resume(); return; }
    const ctx = this.ctx = new AudioContext();
    this.master = ctx.createGain(); this.master.gain.value = this.muted ? 0 : 0.9;
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
    this.loops.crowd = this.loop("CrowdLoop", 0.3);
    this.startBreaths();
  }
  startBreaths() {   // one breathing loop per athlete, in his voice, on his side of the stage
    for (const side of ["red", "blue"]) {
      const want = this.voice[side] === "grandpa" ? "Breath" : `Breath_${this.voice[side]}`;
      const name = this.buf[want] ? want : "Breath";
      if (this.breaths[side]?.name === name) continue;
      try { this.breaths[side]?.src.stop(); } catch { }
      const b = this.loop(name, 0, side === "red" ? -0.45 : 0.45);
      if (b) { b.name = name; b.src.playbackRate.value = 0.94 + 0.12 * Math.random(); this.breaths[side] = b; }   // no two in step
    }
  }
  setMuted(m) {
    this.muted = m; localStorage.setItem("poslap.sound", m ? "off" : "on");
    if (this.ctx) this.master.gain.setTargetAtTime(m ? 0 : 0.9, this.ctx.currentTime, 0.05);
  }
  loop(name, gain, pan = 0) {
    if (!this.buf[name]) return null;
    const src = this.ctx.createBufferSource(), g = this.ctx.createGain(), p = this.ctx.createStereoPanner();
    src.buffer = this.buf[name]; src.loop = true; g.gain.value = gain; p.pan.value = pan;
    src.connect(g).connect(p).connect(this.master); src.start(0, Math.random() * src.buffer.duration);
    return { src, g };
  }
  // a one-shot: gain, playback rate (pitch), pan (-1 left .. 1 right), wet (the hall send), dry (through the air filter)
  play(name, { gain = 1, rate = 1, pan = 0, wet = 0.25, delay = 0, air = true } = {}) {
    const b = this.buf[name];
    if (!this.ctx || !b) return;
    const src = this.ctx.createBufferSource(), g = this.ctx.createGain(), p = this.ctx.createStereoPanner(), send = this.ctx.createGain();
    src.buffer = b; src.playbackRate.value = rate; g.gain.value = gain; p.pan.value = Math.max(-1, Math.min(1, pan));
    send.gain.value = wet * (0.35 + 1.3 * this.room);   // the reverb follows the camera: a close-up is dry, the wide shot rings
    src.connect(g).connect(p).connect(air ? this.air : this.master); p.connect(send).connect(this.hall);
    src.start(this.ctx.currentTime + delay);
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

  // ---- the cues (ASlapHUD's): what the match does, the room hears --------------------------------------------------
  // the strike: a slap take (pitched by speed), and the striker's grunt a beat before it lands, in his own voice
  strike(speed, x, striker) {
    const k = Math.max(0.25, Math.min(1.5, speed / 9));
    this.play(["Slap", "Slap1", "Slap2", "Slap3"][Math.floor(Math.random() * 4)], { gain: k, rate: 0.94 + 0.12 * Math.random(), pan: Sound.pan(x), wet: 0.18 });
    const v = striker && this.voice[striker];
    if (v && speed > 4) this.play(`Grunt_${v}`, { gain: 0.25 + 0.25 * Math.min(1, speed / 10), rate: 0.97 + 0.06 * Math.random(),
      pan: striker === "red" ? -0.35 : 0.35, wet: 0.12, delay: 0.01 });
    this.duckUntil = (this.ctx?.currentTime || 0) + 0.2;
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
  knockdown(x) {
    this.play("Thud", { gain: 1.2, rate: 0.9, pan: Sound.pan(x), wet: 0.3 });
    this.play("CrowdReact", { gain: 0.5, rate: 1.25, wet: 0, air: false });
  }
  bell(final = false) {
    this.play("Bell", { gain: 0.8, wet: 0.35 });
    if (final) this.play("CrowdReact", { gain: 1.4, rate: 0.9, wet: 0, air: false });
  }
  chime() { this.play("PAChime", { gain: 0.55, wet: 0.2, air: false }); }   // the PA before the bout (it carries its own hall)
  // a footstep: the padded mat inside MAT_RADIUS (m from the centre), the hard deck outside it (the walk-in)
  step(x, gain, r = 0) {
    const deck = r > MAT_RADIUS && this.buf.StepDeck;
    this.play(deck ? "StepDeck" : "Step", { gain: gain * (deck ? 0.8 : 1), rate: 0.9 + 0.2 * Math.random(), pan: Sound.pan(x), wet: deck ? 0.22 : 0.15 });
  }
  skid(x, speed) {
    const snd = speed > 0.9 ? (Math.random() < 0.5 ? "Squeak0" : "Squeak1") : "Scrape";
    this.play(snd, { gain: Math.max(0.3, Math.min(1.2, speed / 1.5)), rate: 0.9 + 0.2 * Math.random(), pan: Sound.pan(x), wet: 0.15 });
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
