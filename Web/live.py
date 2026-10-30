"""Second-screen relay and spectator API.

Unreal publishes the live match on ws://<host>:8765/ue; every other connection is a viewer and gets each message
as it comes (and the latest pose at once, so a new viewer sees the athletes before the next frame). The same port
also serves a read-only JSON API, so scoreboards, bots, stream overlays and a second screen can follow a match
without speaking the WebSocket protocol.

    python Web/live.py [--host 0.0.0.0] [--port 8765] [--key <secret>] [--api-key <secret>] [--api-public]
    python Web/serve.py --host 0.0.0.0                       (the page, for phones on the same network)

Unreal: MENU > VIEW > Second screen (or -SlapLive=ws://127.0.0.1:8765/ue). Viewers: http://<host>:8080/Web/?live=ws://<host>:8765
API:    GET /api/match (the state), /api/events (?since=&limit=), /api/events/stream (SSE), /api/health,
        /api/schema, /api/poses and / (the index)

Off the local network, point both at an Azure Web PubSub hub instead (client access URLs; Unreal sends to the group
"live", the page joins it) and this relay isn't needed. Stdlib only: RFC 6455 text frames, no extensions.

The publisher role (pushing text into every viewer's page) needs the shared key: --key, or SLAP_LIVE_KEY. The API's
reads are keyed as well (--api-key, defaulting to --key): with no key anywhere the relay serves viewers and an open
API, which is what a laptop on the LAN wants; --api-public says "open reads" on purpose.
"""
import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
import struct
import time
from collections import OrderedDict, deque
from pathlib import Path
from urllib.parse import unquote

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
ROOT = Path(__file__).resolve().parents[1]

# The relay's limits. The ring buffer is the history a client can still ask for; everything older is gone.
MAX_EVENTS = 5000        # the ring buffer: the last 5,000 match events
MAX_POSES = 200          # the last 200 pose frames (74 floats each: more is not worth the memory)
MAX_LIMIT = 500          # ?limit is clamped to this
DEFAULT_LIMIT = 200      # ?limit without a value
RATE_BURST = 120         # API requests per client per RATE_WINDOW seconds
RATE_WINDOW = 10.0
RATE_CLIENTS = 4096      # the token buckets are capped, so cycling addresses cannot grow the dict without bound
HEARTBEAT = 15.0         # the SSE heartbeat (a comment line), seconds
STRIKE_CLOCK = 30.0      # match.py / sim.js: the turn clock
RECOVERY = 5.0           # match.py / sim.js: the recovery window after a touch
DANGER_FULL = 12.0       # m/s of head snap that reads as full KO danger (the event-derived proxy)
SAFE_NAME = re.compile(r"^[A-Za-z0-9_]{1,40}$")   # a card name from the wire may only be a file name

viewers = set()
latest = {"pose": None}
KEY = ""       # the publisher's shared secret (--key, or SLAP_LIVE_KEY); empty = publishers are refused
API_KEY = ""   # the API's read key (--api-key, or --key); empty = the API reads are open


# ---- the state the API serves: derived from the very events the WebSocket forwards -------------------------
def athlete(corner):
    """One corner. health and danger are derived (see /api/schema): the publisher ships no health value."""
    return {"corner": corner, "tint": corner, "name": None, "display": None, "height_m": None, "mass_kg": None,
            "health": 1.0, "knockdowns": 0, "faints": 0, "fainted": False, "ko": False, "danger": 0.0}


def fresh_state():
    return {"match": 0, "stage": "idle", "round": 0, "turn": 0, "striker": None, "defender": None,
            "clock": {"phase": None, "remaining": None, "turn_clock": STRIKE_CLOCK, "recovery_left": None},
            "judges": [], "cards": {"red": 0, "blue": 0},
            "athletes": [athlete("red"), athlete("blue")],
            "ko": False, "knockdown": False, "result": None, "last_strike": None,
            "danger": {"red": 0.0, "blue": 0.0, "source": "events"}, "updated_at": time.time()}


