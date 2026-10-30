// Spectator API check: node Web/api_check.mjs (from the repo root). Starts Web/live.py on a spare port, plays a fake
// Unreal match and reads the API: the state (/api/match), the event stream (/api/events with ?since and ?limit), the
// SSE stream (/api/events/stream), the health, the schema and the index. The API's reads need their own key (the
// publisher's key does not open them), the CORS headers are there and the rate limiter answers 429. Exits 1 on a miss.
import { spawn } from "node:child_process";
import { connect } from "./ws_client.mjs";

const wait = ms => new Promise(r => setTimeout(r, ms));
const open = u => new Promise((ok, bad) => { const w = connect(u); w.onopen = () => ok(w); w.onerror = bad; });
const fails = [];
const check = (name, cond, extra = "") => {
  if (!cond) fails.push(name);
  console.log(`${cond ? "ok  " : "FAIL"} ${name}${extra ? ` - ${extra}` : ""}`);
};

const startRelay = (port, args) => spawn("python", ["Web/live.py", "--port", String(port), ...args], { stdio: ["ignore", "inherit", "inherit"] });
// the relay needs a moment to bind (a busy machine can take seconds): wait for it instead of guessing
async function waitForRelay(port) {
  for (let i = 0; i < 80; i++) {
    try { await fetch(`http://127.0.0.1:${port}/`); return true; } catch { await wait(250); }
  }
  return false;
}

// ---- the main relay: a publisher key and a separate API key -----------------------------------------------
const port = 8801, base = `http://127.0.0.1:${port}`;
const key = "check-key", apiKey = "api-secret";
const relay = startRelay(port, ["--key", key, "--api-key", apiKey]);
const withKey = (p = "") => `${base}${p}${p.includes("?") ? "&" : "?"}key=${apiKey}`;

