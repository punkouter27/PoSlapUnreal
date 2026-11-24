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

### 5. Before/after screenshots (`SCREENSHOTS/`)
UI changes get a before/after page in [`SCREENSHOTS/`](SCREENSHOTS/) (`<yyMMdd>-<feature>.html`, one side-by-side
`<yyMMdd>-<feature>.png` and the scaled shots in `<yyMMdd>-<feature>/`), made by one command that shoots the local web
build at 1366x768 and 390x844 (never the live site):
```powershell
powershell -ExecutionPolicy Bypass -File scripts\screenshots.ps1 -Feature <name> [-Before <git ref> | -BeforeDir <PNGs>]
```
Example: [`SCREENSHOTS/261124-v4-audit.html`](SCREENSHOTS/261124-v4-audit.html).

---

## Tournament Standard

* **Main menu:** a 4 x 4 grid of the 16 league places: each roster character's tile (place, name, Elo, record), every empty place a **BYE** tile.
* **Bracket (1v1 only, no FFA):** 16-slot single elimination seeded by Elo, bracket order 1v16, 8v9, 4v13, 5v12, 2v15, 7v10, 3v14, 6v11. BYEs take the bottom seeds; a fighter facing a BYE advances unfought; BYE vs BYE sends a BYE on; a draw goes to the higher seed (no replay); a pairing with no arena level in this build is a walkover. PLAY runs every match automatically to a champion; the state is saved to `Saved/PoSlap/tournament.json` (`Source/PoSlapUnreal/SlapHUDTourney.cpp`).
* **Elo:** every character starts at 1000, K = 32, saved to `Saved/PoSlap/elo.json` (`SlapHUDRoundTrip.cpp`, rule in `SlapElo.h`).
  Every **completed** 1v1 bout updates it immediately, whatever started it: menu START, watch, the idle demo / attract mode, the `-SlapLoop` unattended loop and the tournament. A draw scores 0.5 each (no seed rule outside the bracket); an aborted, reset or no-contest bout and a mirror match (a character vs itself) record nothing; each bout is rated once.
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

**Asset naming:** `/Game/Project/Core/Audio/Voice/<card>/S_V_<card>_<EMOTION>_<take 1-5>_<level 1-5>`, e.g. `S_V_grandpa_CELEBRATE_2_5`,
imported from the WAV `Content/Art/Exports/Audio/Voice/<card>/<EMOTION>_<take>_<level>.wav`.

**The manifest, `voices.json`:** `Content/Art/Exports/Audio/Voice/voices.json` (next to the WAVs; the identical
`Content/Data/voices.json` is the copy a packaged build reads, staged as NonUFS). It lists every card x 5 emotions x
5 levels x 5 takes with the asset and WAV names, `exists` (a real recording is there), `placeholder` (a WAV
`voice_pack.py` generated: `manifest.json` lists it and its SHA-1 still matches; it counts as missing), `imported`
(the asset is there), and each card's procedural `voice` profile (pitch,
formant scale, strain, breath, rasp, vibrato, tempo). The flags are written by the generator, never by hand; a
`voice` profile already in the file is kept, so it can be tuned there.

**Adding a recording:** save the WAV under its name in `Content/Art/Exports/Audio/Voice/<card>/`, import it
(`UnrealEditor-Cmd.exe PoSlapUnreal.uproject -run=pythonscript -script="<abs>/scripts/voice_import.py" -unattended`),
then run `python scripts/voice_manifest.py` (or `scripts/voice_pack.py`). It prints "N of M clips recorded ...;
missing: ...". Saving a recording over a placeholder changes its hash, so it counts as recorded. The old placeholder
WAVs and their imported assets stay in the repo but are ignored: the game synthesises those lines with the new engine
(`scripts/voice_pack.py --placeholders` still writes them for listening tests).

**What plays:** the game reads `voices.json` once. A clip marked both `exists` and `imported` plays its asset; every other clip (and
every clip, with one warning, if the manifest is missing) is an **advanced procedural imitation** in that card's
voice (`USlapVoiceWave`, `Source/PoSlapUnreal/SlapVoiceSynth.cpp`): a Rosenberg glottal pulse with jitter, shimmer,
subharmonic rasp and aspiration, through a cascade of four vowel formants, built from 1-4 syllables with consonant
onsets (h, s, sh, f, k, t, p, n, a gasp) and per-emotion prosody (angry: tense, falling, growled; happy: bright
rise-fall and laughter; hurt: gasp then a breaking groan, a long cry at level 5; insult: nasal sing-song "nyah-nyah";
celebrate: a rising "woo-hoo!" with wide vibrato, then laughter). The level scales loudness (peaks 0.30 .. 0.94, the
placeholders' loudness), length, pitch range, rasp, breath, syllables and vibrato. Each line is deterministic per
(card, emotion, take, level), made once and cached; the miss is logged at `Verbose` only.

**Tests:** `pytest tests/test_voice_manifest.py` (every card x 5 x 5 x 5, the names, the flags against the folders,
a distinct voice per card); `UnrealEditor-Cmd.exe PoSlapUnreal.uproject -ExecCmds="Automation RunTests PoSlap.Voice;Quit" -unattended -nullrhi`
(each emotion is audible, level 5 louder and longer than level 1, deterministic, two cards differ).