def side_index(name):
    return 0 if name == "red" else 1 if name == "blue" else -1


def other_side(name):
    return "blue" if name == "red" else "red"


_cards = {}


def card_info(name):
    """The character card behind a corner's name (display name, height, mass). The name comes off the wire, so it is
    checked twice: it must be a plain file name, and the resolved path must stay inside the characters directory
    (the same discipline Web/serve.py's guard uses)."""
    if not name or not SAFE_NAME.match(name):
        return {}
    if name not in _cards:
        info = {}
        try:
            folder = (ROOT / "training" / "characters").resolve()
            path = (folder / f"{name}.json").resolve()
            if path.parent == folder and path.is_file():
                info = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            info = {}
        _cards[name] = info
    return _cards[name]


def judge_cards(cards):
    """[[red, blue], ...] (sim.js scoreRound) as a list of per-judge rows."""
    out = []
    for j, c in enumerate(cards or []):
        if isinstance(c, (list, tuple)) and len(c) >= 2:
            out.append({"judge": j + 1, "red": c[0], "blue": c[1]})
    return out


def apply_event(st, ev):
    """Fold one match event into the state the API serves. The event shapes are sim.js's (Web/sim.js emits them
    and Unreal's USlapController publishes the same ones), so the API and the page read one story."""
    kind = ev.get("type") or ""
    st["updated_at"] = time.time()
    if kind == "match_start":
        st["match"] = ev.get("match") or st["match"] + 1
        st["stage"] = "walkin"
        st["round"] = 1
        st["turn"] = 0
        st["ko"] = st["knockdown"] = False
        st["result"] = None
        st["judges"] = []
        st["cards"] = {"red": 0, "blue": 0}
        # the corners keep their names and cards (the pose frame carries them); the match resets their condition
        st["athletes"] = [dict(a, health=1.0, knockdowns=0, faints=0, fainted=False, ko=False, danger=0.0)
                          for a in st["athletes"]]
        st["danger"] = {"red": 0.0, "blue": 0.0, "source": "events"}
    elif kind == "turn_start":
        st["stage"] = "turn"
        st["round"] = ev.get("round", st["round"])
        st["turn"] = st.get("turn", 0) + 1
        st["striker"] = ev.get("striker")
        st["defender"] = ev.get("defender")
        st["clock"] = {"phase": "strike", "remaining": STRIKE_CLOCK, "turn_clock": STRIKE_CLOCK, "recovery_left": None}
        st["ko"] = st["knockdown"] = False
    elif kind == "strike":
        st["last_strike"] = {"striker": ev.get("striker"), "part": ev.get("part"), "palm": ev.get("palm"),
                             "speed": ev.get("speed"), "t": ev.get("t")}
        st["clock"] = {"phase": "recovery", "remaining": RECOVERY, "turn_clock": STRIKE_CLOCK, "recovery_left": RECOVERY}
    elif kind == "faint":
        i = side_index(ev.get("athlete"))
        if i >= 0:
            a = st["athletes"][i]
            a["faints"] += 1
            a["fainted"] = True
            a["health"] = round(max(0.0, a["health"] - 0.25), 3)
    elif kind == "knockdown":
        i = side_index(ev.get("athlete"))
        st["knockdown"] = True
        if i >= 0:
            a = st["athletes"][i]
            a["knockdowns"] += 1
            a["health"] = round(max(0.0, a["health"] - 0.5), 3)
            a["danger"] = 1.0
            st["danger"][a["corner"]] = 1.0
    elif kind == "turn_end":
        st["ko"] = bool(ev.get("ko"))
        st["stage"] = "hold"
        st["clock"] = {"phase": "hold", "remaining": None, "turn_clock": STRIKE_CLOCK, "recovery_left": None}
        striker = ev.get("striker")
        if striker in ("red", "blue"):
            # the head snap raises the danger, but a knockdown (1.0) is not talked down by the next number
            st["danger"][other_side(striker)] = max(st["danger"][other_side(striker)],
                                                   round(min(1.0, max(0.0, (ev.get("head_peak") or 0.0) / DANGER_FULL)), 3))
        if ev.get("ko") and striker in ("red", "blue"):
            i = side_index(other_side(striker))
            if i >= 0:
                st["athletes"][i]["health"] = 0.0
                st["athletes"][i]["ko"] = True
    elif kind == "round":
        st["round"] = ev.get("round", st["round"])
        st["judges"] = judge_cards(ev.get("cards"))
        st["cards"] = {"red": sum(c["red"] for c in st["judges"]), "blue": sum(c["blue"] for c in st["judges"])}
    elif kind == "result":
        st["stage"] = "over"
        st["result"] = {"how": ev.get("how"), "winner": ev.get("winner"), "cards": judge_cards(ev.get("cards"))}
        st["judges"] = judge_cards(ev.get("cards"))
        st["cards"] = {"red": sum(c["red"] for c in st["judges"]), "blue": sum(c["blue"] for c in st["judges"])}
    return st


