# UU_AGENT (agents.md)

Three parts: **1. Coding standard** (the UU_AGENT rules, repo notes, CI/CD and the Po Game Standard),
**2. Concurrent work**, **3. Verify the repo** (exact commands).

## 1. Coding standard

Scope: RL-trained humanoids and animals with realistic motion on Earth, deployed exclusively in Unreal Engine using
MuJoCo Warp / URLab.

#### 1. Workflow & Setup
- **Target Engine:** Unreal Engine exclusively. Built-in engine physics (Chaos) is strictly disabled; UE5 serves solely
  as an audiovisual rendering, camera tracking, and input layer.
- **Git:** Work on `master`. Commit before syncing or pulling.
- **Automation:** Set up levels, actors, and MJCF bindings via CLI and headless Blender. UI changes require
  before/after screenshots and an HTML report in `SCREENSHOTS/`.
- **Build stamp:** Render the build date (`YYMMDD`) in the bottom-right corner.
- **Paths & Toolchain:**
  - Raw assets: `Content/Art/Raw/`
  - Exported assets / MJCF: `Content/Art/Exports/`
  - Physics runtime: MuJoCo C++ Plugin / URLab runtime (no Chaos)
  - Policy inference: UE5 NNE / ONNX Runtime via Native C++
  - Editor settings: Disable "Use Less CPU when in Background"

#### 2. Physics & Transform Conventions
- **Coordinate & Unit Space:** UE5 uses Z-Up, Left-Handed coordinates in centimeters. MuJoCo / URLab uses Z-Up (or
  Y-Up depending on model XML), Right-Handed coordinates in meters. Apply an explicit 100× unit scale factor and axis
  transformation in the bridge layer.
- **Deterministic Allocation:** Pre-allocate all character skeletal meshes, ground collision surfaces, and props from
  simulation definitions at startup. Dynamic runtime spawning or actor destruction is forbidden.
- **Collision:** Primitive colliders only (capsules, spheres, boxes). Non-adjacent bodies collide. Zero penetration at
  rest, at joint limits, and during active motion.
- **Fidelity:** Gravity −9.81 m/s², anatomical mass distributions, strict torque, velocity, and joint-limit envelopes.
  Visual skeletal bones strictly mirror physics body transforms.
- **Smooth Motion:** Policy rewards must enforce action-rate/jerk penalties (or low-pass filtering) to prevent
  high-frequency jitter.

#### 3. Policy Architecture & Hand-Overs
- **Hierarchy:** Lean policies divided by motor skill family (Level 0 balance, locomotion, task skills). Variances are
  handled via command vectors (target velocity, gait style), not separate networks.
- **Directors:** Layered rule-based state machines (game flow → body state → skill selector) switch policies based on
  physical sensors (contact flags, center of mass, foot slip). Policies never switch or invoke each other directly.
- **Hand-Overs:** All policies for a given character share the observation space, joint ordering, action layout, step
  rate (dt), and style rewards. Preload all policies at level initialization. Carry over the last action vector; never
  snap poses or blend bone transforms. Train recovery/get-up skills starting from real simulated falls.
- **Interface Stability:** Append new observation features only at the end with zero-initialized weights; never
  reorder or delete inputs. Breakages require policy distillation.
- **Isolation:** Each character trains from scratch with its own policy weights and character card traits. No policy
  sharing across different character bodies. Avoid full retraining once a baseline character passes all gates.
- **Audit Trail:** Log all affected policies, state machine rows, and interface revisions in `/docs` prior to
  modifications. Re-run task and kinematic regression benchmarks after any update.

#### 4. Training, Gates & Optimization
- **Execution & Resources:** Close the UE5 editor during headless training runs exceeding 30 minutes or when available
  system RAM drops below 4 GB. Save and commit state beforehand.
- **Cleanup:** Purge failed runs, short checks (<5,000 steps), and redundant checkpoints. Never delete a run modified
  within the last 24 hours or one tied to an active training queue.
- **Verification Gate:** Export the final ONNX model only after holding target rewards for 100 consecutive episodes
  with zero NaN gradients. Validate a 5-second rollout ensuring:
  1. *Kinematics:* Root velocity, angular momentum, and joint angles match the MuJoCo/URLab baseline.
  2. *Contacts:* No foot sliding, ground clipping, or contact chatter.
