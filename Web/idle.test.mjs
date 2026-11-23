// node --test Web/idle.test.mjs (tests/test_web_idle.py runs it): the demo-mode idle clock ignores long frames.
import { test } from "node:test";
import assert from "node:assert/strict";
import { IdleClock, IDLE_S, MAX_IDLE_FRAME_S } from "./idle.js";

const run = (clock, frameS, seconds) => { for (let t = 0; t < seconds - 1e-9; t += frameS) clock.tick(frameS); };

test("defaults: 30 s idle, frames over 0.25 s ignored", () => {
  assert.equal(IDLE_S, 30);
  assert.equal(MAX_IDLE_FRAME_S, 0.25);
});

test("30 s of 60 fps frames starts demo mode, 29 s does not", () => {
  const c = new IdleClock();
  run(c, 1 / 60, 29);
  assert.equal(c.due, false);
  run(c, 1 / 60, 1.05);
  assert.equal(c.due, true);
});

test("one 40 s frame adds no idle time", () => {
  const c = new IdleClock();
  assert.equal(c.tick(40), 0);
  assert.equal(c.idle, 0);
  assert.equal(c.due, false);
});

test("a 0.26 s frame is ignored, a 0.25 s frame counts", () => {
  const c = new IdleClock();
  assert.equal(c.tick(0.26), 0);
  assert.equal(c.tick(0.25), 0.25);
  assert.equal(c.idle, 0.25);
});

test("long frames between short ones never reach the threshold early", () => {
  const c = new IdleClock();
  for (let i = 0; i < 100; i++) { c.tick(10); c.tick(0.1); }   // 1000 s of wall time, 10 s of normal frames
  assert.ok(Math.abs(c.idle - 10) < 1e-9);
  assert.equal(c.due, false);
});

test("input resets the clock; zero and negative frames count nothing", () => {
  const c = new IdleClock();
  run(c, 0.1, 31);
  assert.equal(c.due, true);
  c.reset();
  assert.equal(c.idle, 0);
  assert.equal(c.tick(0), 0);
  assert.equal(c.tick(-1), 0);
});