# The relay's live state: the ring buffer of events, the state built from them and the SSE listeners.
events = deque(maxlen=MAX_EVENTS)     # the match events, oldest first
poses = deque(maxlen=MAX_POSES)      # the pose frames (not part of the event stream: 74 floats each)
seq = 0
state = fresh_state()
sse_clients = set()
rate = OrderedDict()     # client address -> [tokens, last refill]
started = time.time()
publisher_at = None    # when the publisher (Unreal) connected, None while it is away


def publish_event(ev):
    """One event into the ring buffer, the state and every SSE listener. Returns the stored record."""
    global seq
    seq += 1
    rec = {"seq": seq, "ts": time.time(), "type": ev.get("type") or "unknown"}
    for k, v in ev.items():
        if k != "type":
            rec[k] = v
    events.append(rec)
    apply_event(state, ev)
    for q in list(sse_clients):
        try:
            q.put_nowait(rec)
        except asyncio.QueueFull:
            pass          # a client that cannot keep up misses events; it catches up with ?since=
    return rec


def events_after(since):
    """The buffered events with a sequence number above since (the whole buffer if since is 0)."""
    return [e for e in events if e["seq"] > since]


# ---- the publisher's role -----------------------------------------------------------------------------
def publisher_ok(path, origin):
    """The publisher's role is proved by the shared key in the path's query. With no key configured the relay serves
    viewers only: a page on the network cannot push text into every other viewer's page."""
    if not KEY:
        return False
    query = path.split("?", 1)[1] if "?" in path else ""
    for pair in query.split("&"):
        name, _, value = pair.partition("=")
        if name == "key" and hmac.compare_digest(value.encode(), KEY.encode()):
            return True
    return False


def api_ok(params, headers):
    """The API's reads: keyed when a key is configured (the publisher's key by default). The key comes in the
    query (?key=...) or in the X-Slap-Key header."""
    if not API_KEY:
        return True
    got = headers.get("x-slap-key") or params.get("key") or ""
    return hmac.compare_digest(str(got).encode(), API_KEY.encode())


def rate_ok(who):
    """A token bucket per client address: a scoreboard may poll, a script may not hammer."""
    now = time.time()
    tokens, last = rate.get(who, (RATE_BURST, now))
    tokens = min(RATE_BURST, tokens + (now - last) * (RATE_BURST / RATE_WINDOW))
    if tokens < 1:
        rate[who] = (tokens, now)
        rate.move_to_end(who)
        return False
    rate[who] = (tokens - 1, now)
    rate.move_to_end(who)
    if len(rate) > RATE_CLIENTS:
        evict_rate(now)
    return True


def evict_rate(now):
    """The buckets are capped: a client cycling addresses must not grow the dict without bound. The spent and the
    idle ones go first, then the least recently used."""
    for k in [k for k, (t, l) in rate.items() if t >= RATE_BURST or now - l > 4 * RATE_WINDOW]:
        del rate[k]
    while len(rate) > RATE_CLIENTS:
        rate.popitem(last=False)


