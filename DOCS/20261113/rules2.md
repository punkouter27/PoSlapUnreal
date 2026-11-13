# Rules 2, physics 4 and the generated constants (2026-11-13)

The audit record AGENTS.md asks for: what changed, which policies it affects, and what was and was not checked.
Nothing here changes the shipped game: every new rule is switched on by a fingerprint's `"physics": 4`, and all
shipped models and brains are physics 1.

## What changed

| Area | Before | Now |
|---|---|---|
| Shared numbers | typed three times (Python, C++, JS), checked by string search | `training/constants.py` + `training/rules.json` are the source; `gen_constants.py` writes `Source/PoSlapUnreal/SlapShared.gen.h` and `Web/shared.gen.js`; the engines read those |
| Match rules | constants inside `match.py`, `SlapMatch.cpp`, `sim.js` | `training/rules.json` |
| Physics versions | 1 (shipped), 2, 3 | + 4: the physics-3 bodies, unchanged, under rules 2 (`training/models/v4/`) |
| Brain I/O document | removed from the tree | `DOCS/BRAIN_IO.md` restored; `constants.LAYOUT` is the same table as data |
| CI | deploy only | `.github/workflows/verify.yml` restored, plus the generated-copy check and Python-vs-browser parity |
| Brains folder | 52 files | 32: 21 files nothing referenced were removed with `git rm`, `kim_L3_v3.pt` rebuilt as a seed |

## Rules 2 (from physics 4)

| Rule | Rules 1 | Rules 2 | Number (`rules.json`) |
|---|---|---|---|
| The blow | contact + `strike_mass` × part velocity as a 10 ms impulse on the head | the contact alone | - |
| Knockout model | defender down after the strike = KO | head speed peak in the first 0.25 s ≥ threshold × card `chin` → every motor at 25 % for 1 s × severity | `stun_speed` 1.0 m/s (provisional), `stun_window_s`, `stun_strength`, `stun_s`, `stun_max` |
| Knockdown after the strike | any part but a sole on floor or podium | a part on the floor, or the pelvis under 85 % of its stand height; then a 10 s count | `count_s`, `up_hold_s`, `up_height` |
| Beating the count | - | up for 1 s: the turn ends, the round is 10-8 against him. Not up in 10 s: KO | |
| While down | - | his get-up brain (`<char>_G1[_v<n>].onnx`) drives him if he has one (Python; a hook in Unreal and the browser) | |
| Fouls | clock, knocked down, down before the strike, out of the box | + flinch (head moves 12 cm while the palm swings), + clubbing (first touch is not a palm, either hand) | `flinch_m` |
| Judges | fixed weights | each weight × (1 ± 15 %) per round, from the match seed | `judge_noise` |
| Damage | stamina, neck strength | + a hurt neck sees the hand up to 4 decisions later | `daze_react` |

No observation index, width, joint order, action layout or step rate changed (interface 4, 140 inputs).

## Affected policies

| Policy | Effect |
|---|---|
| Every shipped brain (physics 1) | none: rules 1 match logs are identical before and after (3 seeded matches, 1,362 events) |
| Any brain trained on physics 4 | trains and plays under rules 2; none exists yet |
| Grandpa (shipped match brain) | under rules 2 none of 192 strikes was palm-led: all would be clubbing fouls. He needs L2 again |
| Kim (shipped match brain) | 22 of 288 strikes clubbing under rules 2 |
| Get-up (G1) | now has a use in the match (the count); none graduated |

Director rows added (match engine): `count running and a get-up brain present` → the get-up brain decides with the
role-0 observation, last action carried; `count over` → the turn ends.

## What was measured (shipped brains, physics 1, rules 2 forced with `SLAP_RULES=2`)

- Head speed without the kick: p50 0.77, p99 1.07 m/s (with the kick: p50 2.29, p99 3.45). In the stun window: p50 0.65, p99 0.83.
- So `stun_speed` 1.0 stuns nobody today and KOs fall from 15 % of strikes to 0 %. This is the main open risk of
  item 7: whether brains trained under rules 2 learn to hit harder is not known. `calibrate_rules.py` prints these
  percentiles; re-read `stun_speed` after the first physics-4 L4 stage.
- Head movement during the swing: Kim ≤ 9 cm, Grandpa ≤ 3 cm, so `flinch_m` 0.12 calls no flinch on them.
- Decisions with judge noise (48 matches): 33 unanimous, 9 majority, 2 split, 4 on tiebreaks.

## Checks

| Check | Result |
|---|---|
| Rules 1 unchanged, Python (seeded logs before vs after) | identical |
| Rules 1 unchanged, browser (`check.mjs gk` before vs after) | identical event lines |
| Python vs browser, rules 1 (`parity_check.py`) | 2/2 |
| Python vs browser, rules 2 | 2/2 exact |
| Python vs browser, rules 2 with stuns and counts (`--stun 0.55`) | 2/2 with the post-stun tolerance (a slack fall differs by a brain step between ONNX runtimes) |
| `verify_constants.py`, `verify_cards.py`, `node Web/check.mjs gg|kk|gk` | pass |
| Unreal build and `match.py compare` | see the session report; not recorded here |
| GPU training under rules 2 | smoke runs only; no brain trained |

## Not done

- No training was run. The queue is `training/configs/retrain_v5_queue.json` (19 stages, 23 h).
- The page was not opened in a browser after the `app.js` import change (syntax-checked only); the ten-count overlay,
  a stun indicator and the strike effects' scaling still follow rules 1.
- Unreal has no physics-4 level: `Scripts/build_scene.py` would need the v4 arena imported.
