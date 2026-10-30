# Feature pass, 2026-10-29 — Features 2/4/6/8, GFX & Audio 1-10, and the rest

The user asked for the whole list from the audit round: features 2, 4, 6 and 8, all ten GFX & audio options, and
"all the other" (the remaining features, the UI list, the clean list, the realism list). Three parallel streams on
disjoint files: the Unreal broadcast layer (`Source/`), the training side (`training/`), the browser (`Web/`).

Everything here is **display-only** unless it says otherwise. MuJoCo owns the physics; Unreal and the browser
only draw. The parity invariant (Blueprint §8) is the canary for every change.

## What the pass found in the audit's own fixes

The audit's H7 fix — "`Web/serve.py` now serves only `Web/` and `training/`" — **never worked**. The guard wrote
a 404 response and then `do_GET` fell through to `super().do_GET()` anyway, so the socket received the 404 *and
the whole file*. A raw-socket probe:

```
/.git/config     -> HTTP/1.0 404 Not Found   but the body carried the file: leaked=True
/AGENTS.md       -> HTTP/1.0 404 Not Found   but the body carried the file: leaked=True
```

And the guard tested the *request* path, while `SimpleHTTPRequestHandler` **drops** `..` segments instead of
resolving them — so `/Web/../AGENTS.md` resolved to the repo root and passed. Two holes, both closed (the
handler stops at the guard; the guard checks the resolved path). After the fix every escape route 404s with no
bytes leaked and `Web/` and `training/` still serve.

The other gap: **CI ran nothing but the deploy**. There was no workflow that ran a single check.

## The new gates

| Gate | What it proves | Where |
|---|---|---|
| `training/verify_cards.py` | every card is complete and has `strike_mass > 0`; every card builds and passes A8–A13; `Content/Data` (the packaged Android build's copy) is not a build behind; every model shares the same per-athlete joint and actuator set (31 joints, 30 motors) with a motor on every motorised joint; the model's timestep agrees with the config; every roster entry and packaged brain resolves | CPU only |
| `training/verify_constants.py` | the three engines still carry the same **value** for every shared number (OBS 136/148, REACT, HILL_K, the fatigue floors, DECIM, the rates) | CPU only |
| `Scripts/check_naming.py` | an asset carries its folder's prefix; no two assets share a base name; every name is one Unreal accepts; every C++ type is PascalCase | CPU only |
| `training/prune_runs.py` | the artifact purge, dry run by default, with the AGENTS.md guards | dry run |
| `Scripts/verify_all.ps1` | every check in one command, one summary, exit 0 only when all pass | — |
| `.github/workflows/verify.yml` | the browser engine, the relay, the API, a python parse, the card and constant gates, the naming lint and the `serve.py` guard — on every push to `master` | CI |

## Feature 8 — the spectator API

`Web/live.py` serves a read-only JSON API next to the WebSocket.

| Endpoint | What |
|---|---|
| `GET /api/match` | the state: athletes, round, turn, clock, judges' cards, KO flags, danger |
| `GET /api/events` | the event array (`?since=<seq>`, `?limit=<n>` clamped to 500) |
| `GET /api/events/stream` | the same events as Server-Sent Events (`?since=`, or `Last-Event-ID`) |
| `GET /api/health` | publisher connected, last sequence, uptime, clients |
| `GET /api/schema` | the event types and the state fields, machine-readable |
| `GET /api/` | an index of the endpoints |

Every event carries a sequence number so a client can resume; the relay keeps the last 5,000 events; a token
bucket per client address answers 429. The reads are open by default (the relay binds to localhost unless
`--host 0.0.0.0`) and answer with `Access-Control-Allow-Origin: *`, because the deployed spectator site is
hosted elsewhere and the data is read-only; `--api-key <secret>` makes the reads keyed. The publisher role never
opens the API: pushing into the relay still needs the publisher key.

`node Web/api_check.mjs` PASS — the clamp, the `?since` window, the SSE backlog and the live event, health,
schema, the index, the key checks in both directions, the preflight, the refused write and the 429.

## The artifact purge, honestly

`training/prune_runs.py` finds **nothing to delete** on this checkout: all 15 runs are either inside the
24-hour window or named by a live queue (`training/runs/overnight_queue.json` still carries an unfinished L4
self-play stage, and a finished stage can still be resumed from its checkpoint). What it would free is 24 KB of
stale overnight logs. The real disk hogs are the gitignored build trees — `Intermediate/` 6.4 GB,
`training/.venv` 5.0 GB, `Saved/` 1.9 GB — not the runs.

## The main open issue: the gaze servo was missing from training

`tasks.md` had this open: *the eye-contact gaze servo drives the neck goals in matches but not in the training
envs. With `SLAP_EYE_CONTACT=0` the same candidate lands 118/118 with 0 self-falls; with it on, 16/296.*

The second half of the proposed fix ("make the servo only turn, not nod") was already in all three engines. The
missing half was the env: `training/envs/arena.py` had no servo at all, so the brains trained in a different
world from the match. The servo is now ported to the batched env with the same maths (`constants.py` holds the
numbers once; `match.py`, `envs/arena.py`, `USlapController::ApplyGaze` and `Web/sim.js gazeApply` all use them):
the world-space bias walks to the eye line on the great circle, is projected into the chest frame and writes the
neck **yaw** goal only. `training/gaze_check.py` proves it is wired in and that the observation is untouched.

### The measurement that pins the problem down

The reference engine, the shipped mixed brains, the same seed:

| | strikes landed | result |
|---|---|---|
| the gaze servo **on** (the default) | **0 of 6 turns** — every turn ends with Grandpa down before he swings | unanimous decision, blue 30-24 |
| the gaze servo **off** (`SLAP_EYE_CONTACT=0`) | **2 of 3 turns** — 8.56 m/s then 8.62 m/s, the second one a **KO** | KO in round 2, red wins |

So the turn-only servo is not "a little off" with the current brains: it is catastrophic, and it is the servo and
not the pairing that breaks it. The reason is visible in the maths — `GAZE_GAIN = 1.0` means the servo **fully
replaces** the brain's neck goal every decision, so the brain is shown a neck angle it never saw in training and
its balance controller reacts to an out-of-distribution pose. That is exactly what porting the servo into the
training env fixes: the neck pose goes back inside the training distribution.

The acceptance criteria for the retrain are therefore: strikes land with the servo **on** (the default), Grandpa's
self-fall rate stays ≤ 5 % per strike, the KO rate per strike sits in the 10–40 % band, **and** the A/B holds —
the retrained brain must not collapse when the servo is turned off, or it has merely learned to depend on it.