# ---- the WebSocket --------------------------------------------------------------------------------------
async def handshake(writer, hdr):
    accept = base64.b64encode(hashlib.sha1((hdr["sec-websocket-key"] + GUID).encode()).digest()).decode()
    writer.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                  f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())
    await writer.drain()


async def message(reader):
    """One whole message: (opcode, payload), fragments joined, client masks removed."""
    op, data = None, b""
    while True:
        b0, b1 = await reader.readexactly(2)
        n = b1 & 0x7F
        if n == 126:
            n = struct.unpack(">H", await reader.readexactly(2))[0]
        elif n == 127:
            n = struct.unpack(">Q", await reader.readexactly(8))[0]
        mask = await reader.readexactly(4) if b1 & 0x80 else bytes(4)
        part = bytes(c ^ mask[i % 4] for i, c in enumerate(await reader.readexactly(n)))
        if b0 & 0x0F >= 8:   # a control frame may arrive between fragments
            return b0 & 0x0F, part
        op = op if op is not None else b0 & 0x0F
        data += part
        if b0 & 0x80:
            return op, data


def frame(data, op=1):
    n = len(data)
    size = bytes([n]) if n < 126 else bytes([126]) + struct.pack(">H", n) if n < 65536 else bytes([127]) + struct.pack(">Q", n)
    return bytes([0x80 | op]) + size + data


async def websocket_session(reader, writer, target, hdr):
    global publisher_at
    path = target
    origin = hdr.get("origin", "")
    publisher = path.split("?")[0].rstrip("/").endswith("/ue")
    # The publisher role is what lets a client push text into every viewer's page, so it needs the shared key
    # (--key, or SLAP_LIVE_KEY). Without it only viewers are served: a page on the LAN cannot impersonate Unreal.
    if publisher and not publisher_ok(path, origin):
        print(f"publisher refused (no key): {path}", flush=True)
        writer.close()
        return
    print(f"{'unreal' if publisher else 'viewer'} connected ({len(viewers) + (not publisher)} viewers)", flush=True)
    if publisher:
        publisher_at = time.time()
    else:
        viewers.add(writer)
        if latest["pose"]:
            writer.write(frame(latest["pose"]))
    try:
        while True:
            op, data = await message(reader)
            if op == 8:   # close
                break
            if op == 9:   # ping
                writer.write(frame(data, 10))
            elif publisher and op == 1:
                await publisher_message(data)
                for v in list(viewers):
                    if v.transport.get_write_buffer_size() > 1 << 20:   # a viewer this far behind is gone
                        viewers.discard(v)
                        v.close()
                    else:
                        v.write(frame(data))
    except (asyncio.IncompleteReadError, ConnectionError):
        pass
    finally:
        viewers.discard(writer)
        writer.close()
        if publisher:
            publisher_at = None
        print(f"{'unreal' if publisher else 'viewer'} left ({len(viewers)} viewers)", flush=True)


async def publisher_message(data):
    """One message from Unreal. The wire format is unchanged ({"t":"pose",...} and {"t":"ev","e":{...}}): the relay
    forwards every message to the viewers exactly as it arrived and, on top of that, folds the events into the
    state and the ring buffer the API serves."""
    try:
        msg = json.loads(data.decode("utf-8"))
    except Exception:
        return
    if not isinstance(msg, dict):
        return
    kind = msg.get("t")
    if kind == "pose":
        latest["pose"] = data
        poses.append({"ts": time.time(), "c": msg.get("c"), "m": msg.get("m"), "r": msg.get("r"),
                      "s": msg.get("s"), "w": msg.get("w"), "slow": msg.get("slow")})
        c = msg.get("c") or []
        for i, name in enumerate(c[:2]):
            if name and i < 2:
                info = card_info(name)
                state["athletes"][i]["name"] = name
                state["athletes"][i]["display"] = info.get("display") or name
                state["athletes"][i]["height_m"] = info.get("height_m")
                state["athletes"][i]["mass_kg"] = info.get("mass_kg")
        if msg.get("m"):
            state["match"] = msg["m"]
        if msg.get("r"):
            state["round"] = msg["r"]
        if msg.get("s") in ("red", "blue"):
            state["striker"] = msg["s"]
        if msg.get("w"):
            state["stage"] = "walkin"
    elif kind == "ev" and isinstance(msg.get("e"), dict):
        publish_event(msg["e"])


