# stun_speed calibration (2026-11-19, retrain v5 queue)

Item from `DOCS/20261113/rules2.md` ("re-read `stun_speed` after the first physics-4 L4 stage").

| | Before | After |
|---|---|---|
| `rules.json` `stun_speed` | 1.0 m/s (provisional) | 0.65 m/s |

Source: `kim_L4_v4_candidate.onnx` (best.pt of `runs/kim_L4_v4_night2_0007`, physics 4, interface 5), Kim vs Kim,
`calibrate_rules.py`, 48 seeded matches.

| Measure | at 1.0 | at 0.65 |
|---|---|---|
| Head speed in stun window | p50 0.59, p75 0.66, p90 0.71, p99 0.90 | same |
| Strikes stunning | 1 / 288 | 77 / 282 (27 %) |
| KO per landed strike | 0 % | 1 % (3 KOs, 14 knockdowns) |
| Out-of-box turn ends | 7 | 48 (stunned defenders stagger out) |

Affected policies: only physics-4 brains (rules 2). Kim L3/L4 v4 trained under 1.0 (her L4 failed the exam on
ko_rate 0); every later stage of `retrain_v5_queue` trains under 0.65. Shipped physics-1 brains: unaffected.
Open: the out-of-box rate and the low KO rate; re-read after the next L4 stages.
Checks: `gen_constants.py` regenerated `SlapShared.gen.h` and `shared.gen.js`; `verify_constants.py` pass.
