# UU_AGENT (agents.md)

### 1. Workflow, Tooling & Asset Pipeline
- **Git & Docs:** Work on `master` only. Stage/commit prior to sync/pull. Check `/DOCS` first.
- **Automation & Headless:** Drive scene/prefab/level setup via CLI. Use headless Blender (`blender -b`) / Blender MCP.
  UI changes: annotated before/after screenshots and an HTML report in `SCREENSHOTS/`.
- **Build Stamp:** Show the build date (`YYMMDD`) in the bottom-right corner of the screen.

#### Platform Setup Matrix

| Dimension | Unity Pipeline | Unreal Engine 5 Pipeline |
|---|---|---|
| Background Throttling | Enable Run In Background; run headless via CLI/MCP | Disable Use Less CPU when in Background |
| Raw Asset Path | `Assets/Art/Raw/` | `Content/Art/Raw/` |
| Export Asset Path | `Assets/Art/Exports/GLB/` | `Content/Art/Exports/` |
| Physics Solver | Bypass PhysX → MuJoCo (`org.mujoco`) | Bypass Chaos → MuJoCo / URLab |
| Neural Inference | Unity Sentis / ONNX (mlagents) | NNE / ONNX Runtime / C++ |

### 2. Training Lifecycle & Resource Management
- **Headless Execution:** Close the editor for pure RL, runs > 30 min, or memory reclamation (free RAM < 4 GB).
  Save all assets and commit to `master` first; if the editor can't save cleanly, ask instead of closing.
- **Pruning & Cleanup:** Delete failed runs (crash log or non-zero exit), short test runs (< 5,000 steps), old finished
  runs and unneeded checkpoints. Never touch a run written to in the last 24 h or one a training queue still has to resume.
- **Telemetry:** Stream kinematics, actions, observations and metrics locally (`Logs/Gameplay/<YYMMDD>/`, JSONL)
  or to Azure Table Storage when asked. TensorBoard runs with every training run. Log training metrics and
  progress to `DOCS/<YYYYMMDD>/` so later agents can pick up where a run left off.
- **Progress Updates:** Estimate training time at startup; send hourly updates with a status grid, key improvements
  and a short summary.

### 3. Physics, Collisions & Verification Gates
- **Collision Rules:** Primitive colliders only (capsules, spheres, boxes). Self-collision on non-adjacent pairs.
  Zero penetration across rest, T-pose, joint limits and a swing.
- **Physical Fidelity:** Gravity −9.81 m/s², anatomical mass, realistic joint/torque limits. Skeleton joint frames
  and bone directions match between the 3D files and the simulator.
- **Graduation Gate:** Export ONNX policies when mean reward meets target for 100 consecutive episodes without NaN gradients.
- **HTML Milestones:** For runs > 30 min, an HTML report with 3-tier milestones (first 10 % / 45–55 % / final 10 %
  of steps), top-3 metric charts and run-over-run deltas.
- **Action Smoothing:** Rewards carry action-rate/jerk penalties (or low-pass filtering) for fluid, human-like joint
  motion with no jitter.

### 4. Output Directives
- **Strategic Direction:** If a code change exceeds 100 lines without pending questions, offer 3 architecture/roadmap questions.
- **TL;DR:** Append a ~20-word TL;DR to responses exceeding 100 words.
- **Problem Solving:** End each response with a proposed fix for the app's main open issue, and ask for confirmation before acting on it.

## CI/CD and Azure (same section in every Po repo)

- A push to `master` deploys the web build (`.github/workflows/deploy-web.yml`) to its Azure Static Web App. Nothing is deployed by hand.
- Tests pass on any machine: no drive-letter, temp-folder or working-directory assumptions, and no live config value hard-coded as an expected result (read it from the same config the app reads).
- A red run on the default branch gets an ntfy push from the CI watcher in PoPunkouterSoftware. Fix it, or say why it is red.
