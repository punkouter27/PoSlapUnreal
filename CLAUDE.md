# PoSlapUnreal — Claude / Agent Reference

See [AGENTS.md](./AGENTS.md) for the authoritative project specification and physics/training gates.

## Standard Profile B Conventions
- **Physics & Sim**: MuJoCo / URLab C++ plugin with Chaos disabled; −9.81 m/s² gravity and 0.002 s substeps synchronized via character fingerprints; selectable seed (`-SlapSeed=<N>`).
- **UI & Theme**: Shared theme tokens live in `Source/PoSlapUnreal/UiTheme.h` (`SlapUiTheme`), defining shared spacing, corner radii, safe padding, `48dp` minimum touch targets, policy role colors (`PolicyBot`, `PolicyRef`, `PolicyCustom`), and five HUD anchors including bottom-right `YYMMDD` build stamp.
- **Controls & Cameras**:
  - `F1`: Cycle mode (`Watch` / `Play` / `Testbed`)
  - `F2` / `C`: Cycle camera (`Auto`, `Wide`, `Side`, `Close`, `Follow`, `Orbit`, `Free` — WASD/Arrows pan in `Free`)
  - `F3` / `Tab`: Toggle telemetry & debug panel
  - `F4`: Hot-swap striker brain policy
  - `Space`: Skip active replay or toggle pause
  - `R`: Reset / rematch
- **Observation & Action Contracts**: `USlapController::AppendZeroInitInputs` appends zero-initialized tail inputs without reordering; `RunNet` tracks `NanInfCount` and sanitizes non-finite outputs.
- **Folders**: `Content/Art/Raw/`, `Content/Art/Exports/`, `DOCS/`, `SCREENSHOTS/`, `training/`.
