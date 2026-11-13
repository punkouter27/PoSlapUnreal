# Brain I/O contract (Python env ⇄ Unreal `USlapController`)

Every engine MUST build exactly this (Python envs and `brain_cpu.Athlete.obs`, Unreal `SlapBrainIO.cpp`, the browser
`sim.js`). Any change here = change all of them, then rerun the matching checks. Inputs are only ever appended.
(Restored 2026-11-13 from commit `cb01401`; the shared numbers now come from `training/gen_constants.py`.)

Timing: physics 200 Hz, brain every 4th physics step (50 Hz), decided *before* that step.
Order of the 30 joints = `joint_order` in `training/models/<name>_fingerprint.json` (names without `red_`/`blue_`).
Heading frame = world rotated by the pelvis yaw only (pelvis x-axis projected on the floor).
Positions (xpos, site_xpos) come from `mj_kinematics` on the current qpos (mj_step leaves them one step behind).

## Observation — 142 floats (interface 5), ONNX input `obs` shape [1, 142]

Every engine builds all 142 and feeds a brain only as many numbers as its input has: 108 (interface 1, before memory),
130 (2, before the own palm), **136 (3: every shipped brain)**, 140 (4, foot contacts), **142 (5, player command vector)**. **The input width is the
interface tag at run time**; new checkpoints also carry `extra.interface` / `physics` / `obs_dim` / `config` (hashes
of card, model, level config) and exported ONNX files the same in `metadata_props` (`DOCS/20261104/policy_change_log.md`).
Inputs are only ever appended (zero-initialised first-layer weights when a checkpoint widens: `ppo.load(widen=True)`).
The unused 12-float extension (148, "OBS_EXT") was retired the same day: no brain ever read it.

