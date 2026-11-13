# Broadcast pass, stage 1 (2026-11-12)

User request: ten spectator features, "do all", render side first. This note covers the display-only stage. The two
features that change brains (style commands, ring navigation) are in `stage2_brain_changes_audit.md` and are not started.

**Nothing here reaches the physics, the brains, the referee or the timing.** No observation, action, rule or constant
changed, so the three-engine parity checks are untouched. The only physics-thread addition is a memcpy of body poses
into a ring allocated at bind time (the reel recorder).

## What was built

| # | Feature | Where | Notes |
|---|---|---|---|
| 1 | Win probability | `training/winprob.py`, `training/models/winprob.json`, `SlapHUDWinProb.cpp`, `Web/live.py` (`win_prob` in `/api/match`) | Logistic model, five corner-antisymmetric inputs, no bias. A thin row under the momentum bar |
| 4 | Sequencer camera moves | `Scripts/broadcast_pass.py` -> `/Game/Cinematics/LS_*`, `SlapHUDStage.cpp` (`SeqTick`) | Intro crane, KO crane, winner push. AUTO camera only. `-SlapSeq=0` |
| 5 | Jumbotron | `/Game/FX/M_Jumbotron`, `SlapHUDStage.cpp` (`Jumbo`) | 272 x 153 cm screen behind the podium, second camera captured at 30 Hz, HIGH tier and up, desktop. `-SlapJumbo=0` |
| 7 | Soft tissue | `M_<athlete>` (JiggleA/B nodes), `SlapHUDStage.cpp` (`Jiggle`) | Belly 3.5 Hz and jowl 7 Hz on damped springs, drawn as world-position offset. Softness from the card's mass and height. `-SlapJiggle=0` |
| 9 | Highlight reel | `SlapReel.h/.cpp`, `SlapHUDReel.cpp`, `Scripts/highlight_reel.py` | 6 s of per-step body poses saved after a KO or a 9 m/s slap; playback renders one physics step per frame |
| 10 | 3D titles | `SlapHUDStage.cpp` (`Titles`), Text3D plugin | Fighter names on the mat on the walk-in and for the winner; the big caption standing between the heads. `-SlapTitles=0` |

## What was not built, and why

| # | Feature | Outcome |
|---|---|---|
| 6 | 3D crowd | Already there: `Env_Crowd` with `M_CrowdLive` (Rise seat bounce, Cheer phone lights), driven by the tension signal. No change |
| 8 | Eyes and faces | Not built. The athlete meshes are single scans with 17 bones: no eye geometry, no face rig. Needs an art pass per character (eyes, lids, a few blendshapes) before code can drive it |
| 7 | Kawaii Physics | Not used. The athletes are `UPoseableMeshComponent`s with no AnimGraph, which is where that plugin runs. The spring is in C++ and the skin material instead |
| 10 | Motion Design, Twitch, ElevenLabs, uploads | Motion Design scenes need editor authoring, so the 3D graphics use Text3D directly. External services were excluded by the user ("local only") |
| 9 | Movie Render Queue | Not used. The reel is rendered by the game itself at a fixed 5 ms frame time with one PNG per frame, which keeps the one-step-per-frame guarantee simple. No spatial or temporal oversampling |

## Win probability model

Fitted on 59 distinct finished matches in `Logs/Gameplay` (124 simulated today with `winprob.py --simulate`, exact
replays counted once), 397 samples. Held-out Brier score 0.161 against 0.25 for a coin. Weights: score 0.13 per point,
knockdown difference 0.08, neck 1.32, stamina -0.83, late-score 0.09. The stamina weight is negative because the
athlete who has struck more has spent more stamina; with 59 matches that sign is not robust, and Mom and Tong (L0
brains that cannot strike) are in the sample. Refit as real matches accumulate.

## Fix found on the way

A clean checkout did not build: UBT merged the HUD's `.cpp` files into a unity blob and their file-local names
(`Amber`, `Red`, `Hi`) collided. `PoSlapUnreal.Build.cs` now sets `bUseUnity = false`.

## Checks run

See `SCREENSHOTS/261112_broadcast_pass/index.html` for the before/after screenshots and the check table.
