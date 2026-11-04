# Training and athlete realism, 2026-11-03

The training stream of the 11-03 batch: tasks J8 + I16 + J10 (athlete realism), the CUDA 719 crashes, feature 3
(the podium obstacle course in the walk-in env), the training-side clean list, and the match events the other
streams read. Nothing here changes what the game plays today: every physics change sits behind a version switch.

## The switch: physics 1 (shipped) and physics 2 (the retrain)

`training/build_body.py --physics 2` writes `training/models/v2/` (solo, loco and arena XMLs and fingerprints with
`"physics": 2`). `--physics 1` (the default) is byte-identical to the shipped models: all four pairings were rebuilt
and `git status` showed no change. Content/Data (the packaged build) is staged from physics 1 only.

Every training-side reader takes the version from `SLAP_PHYSICS` (`constants.models_dir()`), set by
`train.py --physics 2`, `match.py --physics 2`, `slap_demo.py --physics 2`. A physics-2 brain is named
`<char>_<level>_v2` (`train.py`) or `<char>_final[_vs_<x>]_v2` (`overnight.py`), so it never overwrites a shipped brain.

### What physics 2 changes (and why the shipped brains need a retrain on it)

| # | Item | Physics 1 (shipped) | Physics 2 |
|---|---|---|---|
| 1 | palm sinks into the head | default contact softness | explicit stiff pair hand_l/hand_r vs the opponent's head (and the L2 target): solref 0.01 (the 2 x timestep floor), solimp 0.99/0.999, 15 mm margin; head and hand geoms stiff in every other contact |
| 2 | welded pairs never collide | belly-upper arm, chest-head, pelvis-chest, belly-neck, belly-head skipped by MuJoCo's weld-aware parent filter | an explicit `<pair>` each (generic: `build_body.filtered_pairs()`), seen by the fit check (A10) |
| 3 | floor and podium as soft as skin | solref 0.02 | solref 0.01, solimp 0.99/0.999 on floor, podium, the S3 step |
| 4 | condim on the soles | 3 (no torsional friction) | 4 on every floor/podium contact (the floor has priority, so its condim is the soles') |
| 5 | strike impulse | `hand_r`'s velocity whatever touched | the touching part's own velocity (`match.py`, `envs/arena.py`); L2: either hand counts with its own speed |
| 6 | torque limits | symmetric | asymmetric where humans are (`TORQUE_V2`), rotators trimmed (below) |
| 9 | anti-flail | (S2-S4 only) | the action-spread term (0.02) in the slap and arena envs too |

The engines read the version from the fingerprint. **Unreal (`USlapController`) and `Web/sim.js` still take the
strike impulse from `hand_r`** on every model; they need the same physics-2 branch (velocity of the touching geom)
before a physics-2 brain ships, then the URLab `.mjb` parity re-run (replay streams recorded:
`training/replays/v2_grandpa_vs_grandpa_arena_5s.json`, stays standing, lowest pelvis 0.834 m). URLab imports
`<contact><pair>` (`MjContactPair`), so the scene build needs no new feature.

### Measured, the shipped mixed brains in real matches (20 matches, `grandpa_final_vs_kim` vs `kim_final_vs_grandpa`)

| | physics 1 | physics 2 |
|---|---|---|
| palm-in-head depth, median / p90 / max | 46 / 66 / 76 mm | 13 / 28 / 34 mm |
| palm sink duration (> 2 mm), median / max | 50 / 85 ms | 15 / 20 ms |
| sole into the floor, max (a stomp) | 34.4 mm | 19.5 mm |
| into the podium, max | 12.9 mm | 9.1 mm |
| welded-pair contacts | 0 (filtered) | 152,823 contact-steps, max 8.9 mm (the upper arms brushing the belly, median 0.04-0.08 mm) |

The palm can still reach 34 mm: at 200 Hz a 9 m/s palm moves 45 mm per step, so a discrete contact is caught at
most one step late; the margin takes 15 mm off the worst case and the stiff impedance pushes it out in 1-4 steps
instead of 10-17. The rest (a 1-step overlap) is display work (the browser already clamps the drawn hand).

### The shipped brains on physics 2: they collapse (so physics 1 stays the shipped one)

`match.py exam --matches 20` per pairing, the shipped brains:

| pairing | physics 1: strikes / KO per strike / self-falls / hand speed | physics 2 |
|---|---|---|
| gg (`grandpa_final`) | 101/101 landed, 12 %, 0 %, 8.8 m/s | 18/78 landed, 78 %, **36 %**, 3.3 m/s |
| gk (`grandpa_final_vs_kim` vs `kim_final_vs_grandpa`) | 97/97, 14 %, 0 %, 9.2 m/s | 84/92, 17 %, **18 %**, 6.6 m/s |
| kg (Kim red) | 88/88, 14 %, 0 %, 9.2 m/s | 93/104, 8 %, **17 %**, 6.6 m/s |
| kk (`kim_final`) | 120/120, 0 %, 0 %, 9.3 m/s | 107/120, 0 %, 10 %, 6.8 m/s |

GPU arena exam (100 worlds, Grandpa red vs Kim): physics 1 strikes 99 %, KO 2 %, self-falls 4 %; physics 2
strikes 61 %, KO 21 %, self-falls 47 %. Every pairing passes L5 (no blow-ups) on both. The hand speed drops
because the slap's whip is elbow **extension** (triceps, now 55 Nm adult vs 70) and the trunk twist is trimmed;
the self-falls come from the stiffer floor and the new self-contacts the brains never felt. Hence the retrain queue.

## Audit (I16 items 1-3, J8 item 6): torque limits, mass distribution, muscle acceleration

**Mass distribution - right, unchanged.** `build_body.MASS` is Blueprint 2.2 exactly (head 6.9 + neck 1.2 = 8.1 %,
sum 100 %); `verify_realism.py` checks every body against the card.

**Muscle acceleration - right, unchanged.** Activation time constant 35 ms (human 30-40 ms), Hill's curvature
`HILL_K = 0.25` (human a/F0 0.15-0.35), top joint speeds 12 rad/s x card speed for trunk and legs (knee extension
peaks ~10-17 rad/s), 20-25 rad/s for the arm (elbow/throwing peaks 20-40); Hill applied in every engine and now in
every `slap_demo.py` path (its `video` stepped plain `mj_step`).

**Torque limits - partly off, fixed in physics 2.** Trained-adult values before the card's strength multiplier:

| joint | physics 1 (both ways) | physics 2 (negative end / positive end) | literature |
|---|---|---|---|
| knee | 220 | extension 220 / flexion 130 | ext 200-250, flex 100-150 |
| hip flex | 220 | extension 220 / flexion 150 | ext 200-300, flex 120-180 |
| ankle flex | 180 | plantar 180 / dorsi 50 | plantar 150-200, dorsi 40-50 |
| elbow | 70 | extension 55 / flexion 70 | ext 50-60, flex 60-80 |
| waist flex | 200 | extension 200 / flexion 150 | back ext 200-300, abs ~150 |
| waist twist | 120 | 90 | trunk rotation 60-90 |
| hip rotation | 100 | 60 | 40-60 |
| ankle roll | 60 | 40 | inversion/eversion 30-40 |
| shoulders, wrists, neck, hip abduction | unchanged | unchanged | within range |

Stiffness stays from the stronger direction (the forcerange clips the weaker one). The fingerprint carries both
ends (`torque_limit_nm`, `torque_limit_neg_nm`).

**Zero penetration**, checked by the fit (A10) at the reference T-pose, the stand, the swing and stride arcs with
the new pairs included: clean for both athletes (Grandpa's arms now stand at -65 deg abduction instead of -70,
clear of the belly). At single joint-limit poses the body volume itself is the limit (hip adduction through the
other leg, the arm swung across the chest): 15 (Grandpa) / 10 (Kim) such poses overlap on physics 1 and 13 / 10 on
physics 2. That is anatomy, not a build fault: in the simulation the contact stops the joint there.

**Two findings left as they are:** the per-foot friction randomisation (Blueprint 6) is a no-op, because the
floor has `priority="1"` and its friction wins every floor contact (the floor's own 0.6-1.2 randomisation is what
the brains see); and every engine still reads hand_r for the palm observation (130-135) - right for a right-handed slap.

## Kim's strike mass (J10): not the lever

Blueprint 1's method on physics 1, the shipped mixed brains, 25 matches per corner, Kim striking Grandpa:

| Kim strike mass (kg) | 2.31 (card) | 2.7 | 3.1 | 3.5 | 3.9 | 4.2 | 4.4 | 4.6 | 5.0 | 6.5 | 8.0 | 10 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| KO per landed strike | 0 % | 0 % | 0 % | 0 % | 0 % | 0 % | 0 % | 0 % | 1 % | 9.5 % | 0 % | 0 % |
| knocked off balance (KO or COM > 5 cm) | 0 % | 0 % | 0 % | 0 % | 0 % | 0 % | 12 % | 24 % | 73 % | 100 % | 100 % | 100 % |
| Kim's own falls | 0 % | 0 % | 0 % | 0 % | 0 % | 0 % | 5 % | 4 % | 11 % | 42 % | 78 % | 89 % |
| Grandpa's head snap (m/s) | 0.68 | 0.72 | 0.84 | 0.94 | 1.06 | 1.15 | 1.34 | 1.35 | 1.73 | 3.16 | 2.64 | 3.14 |

(Grandpa striking Kim in the same matches: KO 26-39 % per strike, 0 % self-falls, head snap 2.8 m/s.)
The KO band (10-40 %) is out of reach through strike mass: Grandpa does not go down - his brace on the podium
holds - and past ~5 kg the impulse throws **Kim** off her feet (her palm is still on his head when it snaps away).
Blueprint 1's own metric (~31 % off balance) lands at ~4.7 kg, 7.8 % of her body mass against Grandpa's 3.8 %, with
0 % KO. **The card stays at 2.31 kg**: the lever is the pairing (Grandpa's KO resistance on the podium, Kim's slap
direction - her impulse lifts more than it snaps), which the physics-2 retrain's mixed L4 stages train and exam.

## CUDA 719 (J10)

**Not reproduced in 18 minutes of GPU load, and not a buffer overflow.** Repro (`scratch: repro719.py`): the shipped
brains with training noise, 8192 worlds, every 5th step synchronised and every buffer read:

| env | minutes | nefc max / njmax | contacts max / naconmax | broadphase pairs max | Warp overflow bits |
|---|---|---|---|---|---|
| arena L4, Grandpa vs Kim | 4 | 97 / 512 (p99.9 76) | 81,958 / 393,216 | 129,318 / 393,216 | 0 |
| slap L2, Matt (from L1) | 10 | 57 / 512 (p99.9 40) | 47,960 / 393,216 | 68,325 / 393,216 | 0 |
| physics 2 envs, random actions | short | 53-90 / 512 | - | +20-35 % pairs | 0 |

What was found instead:

1. **`_strike_check` is where the error is reported, not where it happens.** A CUDA launch failure is sticky and
   surfaces at the next API call after the failing kernel; `_strike_check` is the first code after every physics
   step's graph launch, and the arena's version synchronised the GPU 4 times per decision (`if not on.any()`). It is
   now sync-free (scatter, no host sync), like the slap env's.
2. **A blown-up world was never reset.** Poisoning one world with NaN: Warp keeps stepping it without a crash, but
   the NaN fails every height test, so the world stepped on forever and fed NaN observations to PPO. Every env now
   ends such an episode like a fall (`envs/common.blown_up`) and counts it (`blowups` in the telemetry).
3. **The GPU runs at its thermal limit.** 87-89 C under load, SM clock throttled to 850-1000 MHz at ~40 W, with
   minute-long stretches at 180 MHz; `nvidia-smi` counters: 4.3 h of SW thermal slowdown, 4.2 h of SW power cap,
   3 s of HW power brake. Every crash of 10-30 hit this card; a driver fault under thermal stress is the most likely
   cause left (unconfirmed: the 10-30 event logs had no nvlddmkm entries).

The guard: `train.py --supervise N` runs the training as a child and resumes it from `latest.pt` after the GPU has
cooled under 75 C (`gpuwatch.cooldown`), up to N times within the same `--minutes`; checkpoints are now written
at least every 3 minutes; `overnight.py` cools down before each of its retries. The telemetry carries the buffers'
fill and Warp's overflow bits every 10 iterations and the GPU's temperature/clock every 50, so the next crash can be
read against them. Config snapshots now record the physics version, the fingerprint sha and the GPU state.

## Feature 3: the podium obstacle course (S3, tasks I10)

The S3 course existed (2026-10-29: a step in the path, the podium further on, a waypoint beyond it, reaction
latency, slopes). Fixed and finished:

* **"Cleared the step" never meant climbing it**: the test was "pelvis above the step's top", which a 0.9 m pelvis
  always is - walking past the step's footprint counted. Now: a sole standing on the step's top (sole centre above it).
* **Step down**: a new flag/bonus/exam bar - after the climb, both soles back on the floor beyond the far edge.
* **Support**: a sole on the step counts as foot contact (stance, gait clock, load) like the floor.
* **Balance**: the trunk, head or an arm on the step is a knockdown (as on the floor); a shin or thigh brushing its
  edge is not (counting the legs too made 99.7 % of episodes falls).
* **Curriculum**: `step_scale` from a 1 cm sill (the S2 walker's shuffle clears 1-2 cm; a 3 cm sill tripped it in
  every episode) up to 10-20 cm, +0.01 per iteration whenever clear > 60 % and down > 50 % (0.05 reached the full step in 20 iterations in the smoke run); carried in checkpoints; graduation
  and exams only at the full step. Exam bars: no-fall 95 %, pace 60 %, clear 70 %, down 70 %, reach 70 %.

Smoke train (`grandpa_S3_smoke2_1103`, from `grandpa_S2.pt`, 4096 worlds, 14 min, 21.4 M agent-steps, `--supervise 2`,
the GPU at 86-89 C, no crash, 0 blow-ups, buffers never past 1/5 full): on the 1 cm sill the walker learned the course
within 5 minutes - clear 0.22 -> 0.87, down 0 -> 0.80, last waypoint reached 0 -> 0.62 (iteration 40) - and then the
curriculum (+0.05 per iteration in that run) put the full 10-20 cm step in within 20 iterations and every rate fell
back to 0-0.2 (end: clear 0.12, reach 0.07, falls 98-100 % of episodes, mostly after a stumble at the step or the
podium). The pipeline works; the ramp is now +0.01 per iteration. A real S3 run needs hours (queue line below).
Before this pass (a 3 cm sill, the pelvis-height "clear", the legs counted as a knockdown on the step) the same
start fell in 100 % of episodes within 3.8 s.

## Clean (D) and the events (E)

* `envs/common.py`: the per-world model fields, the CUDA-graph capture, the contact slots, the blow-up test and
  the buffer readout, once instead of three times; `wp.config.quiet` (no "Module ... load" line per kernel in
  every run log); dead `con_frame` buffers and a dead `joint_qposadr` branch removed.
* One source for the observation count: `brain_cpu.summarise`, `envs/locomotion` (148 -> `OBS_EXT`),
  `verify_realism` read `constants.OBS/OBS_EXT`; `verify_constants.py` passes, plus a new feint-threshold parity
  check against `SlapHUD.cpp`.
* Per-athlete action buffer (I15 item 7): the arena's 0-20 ms action delay was one per world (both athletes the
  same); it is now per athlete.