# ---- the HTTP API ---------------------------------------------------------------------------------------
CORS = ("Access-Control-Allow-Origin: *", "Access-Control-Allow-Methods: GET, HEAD, OPTIONS",
        "Access-Control-Allow-Headers: X-Slap-Key, Content-Type", "Access-Control-Max-Age: 600")


def http_head(status, ctype, length, extra=(), no_len=False):
    lines = [f"HTTP/1.1 {status}", f"Content-Type: {ctype}"]
    if not no_len:
        lines.append(f"Content-Length: {length}")
    lines += ["Cache-Control: no-store", "Connection: close" if not no_len else "Connection: keep-alive"]
    lines += list(extra)
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin1")


async def send(writer, status, body=b"", ctype="application/json; charset=utf-8", extra=()):
    if isinstance(body, str):
        body = body.encode("utf-8")
    writer.write(http_head(status, ctype, len(body), extra))
    writer.write(body)
    await writer.drain()


async def send_json(writer, obj, status="200 OK", extra=(), head_only=False):
    body = json.dumps(obj, separators=(",", ":"), default=str).encode("utf-8")
    writer.write(http_head(status, "application/json; charset=utf-8", len(body), extra))
    if not head_only:
        writer.write(body)
    await writer.drain()


def parse_params(query):
    out = {}
    for pair in query.split("&"):
        if not pair:
            continue
        name, _, value = pair.partition("=")
        name = unquote(name)
        if name not in out:
            out[name] = unquote(value.replace("+", " "))
    return out


def int_param(params, name, default, lo, hi):
    raw = params.get(name)
    if raw is None or raw == "":
        return default
    try:
        v = int(float(raw))
    except ValueError:
        return default
    return max(lo, min(hi, v))


INDEX = """PoSlap spectator API
=====================
The relay that carries a live Unreal match to the second screen also serves this read-only JSON API.
Every event carries a sequence number, so a consumer can poll incrementally or resume a stream.

  GET /api/match          the match state: athletes (name, tint, health), round, turn, clock, the judges'
                          cards, the KO and knockdown flags, the danger meters, the stage of the match
  GET /api/events         the match events as a JSON array (?since=<seq> to poll, ?limit=<n>, max 500)
  GET /api/events/stream  the same events as Server-Sent Events (?since=<seq>, or the Last-Event-ID header)
  GET /api/health         the relay's status: the publisher, the last sequence number, the uptime, the clients
  GET /api/schema         the event types and the state fields, machine-readable
  GET /api/poses          the last pose frames (the WebSocket's high-rate channel)
  GET /                    this index

CORS: the reads answer with Access-Control-Allow-Origin: * (they are read-only, carry no credentials and show
exactly what a WebSocket viewer already sees). The site's own Content-Security-Policy is untouched.

Keys: the API's reads need the shared key when one is configured (--api-key, or --key): pass it as ?key=... or in
the X-Slap-Key header. The publisher role (ws://<host>:<port>/ue) always needs it.
"""


