# Brain I/O contract (Python env ⇄ Unreal `USlapController`)

Both sides MUST build exactly this. Any change here = change both, then rerun the matching check.

Timing: physics 200 Hz, brain every 4th physics step (50 Hz), decided *before* that step.
Order of the 30 joints = `joint_order` in `training/models/<name>_fingerprint.json` (names without `red_`/`blue_`).
Heading frame = world rotated by the pelvis yaw only (pelvis x-axis projected on the floor).
Positions (xpos, site_xpos) come from `mj_kinematics` on the current qpos (mj_step leaves them one step behind).

## Observation — 108 floats, ONNX input `obs` shape [1, 108]

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
| 102–104 | target − own pelvis, heading frame (opponent `face_zone` site; L2: the `target` mocap; else 0) | m |
| 105–107 | opponent `hand_r` − own pelvis, heading frame (0 without opponent) | m |

## Action — 30 floats, ONNX output `action` shape [1, 30]

`a = clip(action, −1, 1)`;
`target = clip(stand + a · ½(hi − lo), lo, hi)` with `lo, hi` = `ctrl_range_rad`;
then rate-limit: `|target − previous target| ≤ max_joint_speed_rad_s × 0.02`.
`ctrl = target` (position actuators) for the next 4 physics steps.