* `build_body.fit` iterates its overlaps sorted: the fingerprints' `geom_scale` key order no longer depends on the
  hash seed (Matt's fingerprint reordered once, values unchanged).
* `train.py` no longer fails a finished run when matplotlib is missing (the fresh venv had none; installed now).
* The slap references are kept: `envs/slap.py` still reads them for L2 imitation.
* Events: `strike` gains `hand_speed`, `feint`, `feints`; new `feint` and `strike_result` (head snap peak, stagger,
  KO) events; `compare` ignores the new fields until the other engine logs them (checked: PASS against a log
  without them). Field list: `DOCS/20261103/events.md`.

## The slap references, rebuilt under Hill (J10 item 8)

`slap_demo.py check` (20 jittered variations, under Hill):

| reference | before (the 10-2x search, plain mj_step) | rebuilt under Hill |
|---|---|---|
| Grandpa, physics 1 | 0/20 clean, 18 falls, ~7.5 m/s | nominal 9.8 m/s clean; 6/20 clean, 8 falls |
| Kim, physics 1 | 0/20 clean, 20 falls, ~7.8 m/s | nominal 8.4 m/s clean; 5/20 clean, 9 falls |
| Grandpa, physics 2 | 0/20, 19 falls (the v1 reference) | nominal 9.5 m/s clean; 9/20 clean, 4 falls |
| Kim, physics 2 | 0/20, 20 falls (the v1 reference) | nominal 9.2 m/s clean; 5/20 clean, 6 falls |

## The retrain (not run here: > 15 min)

Queue: `training/configs/retrain_v2_queue.json` (copied to `training/runs/retrain_v2_queue.json`):

    cd training
    .venv/Scripts/python.exe overnight.py --hours 11 --queue retrain_v2_queue.json

| stage | char | level | minutes | from |
|---|---|---|---|---|
| 1 | grandpa | L2 (strike + guard) | 75 | `grandpa_final.pt`, critic warm-up 20, imitation 0.5 of the v2 reference |
| 2 | grandpa | L3 (absorb + strike) | 60 | stage 1 |
| 3 | grandpa | L4 self-play | 90 | stage 2 |
| 4 | kim | L2 | 75 | `kim_final.pt` |
| 5 | kim | L3 | 60 | stage 4 |
| 6 | kim | L4 self-play | 90 | stage 5 |
| 7 | grandpa | L4 vs Kim (frozen, stage 6) | 90 | stage 3 |
| 8 | kim | L4 vs Grandpa (frozen, stage 7) - the gate | 90 | stage 6 |

**Estimate: 630 min of training, ~10.5 h wall** with exams and the crash guard's cool-downs (each stage stops
early when it graduates). At 4096 worlds S3 ran ~1.5 M agent-steps/min on this card under thermal throttling; the
8192-world stages run about 1.5-2x that. Outputs: `models/brains/<char>_<level>_v2.onnx` on graduation,
`<char>_final[_vs_<x>]_v2.pt/.onnx` at the end. Acceptance (the 10-29 criteria, now on physics 2): strikes land
with the gaze servo on, self-falls <= 5 % per strike, KO 10-40 % per strike in every pairing, and the A/B with the
servo off holds. Then: the Unreal/web physics-2 strike branch, a URLab `.mjb` replay + `match.py compare` on v2,
and only then the switch of the shipped arenas to v2.

Not in the queue: Matt (his 10-30 L2 run's `latest.pt` is not on this clone) and S3 (the obstacle course is an
opt-in walk-in feature; queue it after the duel brains: `{"level": "S3", "char": "grandpa", "minutes": 120,
"resume": "models/brains/grandpa_S2.pt", "no_gate": true}`).
