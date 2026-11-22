# Diagnostics overlay, idle demo mode and editor-exposed reward config

Po Game Standard C9 / C10 / C12 / C13. Code: `Source/PoSlapUnreal/SlapShowcase.*` and `SlapRewardConfig.*` (this repo's own copy).

## Diagnostics overlay (Tab)
`Tab` (or the 44 px "i" button top-right on touch devices; `-ShowcaseTouch` shows it on desktop) toggles an opaque
panel (#121826, text #e6e9f2, ~14:1) with FPS, the current episode reward, inference latency (rolling mean of the last
60 policy steps, ms) and the curriculum stage. `-DiagOverlay` starts with it open.

**Benchmark sim speed** (button in the overlay, or `B` while it is open): a copy of the running arena's MuJoCo model is
stepped on the CPU, off the game thread, for `BenchmarkSeconds` (1.5 s) with zero controls, reset every 5 simulated
seconds like an episode, and the overlay shows physics steps/s and the real-time factor. It is labelled for what it is:
one CPU env on this machine; training runs MuJoCo Warp on the GPU with many envs (`training/`), so it is a proxy, not
the trainer's throughput. The browser port (`Web/`) has the same row and button under TAB / the stats button (MuJoCo
WASM in that browser).

Game hook: ASlapHUD (`SlapHUD.cpp`): the striker brain's critic value as the reward proxy, inference time measured in `USlapController::RunNet` on the physics thread, the striker's brain matched to a curriculum level. Demo mode closes the menu sheet and starts a match if none is running. Tab no longer toggles the stats panel (F3 does).

## Idle demo mode
After `IdleSeconds` (30 s) without keyboard, mouse, touch or gamepad input (frames longer than `MaxIdleFrameSeconds`,
0.25 s, do not count: shader warm-up and level loads are not idle time): the HUD hides, every agent runs on its
trained policy (inference only) and the showcase cameras cycle every `CameraDwellSeconds` (7 s): Wide, Side, Close, Orbit (ASlapHUD camera modes). Any input
returns control and restores the HUD, mode and camera. A small "Demo - press any key" hint stays at the bottom.

## Editor-first config
- `USlapShowcaseConfig` (DataAsset `/Game/Project/DataAssets/DA_ShowcaseConfig`): idle threshold, longest frame that
  counts as idle, camera list, dwell time, overlay and benchmark keys, benchmark budget, latency window, touch button.
- `USlapRewardConfig` (DataAsset `/Game/Project/DataAssets/DA_RewardConfig`): penalty weights `TorqueSqWeight`, `JointAngVelSqWeight`, `ActionRateSqWeight`,
  `FootSlipWeight` and the curriculum stages (name, threshold, metric).
- Both assets are selected in Project Settings > Game > Slap Showcase (`Config/DefaultGame.ini`); empty = the C++ defaults.
  Automation test `PoSlap.Showcase.ConfigDataAssets` checks the selection and that the reward asset equals
  `training/configs/reward_config.json`.
- The defaults mirror `training/configs/reward_config.json`, which the Python trainer reads; a pytest (`test_unreal_reward_mirror.py`) fails when the
  two differ. Change both together.

The shipped brains predate `torque_sq` and `joint_ang_vel_sq` (see reward_config.md): they were NOT retrained. Changing a weight only affects future training runs.

## Unattended check
`UnrealEditor.exe <uproject> -game -windowed -ResX=1600 -ResY=900 -ShowcaseCapture=<dir> -ShowcaseTouch` writes
`overlay_on.png`, `overlay_off.png`, `demo_a.png`, `demo_b.png` into `<dir>` (demo shots after 35 s and one dwell later),
then quits. `-ShowcaseIdle=<s>` overrides the idle threshold; `-ShowcaseBenchmark` runs the sim benchmark before the
`overlay_on` shot. The scripted HUD checks (`-SlapUiTest`, `-SlapRoundTripTest`, `-SlapLayoutTest`, `-SlapPairTest`,
`-SlapTour`) suspend demo mode: their synthetic clicks bypass the input the idle clock watches.

## Tests
`UnrealEditor-Cmd.exe PoSlapUnreal.uproject "-ExecCmds=Automation RunTests PoSlap.Showcase;Quit" -unattended -nullrhi`:
`IdleDemoIgnoresLongFrames` (a 70 s frame adds no idle time; 20 s of 60 fps frames: no demo; 31 s: demo; input: back),
`OverlayAndSimBenchmark` (the overlay rows; a 0.5 s benchmark on `training/models/grandpa_vs_kim_arena.xml`),
`ConfigDataAssets`.