- **Telemetry:** Run TensorBoard alongside all runs. Output training metrics to `/docs/<YYYYMMDD>/`.
- **Runtime Profiling:** Enforce zero heap allocation (no dynamic allocations) inside the real-time C++ inference loop
  to guarantee consistent frame budgets.
- **Reporting:** Runs over 30 minutes require an HTML report with convergence curves and run-over-run deltas. Provide
  hourly progress summaries.

#### 5. Communication & Phased Interaction Protocol
- **State Header:** Prefix all task execution responses with `**Current Phase: Step X of 5**`.
- **Approval Protocol:** Stop completely at each designated `[STOP]` gate. Do not output code or subsequent phases
  until explicit user approval is provided.
- **Large Changes:** For code modifications exceeding 100 lines, provide 3 architectural/roadmap questions before
  implementation.
- **Summary:** Responses exceeding 100 words must conclude with a concise ~20-word TL;DR.
- **Issue Resolution:** Conclude technical troubleshooting with a direct proposed resolution for the primary open
  issue and await confirmation before applying it.

### Repo notes (PoSlapUnreal)

- **Engines:** three engines step the same MuJoCo physics and read the same generated numbers: the Python trainer /
  reference (`training/match.py`), the Unreal game (`Source/PoSlapUnreal`, URLab in
  `Plugins/UnrealRoboticsLab`: upstream + `Plugins/urlab_poslap.patch`, recreated by `scripts/setup_urlab.ps1`), and the browser port "PoSlap Arena" (`Web/`, MuJoCo WASM + onnxruntime-web).
  `training/constants.py` + `training/rules.json` are the source; `training/gen_constants.py` writes
  `Source/PoSlapUnreal/SlapShared.gen.h` and `Web/shared.gen.js` (`--check` in CI).
