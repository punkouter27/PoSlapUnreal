# Director transition table (spec, item 44, 2026-11-04)

AGENTS.md §3: rule-based directors in layers (**game flow → body → skill**) switch policies on physical state
(contacts, centre of mass, falls) through **one** transition table, mirrored in every runtime (`match.py`,
`USlapController`, `Web/sim.js`). Policies never pick each other. Hand-overs pass the last action on: the incoming
brain's goal = the current goal, its previous action (inputs 70-99) = the outgoing brain's last action; no pose is
snapped and outputs are never blended.

**Status: spec only.** Runtime switching in Unreal and the browser is **not in scope until a get-up brain (G1)
graduates**; today a knockdown still ends the turn (Blueprint 9) and the match resets both athletes. The training
side is ready: `envs/getup.py` (G1) trains from real knockdown states with the same observation, joint order, action
map and timing as the match brain.

## Layer 1: game flow (match.py's turn machine, unchanged)

| State | Enters when | Leaves to | On |
|---|---|---|---|
| `walk_in` | match start (opt-in `-SlapWalkBrain`) | `turn` | both pelvises within 0.15 m of their box centres, or 6 s |
| `turn` | coin toss / roles swap | `recovery` | first legal face touch (face zone), or 30 s clock, or knockdown |
| `recovery` | touch | `turn_end` | 1.5 s after the touch (match) / KO window |
| `turn_end` | clock, recovery over, knockdown | `turn` (roles swap) or `match_end` | reset to the stand pose (today) |
| `match_end` | 3 rounds | - | judges' cards |

## Layer 2: body (per athlete; new)

Inputs every decision (50 Hz), all from MuJoCo: `down` = the knockdown rule (a non-sole geom on floor/podium; the
defender's palms on the podium excepted), `up` = pelvis ≥ 0.85 × stand height AND pelvis z-axis · world up ≥ 0.9 AND
only the soles touch floor/podium (`envs/getup.py` UP_*), held `UP_HOLD` = 50 decisions (1 s); `com_e` = horizontal
|(CoM − mid-feet) − stance offset| (`envs/common.com_recovery`); `t_state` = decisions in the current body state.

| # | From | To | Condition (all must hold) | Brain after | Notes |
|---|---|---|---|---|---|
| B1 | `upright` | `down` | `down` | get-up (G1) | the knockdown is scored first (layer 1 logs it); hand-over: last action, current goals |
| B2 | `down` | `rising` | G1 brain loaded AND game flow allows a get-up (layer 1: `recovery` or a get-up window, never mid-`turn` for the striker) | get-up (G1) | role input 0, own foot box as the target (BRAIN_IO 102-104) |
| B3 | `rising` | `upright` | `up` for 1 s AND `com_e` ≤ 0.05 m | the layer-3 skill | the L3 criterion; hand-over: last action, current goals |
| B4 | `rising` | `down` | `down` again after `up` had held ≥ 0.5 s | get-up (G1) | a re-fall; counts as a second knockdown |
| B5 | `rising` | `out` | 10 s without B3, or 2 re-falls | none: layer 1 resets the athlete (today's behaviour) | the fallback when the get-up fails |
| B6 | `upright` | `walking` | layer 1 is `walk_in` | walk-in (S2/S5) | |
| B7 | `walking` | `upright` | pelvis within 0.15 m of the box AND both soles in contact (heel or toe bits, 136-139) | match brain, role 0 | today's walk-in end (`SlapWalkIn.cpp` settle) |

## Layer 3: skill (per athlete, unchanged: one match brain, role input)

| Layer-1 state | Striker | Defender |
|---|---|---|
| `turn` | match brain, role +1 | match brain, role −1 (absorb) or 0 (guard, older brains) |
| `recovery` | match brain, role +1 (stick the landing) | match brain, role −1 |
| `walk_in` | walk brain (S2), role 0 | walk brain (S2), role 0 |

## Mirroring

One table, three runtimes: Python (`match.py`: a `Director` with the rows above), Unreal (`USlapController`:
`StepDirector` on the physics thread before `Decide`), browser (`sim.js`: `director()` before the brains run). The
table's thresholds go into `training/constants.py` with mirrors checked by `verify_constants.py`, like every shared
number. A parity case for `match.py compare`: a scripted shove that knocks an athlete down, the get-up, the B3
hand-back, and the same events logged in all three.
