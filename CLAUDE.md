# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@AGENTS.md

`BLUEPRINT.md`, `tasks.md`, the dated `DOCS/` folders and `SCREENSHOTS/` were removed from the tree on 2026-11-13
(commit 4e8308e); they are in git history at `cb01401`. `DOCS/BRAIN_IO.md` (the brain input/output contract) was
restored the same day; its table is also data (`constants.LAYOUT`). New dated reports go in `DOCS/<YYYYMMDD>/`,
created when needed (`DOCS/20261113/rules2.md`: rules 2, physics 4, the generated constants).

## Architecture: one match, three engines

The same rules and the same brain I/O are written three times. A change to rules, observations, actions, timing
or fatigue has to land in all three, or the parity checks below break:

| Engine | File | Role |
|---|---|---|
| CPU MuJoCo (Python) | `training/match.py` + `training/brain_cpu.py` | **Reference.** Referee, judges, fatigue, logs to `Logs/Gameplay/<YYMMDD>/` |
| Unreal (URLab + NNE) | `Source/PoSlapUnreal/SlapController.cpp` (`USlapController`, a URLab `MjArticulationController`) | Game. Runs on URLab's physics thread; `SlapHUD.cpp` draws the HUD, menu and build stamp |
| Browser (MuJoCo WASM + onnxruntime-web) | `Web/sim.js`, `Web/loader.js` | Spectator site, auto-deployed to Azure on push to `master` |

Training envs (`training/envs/locomotion.py` S2 (walk-in), `slap.py` L0–L3, `arena.py` L4) run on GPU via `mujoco-warp`
and build the same observation (140 floats, interface 4; shipped brains read their first 136).

**Shared numbers and rules are generated, not typed three times.** `training/constants.py` (brain I/O, timing, gaze,
feint) and `training/rules.json` (the match rules) are the source. `python training/gen_constants.py` writes
`Source/PoSlapUnreal/SlapShared.gen.h` (namespace `SlapGen`) and `Web/shared.gen.js`; C++ and JS read those. After
changing either source, run the generator and commit both generated files with it (`verify_constants.py` and both
workflows fail on a stale copy). Never edit a `.gen.` file.

**Rules 1 and rules 2.** A fingerprint's `"physics"` picks the rules: below 4 the shipped rules (a scripted
`strike_mass` impulse at the touch, a knockdown after the strike = KO); from 4 (`training/models/v4/`, the physics-3
bodies) rules 2: no scripted impulse, a stun on head speed, a 10 s count, flinch and clubbing fouls, judge noise, a
daze on a hurt neck. Everything shipped is physics 1. `SLAP_RULES=2` (Python, `check.mjs`, `parity.mjs`) and
`-SlapRules=2` (Unreal) force rules 2 on any physics for checks.

**Body pipeline:** character card `training/characters/<name>.json` + Blender skeleton `<name>_skeleton.json`
→ `training/build_body.py` → `training/models/<name>_solo.xml`, `<red>_vs_<blue>_arena.xml`, `<name>_fingerprint.json`
→ `Scripts/build_scene.py` imports the arena into Unreal through URLab. The card is the source of truth; the fingerprint is a build-time copy.

**Brains:** `training/models/brains/<char>_<level>.onnx` (+ `.pt` checkpoints), stored with Git LFS. A brain only lands
there on graduation (`train.py` handles this). Android has no ONNX runtime in UE 5.8: `training/export_cpu_brains.py`
writes weights to `Content/Data/Brains/*.bin` and `USlapController` rebuilds the MLP for NNE's Basic CPU runtime.

**URLab** (`Plugins/UnrealRoboticsLab`, submodule) is patched, not edited in place: changes go into `Plugins/urlab_poslap.patch`.

## Commands

First-time setup (from repo root):

```powershell
git clone --recurse-submodules https://github.com/URLab-Sim/UnrealRoboticsLab Plugins/UnrealRoboticsLab   # not tracked here
git -C Plugins/UnrealRoboticsLab am ../urlab_poslap.patch
git -C Plugins/UnrealRoboticsLab submodule update --recursive   # the patch moves MuJoCo's gitlink to 3.10.0
Push-Location Plugins/UnrealRoboticsLab/third_party; powershell -ExecutionPolicy Bypass -File .\build_all.ps1; Pop-Location   # MuJoCo, CoACD, ZMQ; must run from its own folder
# or all of the above in one go: powershell -ExecutionPolicy Bypass -File Scripts/setup_urlab.ps1
# setup_urlab.ps1 also pip-installs trimesh numpy scipy networkx Pillow into UE 5.8's bundled Python and writes
# Plugins/UnrealRoboticsLab/Config/LocalUnrealRoboticsLab.ini (PythonPath=): URLab's MJCF importer asks for both in a dialog,
# and under -unattended that dialog cancels the import (build_scene.py dies with StopIteration at the arena import)
uv venv training/.venv --python 3.11; uv pip install --python training/.venv -r training/requirements.txt   # + torch cu128, trimesh scipy networkx Pillow
git lfs pull   # brains and Web/assets/*.glb
```

Python runs from `training/` with its venv (scripts import `envs`, `levels`, `ppo` relative to that folder):