- **MuJoCo is pinned to 3.10.0** in all three engines (`training/requirements.txt`, `Web/package.json`, URLab's
  MuJoCo gitlink in `Plugins/urlab_poslap.patch`). 4.0.0 was evaluated on 2026-11-20 and changes match outcomes (the shipped brains KO a round earlier on the
  same seeds; `build_body.py`'s A12 fingerprint gate fails), so the upgrade waits for a retrain + re-gate.
- **Physics & sim:** MuJoCo / URLab with Chaos disabled; gravity -9.81 m/s^2; seed via `-SlapSeed=<N>`.
- **Controls:** `F1` mode (Watch / Play / Testbed), `F2`/`C` camera, `F3`/`Tab` telemetry + diagnostics,
  `F4` hot-swap striker brain, `Space` skip replay / pause, `R` rematch.
- **Contracts:** `USlapController::AppendZeroInitInputs` appends zero-initialised tail inputs without reordering;
  `RunNet` counts and sanitises non-finite outputs (`docs/BRAIN_IO.md`).
- **Reward config:** penalty weights and curriculum thresholds live in `training/configs/reward_config.json`; the trainer
  reads them and the Unreal DataAsset mirrors them (see `docs/reward_config.md`). Each penalty term is logged on its
  own (`reward_config.TermLog` -> `pen_torque_sq`, `pen_joint_ang_vel_sq`, `pen_action_rate_sq`, `pen_foot_slip` in
  TensorBoard and `telemetry.jsonl`, once per iteration).
- **Web port checks:** the console prints `[policy] <brain>.onnx ... inference in the browser` on each corner's first
  decision (then once a minute); TAB shows fps, inference ms, the critics' reward estimate, stage and mode on an opaque
  panel; demo mode's idle clock is `Web/idle.js` (frames > 0.25 s ignored), tested by `Web/idle.test.mjs` through
  pytest (`tests/test_web_idle.py`); `/diag` caches its results 60 s per tab and build (`?fresh` re-probes).
- **Folders:** `Content/Art/Raw/`, `Content/Art/Exports/`, `docs/`, `scripts/`, `SCREENSHOTS/`, `training/`, `Web/`.
- **Content layout (Po Game Standard, Unreal):** every asset lives under `/Game/Project/`: `Agents/Athletes`,
  `Core/{Audio,FX,Cinematics}`, `DataAssets` (`DA_RewardConfig`, `DA_ShowcaseConfig`, selected in `Config/DefaultGame.ini`),
  `Environments/{Arena,Levels}`, `RL_Policies/Brains`; `UI/` has no assets (the HUD and overlay are C++ Slate). Move
  assets only through the editor (AssetTools rename + redirector fix-up), never with git or the file system.
  `Content/Art/` (raw files and exports) and `Content/Data/` (staged brain binaries and cards) are files, not assets.
- **URLab is not tracked** (its own git repo, ~2.3 GB with build output; owner decision 5ff2b5c): `Plugins/UnrealRoboticsLab`
  is recreated from upstream + `Plugins/urlab_poslap.patch` by `scripts/setup_urlab.ps1` (clone, patch, third-party
  build); that one script is what makes a fresh clone build.
- **Gaze:** real eye contact through the neck joints (`neck_turn` goal servo: `match.py gaze_*`, Unreal `ApplyGaze`,
  `sim.js`), not a render-only look-at; `tests/test_physics_contract.py` checks it with the rest of the physics contract.

### Deliberate deviations

| Deviation | Why | Commit sha / date | How to undo |
|---|---|---|---|
| URLab (`Plugins/UnrealRoboticsLab`) is git-ignored and recreated by `scripts/setup_urlab.ps1` (upstream + `Plugins/urlab_poslap.patch`). Not a deviation from v3: vendoring is not required, a fresh clone builds after that one script | owner decision: a 2.3 GB clone with build output | `5ff2b5c` (owner) 2026-11-21 | track the plugin again |
| `Content/Art/` (raw files, exports) and `Content/Data/` (staged brain binaries, cards) stay outside `/Game/Project` | files loaded by path, not assets | `cc7eb10` 2026-11-22 | — |
| MSVC C4005/C4305 from URLab/MuJoCo headers silenced only around those includes (`SlapMuJoCo.h`); URLab's own sources still warn | third-party code, not tracked here | `cc7eb10` 2026-11-22 | — |
| `training/parity_check.py` is `continue-on-error` in `deploy.yml`; it fails 5/6 today (rules 2, Grandpa vs Kim diverges at event 4) | owner design: blocking after the first green run | — | make it blocking once it passes |
| Scripted HUD checks (`-SlapUiTest` etc.) suspend demo mode | their synthetic clicks bypass the idle clock; demo mode took the screen mid-check | `cc7eb10` 2026-11-22 | — |
| The Unreal window keeps its own `POSLAP` title and build stamp; the web page shows them once, outside the canvas | a desktop game has no page (UU_AGENT build-stamp rule) | `cc7eb10` 2026-11-22 | — |

**Hosting:** Azure Static Web App `poslap-arena`, Free tier. Never run test suites against the live site; live checks =
at most 6 page loads + /diag + X-Commit.

### CI/CD and Azure

- A push to `master` runs `.github/workflows/deploy.yml` (the only workflow): Python checks (`ruff check`,
  `ruff format --check`, `pytest`) -> browser-engine checks -> `Web/build.py` -> deploy `Web/dist` to the Azure Static
  Web App -> live check that every response carries `X-Commit: <this commit>` (`Web/build.py` writes it into
  `staticwebapp.config.json`), plus `/`, `/Web/` and `/diag`. Nothing is deployed by hand. Live: https://ashy-plant-0f75ec30f.3.azurestaticapps.net
- The Unreal project is built locally (UE 5.8 is not available on GitHub runners); it must compile from a fresh clone.
- Tests pass on any machine: no drive-letter, temp-folder or working-directory assumptions, and tests never write
  into tracked files.

### Po Game Standard (Unity / Unreal RL games)

This repo is one of several independent Po games. It follows the conventions below, but it owns every file:
nothing is generated from, synced with, or required to stay identical to another repository (no "same file in
every game" headers, no sync scripts that copy into other repos). Third-party code it needs (ML-Agents, MuJoCo
plugins, URLab) is pinned INSIDE this repo (URLab: upstream + this repo's patch, recreated by
`scripts/setup_urlab.ps1`; the 2.3 GB clone itself is not tracked). Azure resources may be shared through `PoShared`.

#### Repo skeleton
```
README.md   AGENTS.md (this standard + repo notes)   CLAUDE.md ("Read AGENTS.md.")
docs/       lowercase, all prose (no DOCS/, no root DESIGN.md/SPEC.md/tasks.md — move them into docs/)
scripts/    build/publish/helper scripts (Unity: Tools/ is renamed scripts/ unless Unity itself needs it)
.github/workflows/<one file>.yml   the ONLY workflow
pyproject.toml with a [tool.ruff] section for any Python in the repo
```
No absolute local paths (C:\Users\...) committed anywhere. No build outputs or node_modules tracked.

#### Web page around the game (WebGL template / web viewer)
- The page chrome is drawn ONCE, by the HTML page, OUTSIDE the game canvas, so it can never overlap the HUD:
  a 44px top bar (game name top-left, settings gear / fullscreen top-right) and the version `vYYMMDD` bottom-right
  in the bar's counterpart or a 20px footer strip. The canvas fills the remaining viewport.
- The in-game HUD does NOT repeat the game name or version (it may show them inside menus, not as corner chrome).
- Same tokens as the web apps: dark background `#0b0f17`, surface `#121826`, text `#e6e9f2`, muted `#9aa3b5`,
  accent `#6366f1`, Inter/system-ui font. Text ≥ 4.5:1 contrast.

#### Orientation (C11)
The canvas fills any viewport (no fixed portrait aspect, no pillarboxing). UI is anchored and DPI-scaled
(Unity: CanvasScaler "Scale With Screen Size" or UI Toolkit PanelSettings scale-with-screen; Unreal: UMG anchors +
DPI curve). Must be checked at 1600x900 (landscape) and 450x800 (portrait) — screenshots in the PR/commit notes.

#### Version
One stamp, UTC `yyMMdd`, computed once by the publish/build script and injected into: index.html, diag.html,
and the player (`PlayerSettings.bundleVersion` / Unreal ProjectVersion) so `Application.version` matches the page.

#### /diag (games)
`/diag` (static `diag.html` generated by the publish script) titled **System status**, same layout as the web apps:
overall banner, then **API / Data / Services** sections. It must LIVE-probe (fetch from the browser, with latency)
every external dependency the game really calls (leaderboard tables/functions, weather APIs, AI APIs, replay bundles,
model files), plus the WebGL2 capability and that the build bundle files exist. Nothing hard-coded as "Ready".
The checks are listed in the repo's own config (e.g. `scripts/webgl.json` → `diagChecks`).

#### In-game diagnostics overlay (C12)
`Tab` (and a 44px corner button on touch devices) toggles an overlay showing: FPS, current episode reward,
inference latency (ms, rolling average), curriculum stage. Readable text (≥ 4.5:1 on an opaque panel).

#### Demo mode (C13)
After 30 s with no input (keyboard, mouse, touch, gamepad) from load or from last input: hide non-essential HUD,
all agents are driven by the trained policy (inference only), showcase cameras cycle (~6–8 s each); any input
returns control and the HUD. Idle threshold, camera list and dwell time are editor-tunable assets.

#### Rewards + editor-first config (C9, C10)
Reward includes penalties for joint torque², joint angular velocity², action rate ‖a_t − a_{t−1}‖², and foot sliding
(foot planar speed while in contact), each with its own weight. Reward weights, curriculum thresholds, cameras and UI
references live in ScriptableObjects / `[SerializeField]` (Unity) or DataAssets / `UPROPERTY(EditAnywhere,
BlueprintReadWrite)` (Unreal); Python trainers read the same values from the repo's training config. Changing a
penalty does not retrain shipped policies — note this in docs when shipped models predate the change.

#### Workflow
- Unity: the WebGL player is built locally with the EXACT pinned editor (Unity cannot be licensed in CI) by
  `scripts/publish_webgl.ps1`, which runs EditMode tests first, then builds, zips and publishes a `webgl-*` release.
  The one workflow: on release → deploy to the game's Static Web App → check the live page AND /diag; on push →
  Python checks (`ruff check`, `ruff format --check`, `pytest`) only.
- Unreal: the one workflow builds and deploys the repo's web front (viewer / showcase + /diag) to its Static Web App,
  after Python checks. The Unreal project must compile from a fresh clone after `scripts/setup_urlab.ps1`.

#### Tests
Python: real pytest tests (collected by pytest, not ad-hoc scripts), ruff clean. Unity: EditMode tests pass before
every publish. Tests never write into tracked files.

## 2. Concurrent work

- One engine at a time: close the Unreal editor before a commandlet, a `-game` check or a browser test, and never run
  two `UnrealEditor*` processes on this project (they lock the same packages and DDC). Kill what you started.
- Training runs (`training/`, GPU) and Unreal runs compete for the same GPU: check `nvidia-smi` and the night queue
  (`training/night_driver.py`, `queue_task.ps1`) before anything heavy; never delete a run modified in the last 24 h.
- `git status` before you start; commit only your own files (never `Saved/`, `Intermediate/`, `Binaries/`,
  `Plugins/UnrealRoboticsLab/`, `training/runs/`, `Web/dist/`, screenshots outside `SCREENSHOTS/`). Pull/rebase before
  pushing; never force-push.
- Asset moves touch many `.uasset` files at once: do them in their own commit while nobody else has the editor open.
- Live site: see **Hosting** in section 1 for the budget. Run the full checks locally against `Web/dist` (the
  artefact that deploys).

## 3. Verify the repo

One command sequence verifies the repo (ruff, pytest, then `verify_all.ps1`: cards, constants, naming, physics audit,
browser engine, live relay, spectator API, web build, CUDA realism, and with `-Unreal` the editor build, UI test and
Showcase automation; run `scripts\setup_urlab.ps1` once per clone first):

```
ruff check . && ruff format --check . && python -m pytest -q && powershell -ExecutionPolicy Bypass -File scripts\verify_all.ps1 -Unreal
```

Last full run (2026-11-23): pytest 23 passed (incl. the Node demo-clock test); naming 815 assets, 0 errors; browser
checks pass; `parity_check` 5/6 (non-blocking); build 0 warnings in `Source/PoSlapUnreal`; PoSlap.Showcase 3/3 on D3D12.

The steps one by one, from the repo root (Python 3.12 venv with `training/requirements.txt`, or the CI set:
`pip install "mujoco==3.10.0" numpy onnxruntime pytest ruff`):

```
ruff check .
ruff format --check .
python -m pytest -q                                   # tests/ (cards, models, constants, a seeded match, rewards, physics contract)
python scripts/check_naming.py                        # asset names + the /Game/Project layout
cd Web; npm ci; cd ..
foreach ($p in "gg","kk","gk") { node Web/check.mjs $p }; $env:SLAP_RULES=2; node Web/check.mjs gk; Remove-Item Env:SLAP_RULES
node Web/live_check.mjs; node Web/api_check.mjs
python training/parity_check.py --stun 0.55           # Python vs browser (non-blocking in CI, see deploy.yml)
python Web/build.py                                   # Web/dist + /diag + X-Commit header
python -m http.server 8731 --directory Web/dist       # local check: http://127.0.0.1:8731/Web/ and /Web/diag.html
```

Unreal (UE 5.8; once per clone: `powershell -ExecutionPolicy Bypass -File scripts\setup_urlab.ps1`; close the editor first):

```
& "C:\Program Files\Epic Games\UE_5.8\Engine\Build\BatchFiles\Build.bat" PoSlapUnrealEditor Win64 Development `
    "-Project=$PWD\PoSlapUnreal.uproject" -WaitMutex                          # 0 warnings in Source/PoSlapUnreal
& "C:\Program Files\Epic Games\UE_5.8\Engine\Binaries\Win64\UnrealEditor-Cmd.exe" "$PWD\PoSlapUnreal.uproject" `
    "-ExecCmds=Automation RunTests PoSlap.Showcase;Quit" -unattended -nosplash   # 3 tests, "Test Completed. Result={Success}" (rendered: never -nullrhi)
# the HUD's own checks need a real renderer (they run from DrawHUD; -nullrhi never draws, so they never start):
& "C:\Program Files\Epic Games\UE_5.8\Engine\Binaries\Win64\UnrealEditor-Cmd.exe" "$PWD\PoSlapUnreal.uproject" `
    -game -windowed -ResX=1600 -ResY=900 -SlapUiTest -unattended -nosplash      # "UI test PASS"
# 16:9 and 9:16 runtime check (overlay + benchmark, demo mode screenshots into <dir>, then quits):
& "C:\Program Files\Epic Games\UE_5.8\Engine\Binaries\Win64\UnrealEditor.exe" "$PWD\PoSlapUnreal.uproject" `
    -game -windowed -ResX=1600 -ResY=900 -ShowcaseCapture=<dir> -ShowcaseTouch -ShowcaseBenchmark   # and -ResX=450 -ResY=800
```

Live (after a push to master): the workflow run ends with `Live: <url> at X-Commit <sha> ...`.
