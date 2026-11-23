# PoSlap Unreal: Power Slap Biomechanics Simulation & RL Spectator Arena

A high-fidelity competitive Power Slap sports biomechanics simulation and autonomous AI tournament arena built in **Unreal Engine 5.8** using **MuJoCo 3.10.0**, **UnrealRoboticsLab (URLab)**, and **Unreal NNE (Neural Network Engine)**.

**Browser port (PoSlap Arena):** https://ashy-plant-0f75ec30f.3.azurestaticapps.net (status: `/diag`), deployed by `.github/workflows/deploy.yml` on every push to `master`.

---

## Overview & Architecture Highlights

* **Physics Ownership:** *MuJoCo moves the athletes; Unreal only draws them.* Unreal's Chaos physics engine is disabled across all athletes, arena props, the podium, and the floor.
* **Deterministic Simulation Pacing:**
  * **MuJoCo C-API Physics:** 200 Hz fixed timestep ($\Delta t = 5.0$ ms) running via URLab articulation controllers.
  * **Neural Policy Inference:** 50 Hz decision loop ($\Delta t = 20.0$ ms) via Unreal NNE (ORT CPU runtime), executing every 4th physics sub-step.
  * **Render & Game Threads:** Decoupled 60–120+ fps rendering displaying latest rigid-body snapshots ($<5$ ms latency).
* **Multi-Role Autonomous Brains:** Athletes share a unified continuous PPO neural policy (136 inputs $\rightarrow$ 30 motor outputs) operating across three dynamic match roles: Striker (+1), Defender (-1), and Guard (0).
* **Dynamic Character Roster:**
  * **Grandpa (Slot 1):** 1.70 m, 78.0 kg heavyweight kinetic striker (12.8 m/s palm strikes, deep 34° trunk wind-up).
  * **Kim (Slot 2):** 1.63 m, 60.0 kg athletic counter-striker (11.6 m/s snappy whip, low 25° bent-knee crouch).
  * Scalable to a 16-athlete competitive tournament bracket.
* **Broadcast Presentation:** 9:16 mobile portrait CineCamera, Niagara sweat/slap spray bursts, dynamic cheek ripple World Position Offset (WPO) material deformations, team rim lighting, and automated live commentary.

---

## Required Plugins & Engine Version

* **Unreal Engine:** Version `5.8` (Windows x64).
* **UnrealRoboticsLab (`Plugins/UnrealRoboticsLab`):** not tracked (its own ~2.3 GB clone); `scripts/setup_urlab.ps1` clones upstream, applies `Plugins/urlab_poslap.patch` (MuJoCo `3.10.0` + the UE 5.8 fixes) and builds its third-party libraries.
* **NNERuntimeORT:** Unreal Engine Neural Network Engine ONNX Runtime plugin (CPU execution on physics thread).
* **ModelContextProtocol (`ModelContextProtocol` & `AllToolsets`):** Unreal MCP server enabled on `127.0.0.1:8000/mcp`.
* **PythonScriptPlugin:** Automated editor scene generation, lighting setup, and material compilation.
* **PixelStreaming2:** Low-latency WebRTC broadcast stream to mobile and web viewers.

---

## Quick-Start Instructions

### 1. Repository Setup & Dependencies
`Plugins/UnrealRoboticsLab` is not tracked: run `scripts/setup_urlab.ps1` once per clone (clone upstream URLab, apply
`Plugins/urlab_poslap.patch`, build CoACD / MuJoCo 3.10.0 / libzmq, install the editor-Python packages URLab's MJCF
importer needs); then the editor target builds. Exact verify commands: AGENTS.md, part 3.
```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup_urlab.ps1
# Python: trainer + checks (Python 3.12; MuJoCo pinned in training/requirements.txt)
uv venv training/.venv --python 3.12
uv pip install -p training/.venv -r training/requirements.txt
training/.venv/Scripts/python.exe -m pytest           # the CPU test suite (tests/)
ruff check . ; ruff format --check .
```

