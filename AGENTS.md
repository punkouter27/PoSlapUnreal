# UNREAL_AGENT (agents.md)

### 1. Project Setup & Git Workflow
- **Project Context & Versioning:** Read `/DOCS` summaries before starting. Display deployment build version `YYMMDD` (derived via automated build script from current date or Git commit date) in the bottom-right corner.
- **Branching & Sync:** Work exclusively on `master`. Commit pending changes before any git sync.
- **Scene Construction & Assets:** Author Levels, Actors, Blueprints, and Data Layers via Unreal CLI / Unreal Engine MCP. When modifying Blender scenes via Blender MCP, archive original `.blend` files in `Content/Art/Raw/` and export GLB/FBX/USD copies to `Content/Art/Exports/` for future reference.
- **Performance Safeguards:** Disable editor throttling (uncheck "Use Less CPU when in Background"), ensure active tick rates when out of focus, and sustain auto-ticking via Unreal MCP / CLI automation.
- **UI Documentation:** Save UMG widget/UI diffs as an HTML report with annotated before/after viewport captures in `SCREENSHOTS/`.

### 2. Training Environment & Execution
- **Engine Routing:** Train in MuJoCo/Newton (or MuJoCo Warp). For in-engine verification or live integration, execute via **UnrealRoboticsLab (URLab)** or native embedded C++ MuJoCo stepping, bypassing Chaos physics.
- **Mesh & Rig Pipeline:** Validate skeletal meshes and extract bone hierarchies/coordinate frames ($Z$-up conversion) prior to MuJoCo/Newton training.
- **Inference Runtime:** Export trained policies to ONNX format and load via Unreal NNE (Neural Network Engine) or ONNX Runtime plugin for runtime neural inference.
- **Resource Management & Housekeeping:** Keep simulation telemetry visible. Close Unreal Engine Editor during heavy headless RL training runs or sessions >30 min to maximize GPU compute; automatically prune abandoned run directories, dummy checkpoints, and TensorBoard logs under 5,000 steps.
- **TensorBoard & Graduation Metrics:** Start TensorBoard at launch. Export policy only after passing graduation thresholds (mean reward $\ge$ target across 100 consecutive episodes with zero NaN gradients). For runs >30 min, output an HTML report with top-3 metric screenshots, delta comparisons, and 3-tier summaries (Toddler, Child, Adult).
- **Telemetry:** Stream agent actions, win/loss stats, and kinematics to local storage or Azure Tables for future analysis and debugging.

### 3. Physics, Biomechanics & Self-Collision
- **Physical Realism:** Enforce real-world gravity, proportional mass, and realistic joint forces/actuator torque limits.
- **Primitive Colliders:** Enclose skeletal bodies using primitive collision shapes (capsules, boxes, spheres)—never raw skeleton transforms or complex visual meshes.
- **Self-Collision Matrix:** Verify zero penetration in reference pose, rest state, and full swing arcs. Enable collision for all paired segments except direct parent-child linked bodies.

### 4. Code Change Gate & Purpose Direction
- **Scope Trigger:** After any code change exceeding 100 lines (and when no other pending questions remain in the user's prompt), ask 3 optional high-level strategic questions focusing on the application's overarching purpose, system trajectory, and potential next architectural pivots.

### 5. Response Formatting
- **TL;DR Rule:** Append a 20-word TL;DR to any response exceeding 100 words.
