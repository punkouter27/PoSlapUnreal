// The broadcast sound in the browser: the same synthesised sounds Unreal plays (Scripts/broadcast_audio.py writes both),
// cued the way ASlapHUD cues them. Each one-shot is panned by where it happened, sent to a generated hall (Unreal's
// RV_Arena: 1.8 s, the treble dying first) and summed through a limiter. Browsers only start audio after a click, so
// the context opens on the first START / gesture.
const NAMES = ["Slap", "Slap1", "Slap2", "Slap3", "Thud", "Squeak0", "Squeak1", "Scrape", "Step", "Breath",
  "CrowdReact", "CrowdNear", "CrowdLoop", "Bell"];

function hallIR(ctx, seconds = 1.8, hfRatio = 0.7) {
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
    this.ctx = null; this.buf = {}; this.loops = {};
    this.muted = localStorage.getItem("poslap.sound") === "off";
    this.duckUntil = 0; this.tension = 0;
  }
  async unlock() {
    if (this.ctx) { if (this.ctx.state === "suspended") await this.ctx.resume(); return; }
    const ctx = this.ctx = new AudioContext();
    this.master = ctx.createGain(); this.master.gain.value = this.muted ? 0 : 0.9;
    const limit = ctx.createDynamicsCompressor();
    Object.assign(limit, {});
    limit.threshold.value = -10; limit.knee.value = 6; limit.ratio.value = 12; limit.attack.value = 0.002; limit.release.value = 0.2;
    this.master.connect(limit).connect(ctx.destination);
    this.hall = ctx.createConvolver(); this.hall.buffer = hallIR(ctx);
    this.hall.connect(this.master);
    await Promise.all(NAMES.map(async n => {
      try { this.buf[n] = await ctx.decodeAudioData(await (await fetch(`./assets/audio/${n}.wav`)).arrayBuffer()); }
      catch (err) { console.warn(`sound ${n}:`, err); }
    }));
    this.loops.crowd = this.loop("CrowdLoop", 0.35);
    this.loops.breath = this.loop("Breath", 0);
  }
  setMuted(m) {
    this.muted = m; localStorage.setItem("poslap.sound", m ? "off" : "on");
    if (this.ctx) this.master.gain.setTargetAtTime(m ? 0 : 0.9, this.ctx.currentTime, 0.05);
  }
  loop(name, gain) {
    if (!this.buf[name]) return null;
    const src = this.ctx.createBufferSource(), g = this.ctx.createGain();
    src.buffer = this.buf[name]; src.loop = true; g.gain.value = gain;
    src.connect(g).connect(this.master); src.start();
    return { src, g };
  }
  // a one-shot: gain, playback rate (pitch), pan (-1 left .. 1 right), wet (the hall send)
  play(name, { gain = 1, rate = 1, pan = 0, wet = 0.25, delay = 0 } = {}) {
    const b = this.buf[name];
    if (!this.ctx || !b) return;
    const src = this.ctx.createBufferSource(), g = this.ctx.createGain(), p = this.ctx.createStereoPanner(), send = this.ctx.createGain();
    src.buffer = b; src.playbackRate.value = rate; g.gain.value = gain; p.pan.value = Math.max(-1, Math.min(1, pan)); send.gain.value = wet;
    src.connect(g).connect(p).connect(this.master); p.connect(send).connect(this.hall);
    src.start(this.ctx.currentTime + delay);
  }
  // MuJoCo x (red at -X, on the left from the -Y camera) to a stereo position
  static pan(x) { return Math.max(-0.7, Math.min(0.7, x * 0.6)); }

  // ---- the cues (ASlapHUD's): what the match does, the room hears --------------------------------------------------
  strike(speed, x) {
    const k = Math.max(0.25, Math.min(1.5, speed / 9));
    this.play(["Slap", "Slap1", "Slap2", "Slap3"][Math.floor(Math.random() * 4)], { gain: k, rate: 0.94 + 0.12 * Math.random(), pan: Sound.pan(x), wet: 0.18 });
    this.duckUntil = (this.ctx?.currentTime || 0) + 0.2;
  }
  reaction(staggered, landed, k) {   // the crowd after a turn: a roar for a stagger, a groan for a slap that moved nobody
    const roar = 0.3 + 0.5 * k;
    if (staggered) this.play("CrowdReact", { gain: roar, wet: 0 });
    else if (landed) this.play("CrowdNear", { gain: roar * 0.9, wet: 0 });
  }
  knockdown(x) {
    this.play("Thud", { gain: 1.2, rate: 0.9, pan: Sound.pan(x), wet: 0.3 });
    this.play("CrowdReact", { gain: 0.5, rate: 1.25, wet: 0 });
  }
  bell(final = false) {
    this.play("Bell", { gain: 0.8, wet: 0.35 });
    if (final) this.play("CrowdReact", { gain: 1.4, rate: 0.9, wet: 0 });
  }
  step(x, gain) { this.play("Step", { gain, rate: 0.9 + 0.2 * Math.random(), pan: Sound.pan(x), wet: 0.15 }); }
  skid(x, speed) {
    const snd = speed > 0.9 ? (Math.random() < 0.5 ? "Squeak0" : "Squeak1") : "Scrape";
    this.play(snd, { gain: Math.max(0.3, Math.min(1.2, speed / 1.5)), rate: 0.9 + 0.2 * Math.random(), pan: Sound.pan(x), wet: 0.15 });
  }
  // every frame: the murmur swells with the tension and ducks under a slap; the breathing follows the worse stamina
  tick(tension, stamina) {
    if (!this.ctx) return;
    const t = this.ctx.currentTime;
    if (this.loops.crowd) this.loops.crowd.g.gain.setTargetAtTime(0.35 * (1 + 1.6 * tension) * (t < this.duckUntil ? 0.3 : 1), t, 0.08);
    if (this.loops.breath) {
      const need = Math.max(0, Math.min(1, (1 - stamina) / 0.3));   // stamina floors at 0.7
      this.loops.breath.g.gain.setTargetAtTime(0.01 + 0.6 * need * need, t, 0.3);
      this.loops.breath.src.playbackRate.setTargetAtTime(1 + 0.45 * need, t, 0.3);
    }
  }
}