### 2. Generate Character Bodies & Build Unreal Scene
```powershell
# Generate Grandpa and Kim MuJoCo MJCF models and fingerprint specs
training/.venv/Scripts/python.exe training/build_body.py training/characters/grandpa.json --blue training/characters/kim.json

# Build the arena level pairing in Unreal Editor (idempotent commandlet)
UnrealEditor-Cmd.exe PoSlapUnreal.uproject -run=pythonscript -script="scripts/build_scene.py" -unattended
```

### 3. Launching the Game & Running Matches
```powershell
# Launch standalone portrait match (Grandpa Red vs. Kim Blue)
UnrealEditor.exe PoSlapUnreal.uproject /Game/Project/Environments/Levels/L_Arena_grandpa_vs_kim -game -ResX=540 -ResY=960

# Run automated UI synthetic interaction test
UnrealEditor.exe PoSlapUnreal.uproject /Game/Project/Environments/Levels/L_Arena -game -SlapUiTest
```

### 4. Deterministic Replay & Parity Verification
```powershell
# Verify bit-identical match event sequence against Python MuJoCo reference
training/.venv/Scripts/python.exe training/match.py compare training/models/brains/grandpa_final.onnx Saved/URLab/live_ue.json --mjb Saved/URLab/scene_compiled.mjb
```

---

## Tournament Standard

* **Main menu:** a 4 x 4 grid of the 16 league places: each roster character's tile (place, name, Elo, record), every empty place a **BYE** tile.
* **Bracket (1v1 only, no FFA):** 16-slot single elimination seeded by Elo, bracket order 1v16, 8v9, 4v13, 5v12, 2v15, 7v10, 3v14, 6v11. BYEs take the bottom seeds; a fighter facing a BYE advances unfought; BYE vs BYE sends a BYE on; a draw goes to the higher seed (no replay); a pairing with no arena level in this build is a walkover. PLAY runs every match automatically to a champion; the state is saved to `Saved/PoSlap/tournament.json` (`Source/PoSlapUnreal/SlapHUDTourney.cpp`).
* **Elo:** every character starts at 1000, K = 32, saved to `Saved/PoSlap/elo.json` (`SlapHUDRoundTrip.cpp`).
* **No free-for-all:** PoSlap is a 1v1 sport, so this project has the bracket only; the FFA mode of the tournament standard does not apply.

## Voice / SFX Matrix

Every card has 5 emotions x 5 intensity levels (1 weakest .. 5 strongest), each with 5 takes (a random take, never the same one twice running):

| Emotion | When |
|---|---|
| `ANGRY` | a knockdown (louder with each one); the loser of a decision |
| `HAPPY` | the defender after a turn with no strike; the winner of a decision |
| `HURT` | struck (level from the palm's speed) |
| `INSULT` | the walk-in introduction; the striker at each new turn |
| `CELEBRATE` | the winner of a bout by KO (level 4); the tournament champion (level 5) |

**Asset naming:** `/Game/Project/Core/Audio/Voice/<card>/S_V_<card>_<EMOTION>_<take 1-5>_<level 1-5>`, e.g. `S_V_grandpa_CELEBRATE_2_5`
(`scripts/voice_pack.py` writes the WAV placeholders for HAPPY / ANGRY / HURT / INSULT; `voice_import.py` imports them; CELEBRATE has no WAVs yet).

**Procedural fallback:** a line with no imported asset is synthesised at runtime (`USlapVoiceWave`, `Source/PoSlapUnreal/SlapVoiceSynth.cpp`):
a glottal buzz through three vowel formants whose pitch, glide, length, loudness and roughness scale with the emotion and the level,
with a per-card pitch offset and vocal-tract size (from the card name) so the cards sound different. Each generated line is made once and cached;
the miss is logged at `Verbose` only. Drop in a WAV with the name above and it replaces the synthesised line on the next launch.
