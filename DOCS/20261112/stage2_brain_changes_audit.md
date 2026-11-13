# Stage 2 audit: style commands and ring navigation (2026-11-12)

Status: **proposal, nothing changed yet.** AGENTS.md section 3 asks for this log before any policy, state-machine or
interface change. No brain, env, referee or controller code has been touched for these two features.

Stage 1 (the eight display-only features) is in `broadcast_pass.md` in this folder.

## What was asked

- **Feature 2, style commands:** the director picks a slap style per turn (power against accuracy, feint rate, tempo)
  from the opponent's card and condition, fed to the brain as three appended inputs.
- **Feature 3, ring navigation:** athletes walk to their corner between rounds, return to the podium, and the winner
  takes a lap.
- Scope chosen by the user: fine-tune the whole roster.

## What the code and the brains look like today

| Fact | Where | Why it matters |
|---|---|---|
| The observation is already 140 wide (interface 4: heel/toe contacts, action low-pass) | `training/constants.py`, `SlapControllerInternal.h`, `Web/sim.js` | CLAUDE.md still says 136 |
| 28 of the 30 exported brains read 136 inputs (interface 3). Only `kim_L3_v3` and `tong_L0_candidate` read 140 | `training/models/brains/*.onnx` | The interface 4 retrain is not finished |
| A brain 140 or wider runs with the 8 Hz action low-pass; a 136 brain does not | `constants.lpf_on` | Widening a shipped 136 brain past 140 changes its action rule, not only its inputs |
| The walk-in brain (S2) is commanded straight ahead to a box 1.8 to 3.0 m away | `envs/locomotion.py` | It was never trained to turn round or walk away from the podium |
| S3 (obstacle course) and S4 (the aisle lap, waypoint commands) exist as levels, with no graduated brain | `envs/locomotion.py`, no `*_S3` / `*_S4` in `models/brains` | The training side of ring navigation is written but not run |
| The walk-in is display only in Unreal: the first turn resets both to the stand pose, so `match.py` parity holds | `SlapWalkIn.cpp` | Ring navigation can follow the same rule and stay out of the referee |

## Proposal

### Feature 2: interface 5 = 140 + 3 command inputs

| Index | Input | Range | Set by |
|---|---|---|---|
| 140 | power: accuracy and balance (-1) to maximum palm speed (+1) | [-1, 1] | the director, at each turn start, striker only |
| 141 | feint: how much to sell a feint before the real swing | [0, 1] | same |
| 142 | tempo: strike early (-1) to wait late in the clock (+1) | [-1, 1] | same |

Zero for the defender and for role 0. Appended at the end with zero first-layer weights (`ppo.load(widen=True)`).

Director row (the same deterministic rule in `match.py`, `USlapController` and `sim.js`, from match state and the two
cards only, so the event logs still compare):

| Condition at turn start | power | feint | tempo |
|---|---|---|---|
| Opponent's neck below half, or striker behind on the cards | +1 | 0 | -1 |
| Opponent absorbed the last strike with a head snap under 1 m/s | 0 | 1 | +1 |
| Striker ahead on the cards and his stamina below half | -1 | 0 | 0 |
| Otherwise | 0 | 0.3 | 0 |

Training: `envs/arena.py` draws the three commands per episode and adds reward terms that pay for following them
(palm speed over 8 m/s scaled by power; the existing feint bonus scaled by feint; touch time against tempo).

**Recommendation: fold these three inputs into the interface 4 retrain that is already in flight, as interface 5.**
Otherwise the roster goes through two widenings in a row and every character is retrained twice. Affected policies:
every match brain of Grandpa, Kim and Matt (L2 to final), and Mom and Tong as they climb the ladder. The S2 walking
brains ignore the inputs.

Gates to re-run per character: the level exam for its top level, `match.py exam` (100 matches, L5), `match.py compare`
against an Unreal log, `node Web/check.mjs gg|kk|gk`, `verify_constants.py`, and `replay_check.py` (unaffected, but
it is the gate before anything ships).

### Feature 3: ring navigation on the S4 aisle-lap walker

- Train S4 per character (the level exists). Graduation as in Blueprint section 7.
- Unreal only, display only, in the turn hold after a round is scored and in the post-match hold: walk to a corner
  waypoint, turn, walk back, settle onto the stand pose exactly as the walk-in does. The next turn still starts from
  the reset stand pose, so nothing reaches the referee.
- New director rows: `RoundScored -> CornerWalk -> Settle -> TurnStart`, and `MatchOver -> VictoryLap -> Hold`. A fall
  on the way settles onto the box, as a walk-in stumble does today.
- Waypoints are fixed points in the arena frame (corner 1.5 m behind each foot box), not a NavMesh.

## Cost and risk

- GPU time: an L4 fine-tune per character plus an S4 run per character. The last full session
  (`DOCS/20261109/training_8h_report.html`) was budgeted at 8 hours for less. Unreal Editor stays closed.
- A shipped brain that passes its gates today may fail them after the low-pass comes on. That risk belongs to the
  interface 4 migration and exists with or without this proposal.
- AGENTS.md section 3 says to avoid full retraining once a baseline passes all gates. Feature 2 is a fine-tune of every
  match brain, which is close to that line.

## Decisions needed before any of this starts

1. Fold the three command inputs into the interface 4 retrain (one widening, 136 to 143), or keep them separate.
2. Whether Feature 2 is worth a fine-tune of brains that already pass their gates.
3. The GPU budget and the order of characters.
