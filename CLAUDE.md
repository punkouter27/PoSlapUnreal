# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@AGENTS.md
@BLUEPRINT.md

Build checklist and current phase: [tasks.md](tasks.md). Brain input/output layout: [DOCS/BRAIN_IO.md](DOCS/BRAIN_IO.md).
Project summary: [DOCS/SUMMARY.md](DOCS/SUMMARY.md). Dated reports and dashboards: `DOCS/<YYYYMMDD>/`.

## Architecture: one match, three engines

The same rules and the same brain I/O are written three times. A change to rules, observations, actions, timing
or fatigue has to land in all three, or the parity checks below break:

| Engine | File | Role |
|---|---|---|
| CPU MuJoCo (Python) | `training/match.py` + `training/brain_cpu.py` | **Reference.** Referee, judges, fatigue, logs to `Logs/Gameplay/<YYMMDD>/` |
| Unreal (URLab + NNE) | `Source/PoSlapUnreal/SlapController.cpp` (`USlapController`, a URLab `MjArticulationController`) | Game. Runs on URLab's physics thread; `SlapHUD.cpp` draws the HUD, menu and build stamp |
| Browser (MuJoCo WASM + onnxruntime-web) | `Web/sim.js`, `Web/loader.js` | Spectator site, auto-deployed to Azure on push to `master` |

Training envs (`training/envs/locomotion.py` S2 (walk-in), `slap.py` L0–L3, `arena.py` L4) run on GPU via `mujoco-warp`
and build the same 136-dim observation. `OBS`, `LAGS` and the fatigue floors are constants duplicated in each file.

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
git submodule update --init --recursive
git -C Plugins/UnrealRoboticsLab am ../urlab_poslap.patch
powershell -ExecutionPolicy Bypass -File Plugins/UnrealRoboticsLab/third_party/build_all.ps1   # MuJoCo, CoACD, ZMQ
uv venv training/.venv --python 3.11; training/.venv/Scripts/python.exe -m pip install -r training/requirements.txt
git lfs pull   # brains and Web/assets/*.glb
```

Python runs from `training/` with its venv (scripts import `envs`, `levels`, `ppo` relative to that folder):

```powershell
cd training
.venv/Scripts/python.exe build_body.py characters/grandpa.json --blue characters/kim.json   # rebuild models after a card change
.venv/Scripts/python.exe train.py --level L0 --minutes 5                                   # one ladder level (levels.py); TensorBoard :6006
.venv/Scripts/python.exe overnight.py --hours 10                                           # queue from runs/overnight_queue.json
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

Web: `cd Web && npm ci`, then `python Web/serve.py` (http://localhost:8080/Web/) or `python Web/build.py` → `Web/dist/`.
Pushing changes under `Web/`, `training/models/` or `training/characters/` to `master` deploys the site.

## Checks (no unit-test suite; these are the tests)

- `training/replay_check.py record|compare`: MuJoCo vs URLab replay, every body ≤ 1 mm (Blueprint §8). Pass `--mjb Saved/URLab/scene_compiled.mjb`.
- `training/match.py compare <onnx> <unreal match .jsonl> --mjb ...`: Unreal plays the same match events as Python.
- `node Web/check.mjs [gg|kk|gk]` (from repo root): one turn each way through the browser engine; exits 1 if no turn ends.
- `training/verify_realism.py`: card and model realism checks.
- Unreal synthetic tests: launch with `-game -SlapUiTest` or `-SlapPairTest`; `-SlapBasicCpu` runs the Android brain path on desktop.