def schema():
    """The machine-readable description of the API: a consumer can code against this."""
    return {
        "name": "poslap-spectator-api",
        "version": 1,
        "generated": time.strftime("%Y-%m-%d"),
        "endpoints": {
            "GET /api/match": "the match state",
            "GET /api/events": "the event array (?since=<seq>, ?limit=<n>)",
            "GET /api/events/stream": "the same events as Server-Sent Events",
            "GET /api/health": "the relay's status",
            "GET /api/schema": "this document",
            "GET /api/poses": "the last pose frames",
            "GET /": "a human-readable index",
        },
        "event_types": {
            "match_start": {"fields": {"match": "int", "first": "red|blue", "seed": "int"}, "means": "a new match starts"},
            "turn_start": {"fields": {"round": "int", "striker": "red|blue", "defender": "red|blue"}, "means": "the turn clock starts"},
            "strike": {"fields": {"t": "float (s into the turn)", "striker": "red|blue", "part": "string",
                                  "palm": "bool", "speed": "float (m/s)", "pos": "[x,y,z] or missing"},
                       "means": "the palm (or any part) landed on the defender"},
            "faint": {"fields": {"t": "float", "athlete": "red|blue"}, "means": "the athlete passes out"},
            "knockdown": {"fields": {"t": "float", "athlete": "red|blue", "part": "string"},
                          "means": "a non-sole part touched the floor or the podium"},
            "foul": {"fields": {"t": "float", "athlete": "red|blue", "why": "string"}, "means": "a rule was broken"},
            "turn_end": {"fields": {"round": "int", "striker": "red|blue", "touch": "float or null",
                                    "part": "string or null", "palm": "bool", "speed": "float", "head_peak": "float (m/s)",
                                    "stagger": "bool", "fouls": "[[athlete, why], ...]", "ko": "bool", "end": "string"},
                         "means": "the turn is over: the resolution of a spectator's pick"},
            "round": {"fields": {"round": "int", "cards": "[[red, blue], ...] one per judge"}, "means": "the judges scored the round"},
            "result": {"fields": {"how": "string", "winner": "red|blue|null", "cards": "[[red, blue], ...]"},
                       "means": "the match is over"},
            "blowup": {"fields": {}, "means": "the simulation blew up: the match is void"},
        },
        "state_fields": {
            "match": "int, the match number",
            "stage": "idle | walkin | turn | hold | over",
            "round": "int, 1..3",
            "turn": "int, the turns taken in this match",
            "striker": "red | blue | null",
            "defender": "red | blue | null",
            "clock": {"phase": "strike | recovery | hold | null", "remaining": "float (s) or null",
                      "turn_clock": "float (30 s, match.py's turn clock)", "recovery_left": "float (s) or null"},
            "athletes": "one entry per corner: corner, tint, name (the character card), display, height_m, mass_kg, "
                        "health, knockdowns, faints, fainted, ko, danger",
            "judges": "[{judge, red, blue}] the judges' cards",
            "cards": "{red, blue} the judges' totals",
            "ko": "bool, the last turn ended in a knockout",
            "knockdown": "bool, the last turn ended in a knockdown",
            "result": "{how, winner, cards} or null",
            "last_strike": "{striker, part, palm, speed, t} or null",
            "danger": "{red, blue, source} the KO danger, 0..1",
        },
        "derived": {
            "health": "a spectator estimate, not a simulated value: 1.0 at the start of a match, -0.5 per knockdown, "
                      "-0.25 per faint, 0 on a knockout, floored at 0",
            "danger": "an event-derived proxy (the publisher ships no capture point): the head snap of the last strike "
                      f"divided by {DANGER_FULL} m/s, 1.0 on a knockdown; it holds the last resolved turn's value "
                      "until the next one. The browser HUD shows its own local capture-point estimate when it runs "
                      "the match itself",
            "clock": "the turn clock (30 s) and the recovery window (5 s) are match.py's and sim.js's constants",
        },
        "limits": {
            "events_retained": MAX_EVENTS,
            "max_limit": MAX_LIMIT,
            "default_limit": DEFAULT_LIMIT,
            "poses_retained": MAX_POSES,
            "rate": f"{RATE_BURST} requests per {RATE_WINDOW:.0f} s per client (429 after that)",
        },
        "cors": {"access_control_allow_origin": "*", "methods": ["GET", "HEAD", "OPTIONS"],
                 "note": "the reads are public data (a WebSocket viewer sees the same); the site's CSP is untouched"},
        "keys": {"publisher": "the shared key (--key) is required for ws://<host>:<port>/ue",
                 "api": "the reads need the key when one is configured (--api-key, or --key): ?key=... or X-Slap-Key"},
    }


