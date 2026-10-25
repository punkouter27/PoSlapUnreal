# Pre-Match Ring Walk-In Sequence (`261025_walkin`)

- **Before (`before.png`, `-SlapWalkIn=0`):** Athletes spawn immediately in their podium footboxes at Round 1 Turn 1 under the static `Wide` broadcast shot.
- **During Walk-In (`after_walkin.png`, `t = 1.2s`):** Both athletes walk `1.60 m` down the arena aisle toward the podium over `3.0 s` (`USlapController::StepWalkIn`) with a `1.55 Hz` alternating bipedal stride (hip pitch, knee bend, ankle push-off, and counter-arm swing). `ASlapHUD::Direct` uses the new `EShot::WalkIn` low-angle tracking dolly (`FOV 54° -> 48°`) while `ASlapHUD::DrawMatch` displays the `RING WALK-IN` / `Approaching podium` banner.
- **Arrival at Podium (`after_podium.png` / `after.png`, `t = 3.6s`):** At `WalkInProgress == 1.0`, `StartTurn(m, d)` resets `mjData` to the exact canonical Round 1 Turn 1 initial state, preserving 100% deterministic event parity with `training/match.py`.

