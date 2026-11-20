# Reward penalties and curriculum thresholds (editor-first config)

`training/configs/reward_config.json` is the one place for the reward penalty weights and the curriculum thresholds.

| Key | Term | Weight | Where |
|---|---|---|---|
| `torque_sq` | sum of squared joint torques (`qfrc_actuator` on the hinge dofs, N m) | -2e-06 | `envs/slap.py`, `envs/locomotion.py` |
| `joint_ang_vel_sq` | sum of squared joint angular velocities (rad/s) | -1e-04 | `envs/slap.py`, `envs/locomotion.py` |
| `action_rate_sq` | ‖a_t − a_(t−1)‖² (the existing "smooth" term) | -0.01 | both envs (was the literal 0.01) |
| `foot_slip` | planar sole speed above `foot_slip_deadband_mps` (0.18) while the sole is on the floor | -0.30 | both envs (`locomotion.py`'s `leg_drag`; new in `slap.py`) |

Curriculum: one row per ladder stage (`L0`-`L4`, `S2`-`S5`, `G1`) with `threshold` (the primary role's mean episode
return over 100 episodes) and `role_targets` (every role's target). `levels.py` reads `reward_target` from it.

- Trainer: `training/reward_config.py` loads the file; `levels.py`, `envs/slap.py` and `envs/locomotion.py` read the
  weights from it (no duplicated literals). `tests/test_reward_config.py` checks the trainer sees the file's values.
- Unreal: the DataAsset `USlapRewardConfig` (`Source/PoSlapUnreal/SlapRewardConfig.h`) mirrors the same values as
  `UPROPERTY(EditAnywhere, BlueprintReadWrite)` fields; the diagnostics overlay shows the current curriculum stage.

**Not retrained.** `torque_sq` and `joint_ang_vel_sq` (and `foot_slip` in the slap env) were added on 2026-11-20. Every
shipped brain in `training/models/brains/` was trained before that, so they never saw these terms. Measured on the L0
env with random actions they cost about 0.008 per step against a ~2.5 per-step reward, so the next training run picks
them up without changing what the levels pay; a retrain + the level exams are needed before claiming any effect.
