// The demo-mode (attract) idle clock, kept apart from app.js so Node can test it (Web/idle.test.mjs, run by pytest).
// Only frames of a normal length count as idle time: a frame longer than maxFrameS (shader warm-up, a background tab,
// a debugger pause) adds nothing, so one long stall can never start demo mode on its own.
export const IDLE_S = 30, MAX_IDLE_FRAME_S = 0.25;

export class IdleClock {
  constructor(idleS = IDLE_S, maxFrameS = MAX_IDLE_FRAME_S) {
    this.idleS = idleS; this.maxFrameS = maxFrameS; this.idle = 0;
  }
  // adds one frame (seconds); returns the idle time it counted (0 for a long or non-positive frame)
  tick(frameS) {
    const dt = frameS > 0 && frameS <= this.maxFrameS ? frameS : 0;
    this.idle += dt;
    return dt;
  }
  reset() { this.idle = 0; }   // any input
  get due() { return this.idle >= this.idleS; }
}
