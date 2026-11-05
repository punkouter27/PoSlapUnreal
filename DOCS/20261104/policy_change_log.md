# Policy change log: interface 4 (2026-11-04, the training stream)

Written **before** the change (AGENTS.md §3, "Before any change"). Items are the user-approved list (41, 43-47, 29, 49, 50).

## Version tags

| Tag | Value | Meaning |
|---|---|---|
| `interface` | **4** (new) | 140 inputs: BRAIN_IO 0-135 unchanged, **136-139 = foot contact** (heel/toe per foot); the **8 Hz action low-pass** is on |
| `interface` | 3 | 136 inputs (every shipped brain); no low-pass |
| `interface` | 2 / 1 | 130 / 108 inputs (older brains, still read their first n) |
| `physics` | 1 / 2 / 3 | the model build (`build_body.py --physics`), unchanged by this pass |

The interface is **read from the brain's input width** at run time (Unreal `NumIn`, the browser's
`inputMetadata[0].shape[1]`, Python `onnx_policy.n_in`): 140 -> interface 4. The same tag is written into every new
checkpoint (`extra.interface`, `extra.physics`, `extra.obs_dim`) and into every exported ONNX (`metadata_props`
`interface`, `physics`), so a tool can check it without guessing. A brain is always fed its own width; a 136 brain
never sees the low-pass. No shipped brain changes behaviour.

