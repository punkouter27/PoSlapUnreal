# PoSlap Unreal: Power Slap Biomechanics Simulation & RL Spectator Arena

A high-fidelity competitive Power Slap sports biomechanics simulation and autonomous AI tournament arena built in **Unreal Engine 5.8** using **MuJoCo 3.10.0**, **UnrealRoboticsLab (URLab)**, and **Unreal NNE (Neural Network Engine)**.

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
* **UnrealRoboticsLab (`Plugins/UnrealRoboticsLab`):** Pinned to MuJoCo `3.10.0` with custom UE 5.8 JSON key patches (`Plugins/urlab_poslap.patch`).
* **NNERuntimeORT:** Unreal Engine Neural Network Engine ONNX Runtime plugin (CPU execution on physics thread).
* **ModelContextProtocol (`ModelContextProtocol` & `AllToolsets`):** Unreal MCP server enabled on `127.0.0.1:8000/mcp`.
* **PythonScriptPlugin:** Automated editor scene generation, lighting setup, and material compilation.
* **PixelStreaming2:** Low-latency WebRTC broadcast stream to mobile and web viewers.

---

## Quick-Start Instructions

### 1. Repository Setup & Dependencies
```powershell
# Clone submodules and apply URLab patches
git submodule update --init --recursive
git -C Plugins/UnrealRoboticsLab am ../urlab_poslap.patch

# Build URLab native libraries (MuJoCo, CoACD, ZMQ)
powershell -ExecutionPolicy Bypass -File Plugins/UnrealRoboticsLab/third_party/build_all.ps1

# Set up Python training virtual environment
uv venv training/.venv --python 3.11
training/.venv/Scripts/python.exe -m pip install -r training/requirements.txt
```

### 2. Generate Character Bodies & Build Unreal Scene
```powershell
# Generate Grandpa and Kim MuJoCo MJCF models and fingerprint specs
training/.venv/Scripts/python.exe training/build_body.py training/characters/grandpa.json --blue training/characters/kim.json

# Build the arena level pairing in Unreal Editor (idempotent commandlet)
UnrealEditor-Cmd.exe PoSlapUnreal.uproject -run=pythonscript -script="Scripts/build_scene.py" -unattended
```

### 3. Launching the Game & Running Matches
```powershell
# Launch standalone portrait match (Grandpa Red vs. Kim Blue)
UnrealEditor.exe PoSlapUnreal.uproject /Game/Levels/L_Arena_grandpa_vs_kim -game -ResX=540 -ResY=960

# Run automated UI synthetic interaction test
UnrealEditor.exe PoSlapUnreal.uproject /Game/Levels/L_Arena -game -SlapUiTest
```

### 4. Deterministic Replay & Parity Verification
```powershell
# Verify bit-identical match event sequence against Python MuJoCo reference
training/.venv/Scripts/python.exe training/match.py compare training/models/brains/grandpa_final.onnx Saved/URLab/live_ue.json --mjb Saved/URLab/scene_compiled.mjb
```

---

## Documentation Suite (`/DOCS/20261025/`)

Comprehensive technical and design documentation is maintained in [`DOCS/20261025/`](file:///DOCS/20261025/):

* [**Architecture Overview**](file:///DOCS/20261025/architecture_overview.md): Core architecture, 200 Hz URLab loop, NNE inference, 30-DoF control map, and scoring rules.
* [**Creatures Dashboard**](file:///DOCS/20261025/creatures_dashboard.html): Interactive HTML dashboard featuring Executive scorecards and Technical joint actuator curves.
* [**Model & Rig Summary**](file:///DOCS/20261025/model_summary.md): ONNX policy inventory, 136-dim sensory observation vector, and MuJoCo body-to-bone hierarchy.
* [**Scene & Arena Layout**](file:///DOCS/20261025/scene_layout.html): Spatial coordinate transforms, podium obstacles, footboxes, and Chaos vs. MuJoCo collision matrix.
* [**Creature Benchmarks**](file:///DOCS/20261025/creature_benchmarks.html): Performance comparison, strike velocity profiles, and Stat Unit thread profiling.
* [**Training Metrics Guide**](file:///DOCS/20261025/training_metrics_guide.md): Plain-English breakdown of TensorBoard scalars, PPO loss behavior, and multi-agent curriculum ladder.
* [**Agent Abilities Grid**](file:///DOCS/20261025/agent_abilities_grid.md): Biomechanical abilities matrix, proficiency grades (S/A/B), and behavioral servos.
* [**Training Telemetry Visual Report**](file:///DOCS/20261025/training_telemetry_report.html): Top 3 training metric visualizations and stabilization checkpoints.
