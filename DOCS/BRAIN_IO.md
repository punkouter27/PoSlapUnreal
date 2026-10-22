# Brain I/O contract (Python env ⇄ Unreal `USlapController`)

Both sides MUST build exactly this. Any change here = change both, then rerun the matching check.

Timing: physics 200 Hz, brain every 4th physics step (50 Hz), decided *before* that step.
Order of the 30 joints = `joint_order` in `training/models/<name>_fingerprint.json` (names without `red_`/`blue_`).
Heading frame = world rotated by the pelvis yaw only (pelvis x-axis projected on the floor).
Positions (xpos, site_xpos) come from `mj_kinematics` on the current qpos (mj_step leaves them one step behind).

## Observation — 136 floats, ONNX input `obs` shape [1, 136]

Older brains read the first 108 (before memory) or 130 (before the own palm); both sides feed a brain only as many numbers as its input has.

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
| 105–107 | opponent `hand_r` − own pelvis, heading frame. **0 when role = 0** or no opponent; Unreal keeps it 0 for the striker too (L2 trained alone) | m |
| 108–125 | **memory:** inputs 102–107 as they were 5, 10 and 15 decisions ago (0.1, 0.2, 0.3 s; 6 each). At a turn's first decision all three = the current 102–107 | m |
| 126–127 | **memory:** the opponent's last strike this match: touch time / 30 s, palm speed / 10 (both 0 until he has struck; reset each match) | |
| 128 | own **stamina**: 1 fresh, − card `stamina_per_strike` per turn struck, floor 0.7. Scales the action rule's speed limit | |
| 129 | own **neck strength**: 1 fresh, − card `neck_damage_per_snap` × head snap (m/s) per strike absorbed (not a KO), floor 0.5. Scales the neck motors' `forcerange` | |
| 130–132 | own **palm**: `hand_r` geom centre − own pelvis, heading frame (every role) | m |
| 133–135 | own **palm facing**: the `hand_r` box's −z axis (world), heading frame | unit |

**Unreal's match flow (v1):** coin toss picks the striker (role +1, clock, defender's head); the defender plays role 0 (guard).
The turn ends 1.5 s after any striker part touches the defender's head, at 30 s, or on a knockdown; both athletes then reset
to the stand pose and the roles swap. After each turn: the striker's stamina drops; if he touched, the defender
remembers the strike (126–127) and, unless knocked out, his neck strength drops; a `condition` event logs both athletes'
(stamina, neck). Neck `forcerange` = the model's own × neck strength, set at every turn start. The training model has the arena's podium at the same spot (a touch = a fall).

## Action — 30 floats, ONNX output 0 `actions` shape [1, 30] (output 1 `value` [1, 1], the critic)

`a = clip(action, −1, 1)`;
`target = clip(stand + (a > 0 ? a · (hi − stand) : a · (stand − lo)), lo, hi)` with `lo, hi` = `ctrl_range_rad`: a = ±1 reaches
the range ends (until 2026-10-22 `stand + a · ½(hi − lo)`, which left up to half of a range out of reach, e.g. Grandpa's
right shoulder raise above +4°);
then rate-limit per joint: `|target − previous target| ≤ max_joint_speed_rad_s[j] × 0.02 × stamina` (a list in joint
order; older fingerprints hold one number for every joint).
`ctrl = target` (position actuators) for the next 4 physics steps.
