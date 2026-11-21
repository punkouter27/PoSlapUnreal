# Diagnostics overlay, idle demo mode and editor-exposed reward config

Po Game Standard C9 / C10 / C12 / C13. Code: `Source/PoSlapUnreal/SlapShowcase.*` and `SlapRewardConfig.*` (this repo's own copy).

## Diagnostics overlay (Tab)
`Tab` (or the 44 px "i" button top-right on touch devices; `-ShowcaseTouch` shows it on desktop) toggles an opaque
panel (#121826, text #e6e9f2, ~14:1) with FPS, the current episode reward, inference latency (rolling mean of the last
60 policy steps, ms) and the curriculum stage. `-DiagOverlay` starts with it open.

Game hook: ASlapHUD (`SlapHUD.cpp`): the striker brain's critic value as the reward proxy, inference time measured in `USlapController::RunNet` on the physics thread, the striker's brain matched to a curriculum level. Demo mode closes the menu sheet and starts a match if none is running. Tab no longer toggles the stats panel (F3 does).

## Idle demo mode
After `IdleSeconds` (30 s) without keyboard, mouse, touch or gamepad input: the HUD hides, every agent runs on its
trained policy (inference only) and the showcase cameras cycle every `CameraDwellSeconds` (7 s): Wide, Side, Close, Orbit (ASlapHUD camera modes). Any input
returns control and restores the HUD, mode and camera. A small "Demo - press any key" hint stays at the bottom.

## Editor-first config
- `USlapShowcaseConfig` (DataAsset): idle threshold, camera list, dwell time, overlay key, latency window, touch button.
- `USlapRewardConfig` (DataAsset): penalty weights `TorqueSqWeight`, `JointAngVelSqWeight`, `ActionRateSqWeight`,
  `FootSlipWeight` and the curriculum stages (name, threshold, metric).
- Pick the assets in Project Settings > Game > Slap Showcase; empty = the C++ defaults.
- The defaults mirror `training/configs/reward_config.json`, which the Python trainer reads; a pytest (`test_unreal_reward_mirror.py`) fails when the
  two differ. Change both together.

The shipped brains predate `torque_sq` and `joint_ang_vel_sq` (see reward_config.md): they were NOT retrained. Changing a weight only affects future training runs.

## Unattended check
`UnrealEditor.exe <uproject> -game -windowed -ResX=1600 -ResY=900 -ShowcaseCapture=<dir> -ShowcaseTouch` writes
`overlay_on.png`, `overlay_off.png`, `demo_a.png`, `demo_b.png` into `<dir>` (demo shots after 35 s and one dwell later),
then quits. `-ShowcaseIdle=<s>` overrides the idle threshold.