| Index | What | Units / scale |
|---|---|---|
| 0–29 | joint angle − stand pose angle | rad |
| 30–59 | joint velocity × 0.1 | rad/s |
| 60 | pelvis height | m |
| 61–63 | gravity direction in pelvis frame, `R_pelvisᵀ · (0,0,−1)` | unit |
| 64–66 | pelvis linear velocity in heading frame | m/s |
| 67–69 | pelvis angular velocity in pelvis frame (free-joint `qvel[3:6]`) × 0.25 | rad/s |
| 70–99 | previous action | [−1, 1] |
| 100 | role: +1 strike, −1 defend, 0 none | |
| 101 | turn clock remaining (1 → 0 over 30 s) | |
| 102–104 | role ≠ 0: target − own pelvis (opponent **head geom centre**; L2: the `target` mocap sphere, placed where that head is). **Role 0: own foot-box centre − pelvis** (box centre = the athlete's start pelvis x, y; z = 0). Heading frame | m |
| 105–107 | opponent `hand_r` **as it was 6 decisions (0.12 s) ago** (`REACT`: visual reaction; from 2026-10-26) − own pelvis now, heading frame. At a turn's first decision = the current hand. **0 when role = 0** or no opponent; Unreal keeps it 0 for the striker too (L2 trained alone) | m |
| 108–125 | **memory:** inputs 102–107 as they were 5, 10 and 15 decisions ago (0.1, 0.2, 0.3 s; 6 each). At a turn's first decision all three = the current 102–107 | m |
| 126–127 | **memory:** the opponent's last strike this match: touch time / 30 s, palm speed / 10 (both 0 until he has struck; reset each match) | |
| 128 | own **stamina**: 1 fresh, − card `stamina_per_strike` per turn struck, floor 0.7. Scales the action rule's speed limit | |
| 129 | own **neck strength**: 1 fresh, − card `neck_damage_per_snap` × head snap (m/s) per strike absorbed (not a KO), floor 0.5. Scales the neck motors' `forcerange` | |
| 130–132 | own **palm**: `hand_r` geom centre − own pelvis, heading frame (every role) | m |
| 133–135 | own **palm facing**: the `hand_r` box's −z axis (world), heading frame | unit |
| 136–139 | **foot contact** (interface 4): `heel_l, toe_l, heel_r, toe_r`. 1 when the sole box (`foot_l` / `foot_r`) has a contact with a geom of the **world body** (floor, podium, the course's step) whose point, taken along the box's local x axis (its length) from the box centre, is behind it (heel) / at or ahead of it (toe). The contact list is the last physics step's (`d.contact` at decision time, as everywhere); a flat foot reads 1, 1 (four corner contacts) | 0/1 |
| 140 | **strike command** (interface 5): player power vs accuracy (`+1` max power, `-1` max accuracy, `0` neutral AI). Striker (`role > 0`) only; `0` otherwise | [−1, 1] |
| 141 | **defence command** (interface 5): player brace timing (`+1` brace now, `0` neutral AI). Defender (`role < 0`) only; `0` otherwise | [0, 1] |

Where: `brain_cpu.foot_contacts`, `envs/common.foot_contacts` (GPU, every env), `SlapBrainIO.cpp FootContacts`,
`sim.js footContacts` (exported; read `d.geom_xmat` after each `contact.get()`: a get can grow the WASM heap and
detach older views). Python vs browser on the same model agree on every step while the two states agree (gk, kk, gg
arenas and the loco scene, 600 steps).

**The match around the brain:** the rules are `training/rules.json` and the docstring of `training/match.py` (the
reference; Unreal and the browser play the same file through `SlapShared.gen.h` / `shared.gen.js`). What reaches the
brain from them: role and clock (100–101); the striker's hand lag (105–107); the opponent's last strike (126–127);
stamina and neck strength (128–129), both updated at the turn's end and logged in the `condition` event. Neck
`forcerange` = the model's own × neck strength, set at every turn start.

**Rules 2 (models of physics 4 on) change no index and no width.** Two things a brain meets differently:
the hand in 105–107 is `REACT + int(daze_react × (1 − neck) / (1 − MIN_NECK) + 0.5)` decisions old (6 at a fresh
neck, 10 at the floor: `constants.daze_lag`), fixed for the turn; and a stun or a count can leave every motor at
`stun_strength` of its force for a while (the brain is not told: it feels it). A defender down under a count is
driven by his get-up brain (G1) if he has one - same inputs with role 0, the last action carried over.

The layout above is also data: `constants.LAYOUT` (index ranges and names). `verify_constants.py` checks that it
tiles 0 – OBS−1 without a gap and that every range is in this table.

## Action — 30 floats, ONNX output 0 `actions` shape [1, 30] (output 1 `value` [1, 1], the critic)

`a = clip(action, −1, 1)`;
`target = clip(stand + (a > 0 ? a · (hi − stand) : a · (stand − lo)), lo, hi)` with `lo, hi` = `ctrl_range_rad`: a = ±1 reaches
the range ends (until 2026-10-22 `stand + a · ½(hi − lo)`, which left up to half of a range out of reach, e.g. Grandpa's
right shoulder raise above +4°);
**interface 4 only** (width >= 140): the first-order low-pass, `target = goal + α (target − goal)`,
`α = 1 − exp(−2π · 8 Hz / 50 Hz) = 0.634069` (`constants.LPF_*`, `SlapControllerInternal.h LpfHz / LowPass`,
`sim.js LPF_*`); its state is the goal itself (the last rate-limited target; reset to the stand pose with the goal;
the gaze servo's write to the neck yaw goal is part of it in every engine). Brains of interface ≤ 3 skip it.
Then rate-limit per joint: `|target − previous target| ≤ max_joint_speed_rad_s[j] × 0.02 × stamina` (a list in joint
order; older fingerprints hold one number for every joint).
`ctrl = target` (position actuators) for the next 4 physics steps. The actuators filter it (`timeconst` 35 ms,
`build_body.MUSCLE_TC`: muscle activation), so every reset sets each actuator's `act` to its joint's angle (`settle_muscles`).
The walk-in (S2 brains) keeps its own 0.85 EMA after the rate limit (`locomotion.py`, `SlapWalkIn.cpp`); a walking
brain of interface 4 gets the low-pass before the rate limit as well.
Every physics step each motor's `forcerange` is capped by Hill's force-velocity law on its joint's speed
(`brain_cpu.hill`: top speed = `max_joint_speed_rad_s`), then restored after the step.