try {
  await waitForRelay(port);
  const ue = await open(`ws://127.0.0.1:${port}/ue?key=${key}`);
  const ev = e => ue.send(JSON.stringify({ t: "ev", e }));
  ue.send(JSON.stringify({ t: "pose", c: ["grandpa", "kim"], m: 3, r: 1, s: "red", w: false, slow: 1, q: new Array(74).fill(0) }));
  ev({ type: "match_start", match: 3, first: "red", seed: 7 });
  ev({ type: "turn_start", round: 1, striker: "red", defender: "blue" });
  ev({ type: "strike", t: 5.2, striker: "red", part: "head", palm: true, speed: 11.5, pos: [0.1, 0, 1.4] });
  ev({ type: "turn_end", round: 1, striker: "red", touch: 5.2, part: "head", palm: true, speed: 11.5, head_peak: 3.4, stagger: true, fouls: [], ko: false, end: "recovered" });
  ev({ type: "round", round: 2, cards: [[10, 9], [10, 10], [8, 10]] });
  ev({ type: "turn_start", round: 2, striker: "blue", defender: "red" });
  ev({ type: "knockdown", t: 3.1, athlete: "red", part: "hand_l" });
  ev({ type: "turn_end", round: 2, striker: "blue", touch: 3.1, part: "hand_l", palm: false, speed: 9.1, head_peak: 2.2, stagger: false, fouls: [["red", "knocked down"]], ko: false, end: "knockdown" });
  ev({ type: "result", how: "majority decision", winner: "blue", cards: [[10, 9], [10, 10], [8, 10]] });
  await wait(400);

  // /api/match: the state the scoreboards read
  const mRes = await fetch(withKey("/api/match"));
  const m = await mRes.json();
  check("match: 200", mRes.status === 200, String(mRes.status));
  check("match: cors header", mRes.headers.get("access-control-allow-origin") === "*");
  check("match: stage is over", m.stage === "over", String(m.stage));
  check("match: the match number", m.match === 3, String(m.match));
  check("match: the round", m.round === 2, String(m.round));
  check("match: the winner", m.result?.winner === "blue", JSON.stringify(m.result));
  check("match: the judges' cards", Array.isArray(m.judges) && m.judges.length === 3 && m.judges[0].red === 10 && m.judges[2].blue === 10, JSON.stringify(m.judges));
  check("match: the judges' totals", m.cards.red === 28 && m.cards.blue === 29, JSON.stringify(m.cards));
  check("match: the athletes' names and tints", m.athletes[0].name === "grandpa" && m.athletes[0].tint === "red" && m.athletes[1].name === "kim" && m.athletes[1].tint === "blue", JSON.stringify(m.athletes.map(a => a.name)));
  check("match: the display names come from the cards", m.athletes[0].display === "GRANDPA", String(m.athletes[0].display));
  check("match: the health is derived (red down after the knockdown)", m.athletes[0].health === 0.5 && m.athletes[1].health === 1, JSON.stringify(m.athletes.map(a => a.health)));
  check("match: the knockdowns are counted", m.athletes[0].knockdowns === 1 && m.athletes[1].knockdowns === 0);
  check("match: the flags", m.knockdown === true && m.ko === false);
  check("match: the danger meters", m.danger.red === 1 && Math.abs(m.danger.blue - 3.4 / 12) < 0.001, JSON.stringify(m.danger));
  check("match: the last strike", m.last_strike?.striker === "red" && m.last_strike.speed === 11.5, JSON.stringify(m.last_strike));
  check("match: the clock", m.clock.turn_clock === 30 && m.clock.phase === "hold", JSON.stringify(m.clock));

  // /api/events: the ring buffer, the sequence numbers and the caps
  const eRes = await fetch(withKey("/api/events"));
  const e = await eRes.json();
  check("events: an array of the match events", Array.isArray(e) && e.length === 9, `${e.length}`);
  check("events: the sequence numbers count up", e[0].seq === 1 && e[8].seq === 9 && e.every((x, i) => x.seq === i + 1));
  check("events: the types are the match's", e[0].type === "match_start" && e[2].type === "strike" && e[8].type === "result", e.map(x => x.type).join(","));
  check("events: the event fields are kept", e[2].speed === 11.5 && e[2].palm === true && e[6].athlete === "red", JSON.stringify(e[2]));
  check("events: the last sequence number is in the header", eRes.headers.get("x-slap-last-seq") === "9", String(eRes.headers.get("x-slap-last-seq")));
  const since = await (await fetch(withKey("/api/events?since=6"))).json();
  check("events: ?since skips what the client has", since.length === 3 && since[0].seq === 7, JSON.stringify(since.map(x => x.seq)));
  const limited = await (await fetch(withKey("/api/events?limit=2"))).json();
  check("events: ?limit caps the answer", limited.length === 2, `${limited.length}`);
  const huge = await (await fetch(withKey("/api/events?limit=99999"))).json();
  check("events: ?limit is clamped, not obeyed", huge.length === 9, `${huge.length}`);
  const old = await fetch(withKey("/api/events?since=99999"));
  check("events: a ?since beyond the end is empty", (await old.json()).length === 0);

  // /api/events/stream: the same events as Server-Sent Events
  const ac = new AbortController();
  const sse = await fetch(withKey("/api/events/stream?since=0"), { signal: ac.signal });
  check("sse: the content type", (sse.headers.get("content-type") || "").startsWith("text/event-stream"), String(sse.headers.get("content-type")));
  const reader = sse.body.getReader();
  const dec = new TextDecoder();
  let text = "";
  const pump = async () => { for (;;) { const { value, done } = await reader.read(); if (done) break; text += dec.decode(value, { stream: true }); } };
  const pumping = pump();
  await wait(600);
  check("sse: the backlog arrives", text.includes("id: 1") && text.includes("event: match_start") && text.includes("id: 9"), text.slice(0, 120).replace(/\n/g, "|"));
  check("sse: the data line is the event", /data: \{"seq":3,.*"type":"strike"/.test(text), (text.match(/data: \{[^\n]*/) || [""])[0].slice(0, 90));
  ev({ type: "turn_start", round: 3, striker: "red", defender: "blue" });
  await wait(400);
  check("sse: a new event arrives live", text.includes("id: 10") && text.includes("event: turn_start"), (text.match(/id: \d+/g) || []).join(","));

  // /api/health (read while the SSE client is still connected)
  const h = await (await fetch(withKey("/api/health"))).json();
  check("health: the publisher is connected", h.publisher?.connected === true, JSON.stringify(h.publisher));
  check("health: the last sequence number", h.last_seq === 10, String(h.last_seq));
  check("health: the uptime", h.uptime_s > 0, String(h.uptime_s));
  check("health: the clients are counted", h.clients.sse === 1 && h.clients.total === 1, JSON.stringify(h.clients));
  check("health: the limits are published", h.limits.max_events === 5000 && h.limits.max_limit === 500, JSON.stringify(h.limits));
  ac.abort();
  await pumping.catch(() => { });

  // /api/schema
  const s = await (await fetch(withKey("/api/schema"))).json();
  check("schema: the event types are described", !!s.event_types?.match_start && !!s.event_types?.turn_end && !!s.event_types?.strike, Object.keys(s.event_types || {}).join(","));
  check("schema: the state fields are described", !!s.state_fields?.athletes && !!s.state_fields?.danger && !!s.state_fields?.clock);
  check("schema: the derivation is spelled out", /derived/.test(JSON.stringify(s.derived)) && /health/.test(JSON.stringify(s.derived)));

  // the index
  const idx = await fetch(`${base}/`);
  const idxText = await idx.text();
  check("index: it lists the endpoints", idx.status === 200 && idxText.includes("/api/match") && idxText.includes("/api/events/stream"));

  // the keys: the API's reads need the API key, not the publisher's
  check("auth: no key is refused", (await fetch(`${base}/api/match`)).status === 401);
  check("auth: the publisher's key does not open the API", (await fetch(`${base}/api/match?key=${key}`)).status === 401);
  check("auth: the wrong key is refused", (await fetch(`${base}/api/match?key=nope`)).status === 401);
  check("auth: the header works too", (await fetch(`${base}/api/match`, { headers: { "X-Slap-Key": apiKey } })).status === 200);
  const pre = await fetch(`${base}/api/match`, { method: "OPTIONS" });
  check("auth: the preflight is answered", pre.status === 204 && pre.headers.get("access-control-allow-methods")?.includes("GET"), String(pre.status));
  check("auth: a write is refused", (await fetch(`${base}/api/match`, { method: "POST" })).status === 405);
  ue.close();
} catch (err) {
  fails.push(`threw: ${err.message}`);
  console.log(`FAIL threw: ${err.message}`);
} finally {
  relay.kill();
}

// ---- the API inherits the publisher's key when it has none of its own ---------------------------------------
{
  const port2 = 8802, base2 = `http://127.0.0.1:${port2}`;
  const relay2 = startRelay(port2, ["--key", key]);
  try {
    await waitForRelay(port2);
    check("inherit: no key is refused", (await fetch(`${base2}/api/health`)).status === 401);
    check("inherit: the publisher's key opens the API", (await fetch(`${base2}/api/health?key=${key}`)).status === 200);
  } catch (err) {
    fails.push(`inherit threw: ${err.message}`);
    console.log(`FAIL inherit threw: ${err.message}`);
  } finally {
    relay2.kill();
  }
}

// ---- the rate limiter --------------------------------------------------------------------------------------
{
  const port3 = 8803, base3 = `http://127.0.0.1:${port3}`;
  const relay3 = startRelay(port3, ["--api-public"]);
  try {
    await waitForRelay(port3);
    let limited = false;
    for (let i = 0; i < 140 && !limited; i++) limited = (await fetch(`${base3}/api/health`)).status === 429;
    check("rate: the limiter answers 429", limited);
  } catch (err) {
    fails.push(`rate threw: ${err.message}`);
    console.log(`FAIL rate threw: ${err.message}`);
  } finally {
    relay3.kill();
  }
}

console.log(fails.length ? `FAIL: ${fails.join("; ")}` : "PASS");
process.exit(fails.length ? 1 : 0);