`OBS_EXT` (148: 12 "locomotion proprioception" floats at 136-147 in `envs/locomotion.py` and Unreal's `BuildObs`) is
**retired**: no trained brain ever read it (all 30 ONNX brains are 136 wide, one 130, one 108; `train.py` never built
the extended env), and its two implementations disagreed (contacts in Python, a 4.5 cm height guess in Unreal). The
foot-contact bits take 136-139 instead, one definition in every engine. Nothing is reordered for any brain that exists.

## Which policies each item touches

Policies today, per character (Grandpa, Kim, Matt): the **match brain** (L0-L4 ladder, one brain, every role), the
**walk-in brain** (S2; S3/S4 course in training), and from this pass the **get-up brain** (G1, new). Shipped files:
`models/brains/*.onnx` (interface 3, physics 1; `*_v2` physics 2).

| # | Item | Kind (AGENTS §3) | Touches | Shipped brains |
|---|---|---|---|---|
| 41 | Foot contact inputs 136-139 | interface (append) | every policy trained from now on: match (L0-L4), walk (S2-S4), get-up (G1), all three characters. Resumed checkpoints widen with **zero first-layer weights** (`ppo.load(widen=True)`, actor and critic) | none: run on their own 136 |
| 43 | 8 Hz first-order action low-pass | interface (behaviour) | the same policies (interface 4 only). Every runtime keys it on width >= 140: `envs/*`, `brain_cpu`/`match.py`, `Web/sim.js`, Unreal `Decide` + walk-in | none |
| 46 | CoM recovery reward (5 cm) at every level | reward | match brain at L0-L4 (guard, strike after the touch, absorb), walk brain at S2-S4 (standing phases), get-up G1 | none (training only) |
| 47 | High-frequency (>10 Hz) joint oscillation penalty | reward (motion style) | every policy in training (shared motion-style reward, AGENTS §3 hand-overs) | none |
| 45 | +-5 deg slope curriculum | new training condition | walk brain (new level S5: the S3 podium course with the tilt ramped 0 -> 5 deg) | none |
| 44 | Get-up policy + director table | new skill | new get-up brain per character (G1) + director rows (DOCS/20261104/director_transitions.md). Runtime switching: **not in scope** until a get-up brain graduates | none |
| 29 | Tunnelling regression | check only | none (a physics audit) | none |
| 49 | Config snapshot validation | tooling | none (refuses a mismatched resume) | none |
| 50 | Retrain v4 queue + resumable driver | tooling | the retrain of the match brains (Grandpa, Kim, Matt) on interface 4 / physics 3 | none |

Rules for the touched policies: physics stays 3 (`models/v3`) for the retrain; every resumed v3 checkpoint is widened
136 -> 140 on load (zero weights on 136-139, `obs_rms` mean 0 / var 1 there) and from then on trains with the
low-pass; `--critic-warmup` gives the critic time to absorb the new reward terms before the actor moves.

## Regression scenarios to re-run afterwards (§3 "Afterwards")

Per engine: `verify_constants.py`, `verify_cards.py`, `verify_realism.py`, `replay_check.py` (URLab `.mjb`),
`match.py compare` (fresh Unreal log), `node Web/check.mjs gg|kk|gk|kg` (and `SLAP_PHYSICS=3`), the Unreal editor
build + `-game -SlapPairTest`, and the new `physics_audit.py tunnel`. Results: the stream's final report and
`DOCS/BRAIN_IO.md`.

## Afterwards: what landed and the gates (2026-11-04, ~23:40)

| # | Status | Where |
|---|---|---|
| 41 | done | `constants.OBS = 140`, `FOOT = 136:140`; `envs/common.foot_contacts` (slap, arena, locomotion, getup), `brain_cpu.foot_contacts`, `sim.js footContacts` (exported), `SlapBrainIO.cpp FootContacts`; Android rebuild is width-agnostic (`export_cpu_brains.py`: a 140 brain rebuilds, 140 inputs). Widening verified: `interface 3 -> 4: inputs 136-139 added with zero weights (max |w| 0)` (actor and critic) |
| 43 | done | `common.lowpass`, `Athlete.decide`, `sim.js decide`, Unreal `Decide` + walk-in, keyed on width >= 140; a `VsFrozen` 136 opponent keeps its own rule; unknown widths default to the shipped 136 (no filter) |
| 46 | done | `common.com_recovery` (0.3 exp(-e^2/(5 cm)^2), e from the stance offset read at the first step): guard/absorb every step, striker after the touch, walk when the command is 0, get-up when up |
| 47 | done | `common.hf_oscillation`: 10 Hz first-order high-pass of joint velocity at 50 Hz, 0.002 per (rad/s)^2 summed, capped at 2/step; every env |
| 45 | done | level S5 (S3 course, tilt 0 -> +-5 deg via `slope_scale`, +0.02 per iteration while falls < 10 %; graduation at full tilt) |
| 44 | done (training side) | `envs/getup.py` (G1), `getup_states.py`, banks `models/v3/getup/{grandpa,kim,matt}_states.npz` (4000/3999/4000 real knockdowns), `director_transitions.md`. Runtime switching: not in scope until G1 graduates |
| 29 | done | `physics_audit.py tunnel` in `Scripts/verify_all.ps1`: 2732 palm-first shots at 15 m/s on the 9 v3 arenas, 0 through (the same gate at 80 m/s: 206/306 through, so it can fail) |
| 49 | done | `train.check_config` before anything is written; checkpoints carry `interface/physics/obs_dim/config`; ONNX `metadata_props` |
| 50 | done | `runs/retrain_v4_queue.json` (+ `configs/` copy), `overnight.py --resume` + lock + carry rebuild, `queue_task.ps1` |

Gates: `verify_constants` ALL AGREE; `verify_cards` PASS; `verify_realism` ALL 10 VERIFIED; `replay_check` 0.000 mm / 1000
steps (XML vs URLab `.mjb` 0.000 mm); `node Web/check.mjs gg|kk|gk|kg` exit 0 on physics 1 and 3; editor build
Succeeded; `-SlapPairTest` loaded `L_Arena_grandpa_vs_kim` (brains read 136 inputs); `match.py compare` on a fresh
Unreal gk log: FAIL at the first event (Python: blue knockdown at 0.9 s) - **the HEAD Python code gives the identical
event stream** (67 lines, byte-identical), so it is a pre-existing gk divergence, not this change.