async def http_session(reader, writer, method, target, hdr):
    """The API's answers. No request path is ever turned into a file: the routes below are an exact list, so a
    traversal has nothing to escape into. The only file the relay reads is a character card, and card_info() checks
    the resolved path (the same rule Web/serve.py's guard uses)."""
    path, _, query = target.partition("?")
    path = path.rstrip("/") or "/"
    params = parse_params(query)
    head_only = method == "HEAD"
    if method == "OPTIONS":
        writer.write(http_head("204 No Content", "text/plain", 0, CORS))
        await writer.drain()
        writer.close()
        return
    if method not in ("GET", "HEAD"):
        await send(writer, "405 Method Not Allowed", b"read-only API\n", "text/plain; charset=utf-8", CORS)
        writer.close()
        return
    if not rate_ok(writer.get_extra_info("peername")[0] if writer.get_extra_info("peername") else "-"):
        await send(writer, "429 Too Many Requests", b"too many requests\n", "text/plain; charset=utf-8",
                   CORS + ("Retry-After: 10",))
        writer.close()
        return
    if path in ("/", "/api"):
        await send(writer, "200 OK", INDEX, "text/plain; charset=utf-8", CORS)
        writer.close()
        return
    if not api_ok(params, hdr):
        await send(writer, "401 Unauthorized", b"the API needs the shared key (?key=... or X-Slap-Key)\n",
                   "text/plain; charset=utf-8", CORS)
        writer.close()
        return
    if path == "/api/health":
        pub_age = round(time.time() - publisher_at, 1) if publisher_at else None
        await send_json(writer, {
            "ok": True,
            "status": "live" if publisher_at else "waiting",
            "publisher": {"connected": bool(publisher_at), "since": publisher_at, "age_s": pub_age},
            "last_seq": seq,
            "events_retained": len(events),
            "uptime_s": round(time.time() - started, 1),
            "clients": {"viewers": len(viewers), "sse": len(sse_clients), "total": len(viewers) + len(sse_clients)},
            "api": {"key_required": bool(API_KEY), "cors": "GET, HEAD, OPTIONS"},
            "limits": {"max_events": MAX_EVENTS, "max_limit": MAX_LIMIT, "default_limit": DEFAULT_LIMIT},
        }, extra=CORS, head_only=head_only)
    elif path == "/api/match":
        body = dict(state)
        body["ok"] = True
        body["now"] = time.time()
        body["seq"] = seq
        await send_json(writer, body, extra=CORS, head_only=head_only)
    elif path == "/api/events":
        limit = int_param(params, "limit", DEFAULT_LIMIT, 1, MAX_LIMIT)
        since = int_param(params, "since", 0, 0, 1 << 62)
        after = events_after(since)
        # with ?since the oldest missing events come first (the client polls again with the new sequence number);
        # without it the most recent events come first (a fresh consumer wants the tail of the story)
        picked = after[:limit] if since else after[-limit:]
        first = events[0]["seq"] if events else 0
        extra = list(CORS) + [f"X-Slap-First-Seq: {first}", f"X-Slap-Last-Seq: {seq}"]
        if since and since < first - 1:
            extra.append("X-Slap-Reset: 1")   # the buffer no longer reaches back that far
        await send_json(writer, picked, extra=extra, head_only=head_only)
    elif path == "/api/poses":
        await send_json(writer, list(poses), extra=CORS, head_only=head_only)
    elif path == "/api/schema":
        await send_json(writer, schema(), extra=CORS, head_only=head_only)
    elif path == "/api/events/stream":
        await sse_session(reader, writer, params, hdr, head_only)
        return
    else:
        await send(writer, "404 Not Found", b"not found\n", "text/plain; charset=utf-8", CORS)
    writer.close()


