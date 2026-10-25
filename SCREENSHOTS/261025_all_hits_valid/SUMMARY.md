# Any hit is valid (261025)

Rule change (user, 2026-10-25): *"if its a open palm hit or not. it does not matter. all hits are valid"*.
The palm is now **technique**, not validity: a back-of-hand or wrist touch that lands on the face counts as a
valid strike and scores, exactly like an open-palm one. The "clubbing (not the palm)" foul is gone.

## UI change (before → after)

| Shot | Before | After |
|---|---|---|
| `web_before_non_palm_foul.png` | Banner **"Foul: not the palm"**, strike line `13.2 m/s · hand (foul)`, −1 point on every card | — |
| `web_after_non_palm_hit.png` | — | Banner **"Hit"**, sub-line `13.2 m/s · not the palm`, strike line `13.2 m/s · hand`. No foul, the hit scores. |

Same matchup (Grandpa vs Grandpa), same 13.2 m/s non-palm contact — only the judging changed.
The technique is still reported (so the commentator and the HUD can say "not the palm"), it just no longer
costs the striker a point.

## Changed in all three runtimes (the parity rule)

| File | Change |
|---|---|
| `Web/sim.js` | dropped the `clubbing (not the palm)` foul; the judges' effect now counts any touch (`r.touch !== null`, no `r.palm` gate) |
| `training/match.py` | same two edits in the CPU referee (training = the rule of record) |
| `Source/PoSlapUnreal/SlapController.cpp` | same two edits in the Unreal referee; HUD now says "Hit" instead of "Foul: not the palm" |
| `training/envs/slap.py`, `training/envs/arena.py` | the strike reward now pays for any touch (`clean = hit & speed`), not only a palm-led one |
| `training/levels.py`, `training/train.py` | L2 pass criterion wording: "hit = any touch, >= 8 m/s" |

## Verification

- `node Web/check.mjs` — a non-palm strike now reports `"palm":false` with `"fouls":[]` and still ends the turn as a scored hit.
- `training/.venv/Scripts/python.exe training/match.py play models/brains/grandpa_L4.onnx` — full 3-round match, no clubbing fouls, KO logic intact.
- Unreal: `PoSlapUnrealEditor` rebuilt clean (SlapController.cpp compiles, editor binary relinked).
- Browser (local + Azure): non-palm contact shows "Hit" and scores; palm contacts still show "Clean palm".

The existing ONNX brains need no retraining — they already land open-palm hits, which remain valid; the change only
relaxes what counts as a hit. The reward-side change affects **future** training runs.