```powershell
cd training
.venv/Scripts/python.exe build_body.py characters/grandpa.json --blue characters/kim.json   # rebuild models after a card change
.venv/Scripts/python.exe train.py --level L0 --minutes 5                                   # one ladder level (levels.py); TensorBoard :6006
.venv/Scripts/python.exe overnight.py --hours 10                                           # queue from runs/overnight_queue.json
.venv/Scripts/python.exe overnight.py --hours 10 --queue retrain_v5_queue.json --resume    # the rules-2 retrain (copy configs/retrain_v5_queue.json to runs/ first)
.venv/Scripts/python.exe gen_constants.py                                                  # after editing constants.py or rules.json
.venv/Scripts/python.exe match.py play models/brains/grandpa_L4.onnx [--blue <onnx>]       # one match
.venv/Scripts/python.exe match.py exam models/brains/grandpa_L4.onnx --matches 100         # L5 gate
.venv/Scripts/python.exe tournament.py --matches 4                                         # roster.json round robin
```

Unreal (use PowerShell, not Git Bash: Git Bash rewrites `/Game/...` env values into `C:/Program Files/Git/Game/...`):

```powershell
UnrealEditor-Cmd.exe PoSlapUnreal.uproject -run=pythonscript -script="<abs>/Scripts/build_scene.py" -unattended   # SLAP_RED/SLAP_BLUE/SLAP_BRAIN env vars
UnrealEditor-Cmd.exe PoSlapUnreal.uproject -run=pythonscript -script="<abs>/Scripts/set_brain.py" -unattended     # swap a brain only
UnrealEditor.exe PoSlapUnreal.uproject /Game/Levels/L_Arena_grandpa_vs_kim -game -ResX=540 -ResY=960
```

Voices (per character card: HAPPY / ANGRY / HURT / INSULT x 5 sounds x 5 levels; placeholders are synthesised, a recording
saved over a file under the same name is kept): `training/.venv/Scripts/python.exe Scripts/voice_pack.py [--report]`, then
`UnrealEditor-Cmd.exe ... -run=pythonscript -script="<abs>/Scripts/voice_import.py"` → `/Game/Audio/Voice/<card>/S_V_<card>_<EMOTION>_<n>_<level>`.

Broadcast pass (2026-11-12, display only; `SlapHUDStage.cpp`, `SlapHUDWinProb.cpp`, `SlapHUDReel.cpp`):
`UnrealEditor-Cmd.exe ... -run=pythonscript -script="<abs>/Scripts/broadcast_pass.py"` builds its assets (`/Game/FX/M_Jumbotron`,
the soft-tissue nodes on every `M_<athlete>`, `/Game/Cinematics/LS_*`); `training/.venv/Scripts/python.exe winprob.py [--simulate N]`
(from `training/`) refits `models/winprob.json`; `training/.venv/Scripts/python.exe Scripts/highlight_reel.py [--4k]` renders the newest
`Saved/PoSlap/reels/*.reel` (saved after a KO or a 9 m/s slap) to `Saved/Clips/*.mp4`, one physics step per frame.
Switches: `-SlapJumbo=0`, `-SlapTitles=0`, `-SlapSeq=0`, `-SlapJiggle=0`, `-SlapReel=<file>`.

Web: `cd Web && npm ci`, then `python Web/serve.py` (http://localhost:8080/Web/) or `python Web/build.py` → `Web/dist/`.
Pushing changes under `Web/`, `training/models/` or `training/characters/` to `master` deploys the site.

## Checks (no unit-test suite; these are the tests)

- `training/replay_check.py record|compare`: MuJoCo vs URLab replay, every body ≤ 1 mm. Pass `--mjb Saved/URLab/scene_compiled.mjb`.
- `training/match.py compare <onnx> <unreal match .jsonl> --mjb ...`: Unreal plays the same match events as Python.
- `node Web/check.mjs [gg|kk|gk]` (from repo root): one turn each way through the browser engine; exits 1 if no turn ends.
- `training/parity_check.py [--stun 0.55]` (training venv; `npm ci` in `Web/`): the same seeded matches through `match.py` and
  `sim.js` (`node Web/parity.mjs <python log> gk`), referee event for event, rules 1 and rules 2.
- `training/verify_constants.py` (standard library only): the generated copies are current, the engines read them, the
  observation layout matches `DOCS/BRAIN_IO.md`. `training/verify_cards.py`: cards, models, roster, packaged brains.
- `training/calibrate_rules.py <onnx> [--blue ...] [--physics 4] [--rules 2]`: the head-speed and flinch percentiles
  `rules.json`'s thresholds are set from.
- `training/verify_realism.py`: card and model realism checks.
- CI: `.github/workflows/verify.yml` runs the checks above that need no GPU and no Unreal on every push to `master`.
- Unreal synthetic tests: launch with `-game -SlapUiTest` or `-SlapPairTest`; `-SlapTourney -SlapTourneyQuit` plays a whole 16-place bracket (TOURNEY tab, `Saved/PoSlap/tournament.json`) and quits on the champion (needs a window: under `-nullrhi` DrawHUD never runs, so neither does the bracket); `-SlapBasicCpu` runs the Android brain path on desktop.