def sse_frame(ev):
    return f"id: {ev['seq']}\nevent: {ev['type']}\ndata: {json.dumps(ev, separators=(',', ':'), default=str)}\n\n".encode()


async def sse_session(reader, writer, params, hdr, head_only=False):
    """Server-Sent Events: the same events, as they come. ?since=<seq> (or the Last-Event-ID header) replays the
    backlog first, so a client that drops off can resume exactly where it was."""
    since = int_param(params, "since", 0, 0, 1 << 62)
    if not since:
        try:
            since = int(hdr.get("last-event-id") or 0)
        except ValueError:
            since = 0
    writer.write(http_head("200 OK", "text/event-stream; charset=utf-8", 0,
                           CORS + ("X-Accel-Buffering: no",), no_len=True))
    await writer.drain()
    if head_only:
        writer.close()
        return
    queue = asyncio.Queue(maxsize=1024)
    sse_clients.add(queue)
    try:
        for ev in events_after(since):
            writer.write(sse_frame(ev))
        await writer.drain()
        while True:
            get = asyncio.ensure_future(queue.get())
            eof = asyncio.ensure_future(reader.read(1))
            done, _ = await asyncio.wait({get, eof}, timeout=HEARTBEAT, return_when=asyncio.FIRST_COMPLETED)
            if get in done:
                writer.write(sse_frame(get.result()))
                await writer.drain()
            elif eof in done:
                break                       # the client hung up
            else:
                writer.write(b": keep-alive\n\n")
                await writer.drain()
            for t in (get, eof):
                if not t.done():
                    t.cancel()
    except (ConnectionError, OSError, asyncio.CancelledError):
        pass
    finally:
        sse_clients.discard(queue)


# ---- the server -----------------------------------------------------------------------------------------
async def handle(reader, writer):
    """One connection: a WebSocket upgrade goes to the relay, anything else is an HTTP request for the API."""
    try:
        head = (await reader.readuntil(b"\r\n\r\n")).decode("latin1").split("\r\n")
    except (asyncio.IncompleteReadError, ConnectionError, asyncio.LimitOverrunError):
        writer.close()
        return
    parts = head[0].split(" ")
    if len(parts) < 2:
        writer.close()
        return
    method, target = parts[0], parts[1]
    hdr = {k.strip().lower(): v.strip() for k, v in (h.split(":", 1) for h in head[1:] if ":" in h)}
    try:
        if hdr.get("upgrade", "").lower() == "websocket":
            await handshake(writer, hdr)
            await websocket_session(reader, writer, target, hdr)
        else:
            await http_session(reader, writer, method, target, hdr)
    except (ConnectionError, OSError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def main():
    global KEY, API_KEY
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1", help="0.0.0.0 lets phones on the network watch")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--key", default=os.environ.get("SLAP_LIVE_KEY", ""),
                    help="the publisher's shared secret: Unreal connects to ws://<host>:<port>/ue?key=<key>; "
                         "without it only viewers are served")
    ap.add_argument("--api-key", default=os.environ.get("SLAP_LIVE_API_KEY", ""),
                    help="the API's read key (?key=... or the X-Slap-Key header); defaults to --key")
    ap.add_argument("--api-public", action="store_true", help="the API's reads need no key (the publisher still does)")
    a = ap.parse_args()
    KEY = a.key
    API_KEY = "" if a.api_public else (a.api_key or a.key)
    server = await asyncio.start_server(handle, a.host, a.port)
    print(f"PoSlap second screen: Unreal -> ws://{a.host}:{a.port}/ue"
          f"{'?key=...' if KEY else ' (no key: publishers refused)'}, viewers -> ws://{a.host}:{a.port}", flush=True)
    print(f"API: http://{a.host}:{a.port}/api/match"
          f"{' (key needed)' if API_KEY else ' (open reads)'}, events, events/stream, health, schema", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
