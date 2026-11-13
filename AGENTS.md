# UU_AGENT (agents.md)
Scope: RL-trained humanoids and animals with realistic motion on Earth, deployed exclusively in Unreal Engine using
MuJoCo Warp / URLab.

### 1. Workflow & Setup
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

### 2. Physics & Transform Conventions
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

### 3. Policy Architecture & Hand-Overs
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
- **Audit Trail:** Log all affected policies, state machine rows, and interface revisions in `/DOCS` prior to
  modifications. Re-run task and kinematic regression benchmarks after any update.

### 4. Training, Gates & Optimization
- **Execution & Resources:** Close the UE5 editor during headless training runs exceeding 30 minutes or when available
  system RAM drops below 4 GB. Save and commit state beforehand.
- **Cleanup:** Purge failed runs, short checks (<5,000 steps), and redundant checkpoints. Never delete a run modified
  within the last 24 hours or one tied to an active training queue.
- **Verification Gate:** Export the final ONNX model only after holding target rewards for 100 consecutive episodes
  with zero NaN gradients. Validate a 5-second rollout ensuring:
  1. *Kinematics:* Root velocity, angular momentum, and joint angles match the MuJoCo/URLab baseline.
  2. *Contacts:* No foot sliding, ground clipping, or contact chatter.
- **Telemetry:** Run TensorBoard alongside all runs. Output training metrics to `/DOCS/<YYYYMMDD>/`.
- **Runtime Profiling:** Enforce zero heap allocation (no dynamic allocations) inside the real-time C++ inference loop
  to guarantee consistent frame budgets.
- **Reporting:** Runs over 30 minutes require an HTML report with convergence curves and run-over-run deltas. Provide
  hourly progress summaries.

### 5. Communication & Phased Interaction Protocol
- **State Header:** Prefix all task execution responses with `**Current Phase: Step X of 5**`.
- **Approval Protocol:** Stop completely at each designated `[STOP]` gate. Do not output code or subsequent phases
  until explicit user approval is provided.
- **Large Changes:** For code modifications exceeding 100 lines, provide 3 architectural/roadmap questions before
  implementation.
- **Summary:** Responses exceeding 100 words must conclude with a concise ~20-word TL;DR.
- **Issue Resolution:** Conclude technical troubleshooting with a direct proposed resolution for the primary open
  issue and await confirmation before applying it.

## CI/CD and Azure (same section in every Po repo)

- A push to `master` deploys the web build (`.github/workflows/deploy-web.yml`) to its Azure Static Web App. Nothing is deployed by hand.
- Tests pass on any machine: no drive-letter, temp-folder or working-directory assumptions, and no live config value hard-coded as an expected result (read it from the same config the app reads).
- A red run on the default branch gets an ntfy push from the CI watcher in PoPunkouterSoftware. Fix it, or say why it is red.
