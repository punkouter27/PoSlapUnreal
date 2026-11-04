# Match events (the reference engine, `training/match.py`), 2026-11-03

One JSON object per line in `Logs/Gameplay/<YYMMDD>/match_<HHMMSS>_<first>_<n>.jsonl`. `match.py compare` checks the
events marked **compared** against Unreal's log, field for field. Everything else is a readout: Unreal and the browser
may log it or not. Fields added on 2026-11-03 are marked **new**; `compare` drops a new field from the Python side
whenever the other engine's event does not carry it yet (`match.ADDITIVE`), so adding them in Unreal or the browser
later turns the check on for them without a flag day.

| type | when | fields | compared |
|---|---|---|---|
| `match_start` | once | `first`, `rules{rounds, strike_clock, recovery}`, `seed`, `brains{red, blue}`, `jitter` (exam only) | no |
| `turn_start` | each turn | `round`, `striker`, `defender` | no |
| `feint` **new** | before the touch, each time the read fires | `t` (s), `striker`, `peak` (the palm's peak, m/s), `n` (feints so far this turn) | no |
| `strike` | the first touch of the striker's body on the defender's head | `t`, `striker`, `part` (the geom that touched), `palm` (the hand box's palm face led), `speed` (m/s, see below); **new:** `hand_speed` (the striker's right palm, m/s), `feint` (a feint was read earlier this turn), `feints` (how many) | yes (new fields: once logged by both) |
| `knockdown` | a non-sole part on the floor or podium (Blueprint 9) | `t`, `athlete`, `part` | yes |
| `foul` | out of the box | `t`, `athlete`, `why` | yes |
| `faint` | a card's faint fires | `t`, `athlete` | yes |
| `frame` | every 20 physics steps (10 Hz) | per athlete: `pelvis`, `head`, `action` | no |
| `head` | after a touch | `athlete`, `peak_g`, `peak_rad_s2` (5 ms differences) | no |
| `strike_result` **new** | at the end of a turn with a touch, before `turn_end` | `t` (the touch), `striker`, `defender`, `part`, `speed`, `head_peak` (the defender's head snap, m/s, peak over the recovery window), `feint`, `stagger` (COM > 5 cm off at the end), `ko` | no |
| `realism` | each turn | `striker`, `touched`, `jerk`, `chain_ms`, `slide_m` | no |
| `turn_end` | each turn | `round`, `striker`, `touch`, `part`, `palm`, `speed`, `head_peak`, `stagger`, `ko`, `end`, `fouls` | yes |
| `condition` | after each turn | per athlete: `[stamina, neck strength]` | yes |
| `round` | after each round | `round`, `cards` (3 judges, red/blue) | yes |
| `result` | once | `how`, `winner`, `round`, `cards` | yes |

## The three numbers the other streams asked for

* **Head snap peak** - `strike_result.head_peak` (and `turn_end.head_peak`): the fastest the defender's head moved
  (m/s, 5 ms differences) between the touch and the end of the recovery window. It cannot ride on the `strike` event,
  which is written at the touch, before the snap has happened; `strike_result` carries it with the rest of the
  strike's outcome, keyed by the same `t`.
* **Hand speed** - `strike.hand_speed`: the striker's right palm (`hand_r`) at the touch, m/s. `strike.speed` is
  the same number on physics 1; on physics 2 (`models/v2`) it is the speed of the part that touched.
* **Feint** - `strike.feint` / `strike.feints` and the `feint` events, by the rule Unreal's HUD reads a swing with
  (`SlapHUD.cpp` F10), with its thresholds in `training/constants.py`:

| constant | value | meaning |
|---|---|---|
| `FEINT_MIN_PEAK` | 2.5 m/s | the palm's peak must pass this |
| `FEINT_AFTER_S` | 0.12 s | at least this long after the peak ... |
| `FEINT_PULLBACK` | 0.55 | ... the palm is below this share of the peak, with no touch: a feint |
| `FEINT_REARM`, `FEINT_REARM_S` | 0.95, 0.2 s | a swing back up to 95 % of the peak, 0.2 s after it, re-arms the read |

The reference engine samples the palm speed once per brain step (50 Hz) from the turn start until the touch; the
HUD samples the display's frame. Both use the same thresholds, so a feint that both see is the same feint; one that
lives between two brain steps can be read by the HUD alone. The wind-up's own backswing often trips the read just
before a fast slap (the palm peaks, slows to reverse, then lands): `strike.feint` is true for most landed strikes of
the shipped brains, which is what the HUD shows too.

## Physics 2 and the strike speed

`strike.speed` and the impact impulse (card `strike_mass` x velocity over 10 ms) use, on physics 2, the velocity of
the part that touched (the left hand, a forearm), not `hand_r`'s. The engine reads the version from the fingerprint
(`"physics": 2`). Unreal (`USlapController`) and `Web/sim.js` still use `hand_r`'s on every model: they need the
same switch before a physics-2 brain ships (DOCS/20261103/training_realism.md).
