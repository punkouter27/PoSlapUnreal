# UU_AGENT (agents.md)
Scope: RL-trained humanoids and animals with realistic motion on Earth, in Unity or Unreal.

### 1. Workflow & Setup
- **Git:** Work on `master`. Commit before syncing or pulling. Check `/DOCS` first.
- **Automation:** Set up scenes, prefabs and levels via CLI and headless Blender. UI changes get before/after
  screenshots and an HTML report in `SCREENSHOTS/`.
- **Build stamp:** Show the build date (`YYMMDD`) in the bottom-right corner.

| | Unity | Unreal Engine 5 |
|---|---|---|
| Background | Enable Run In Background | Disable Use Less CPU when in Background |
| Raw assets | `Assets/Art/Raw/` | `Content/Art/Raw/` |
| Exported assets | `Assets/Art/Exports/GLB/` | `Content/Art/Exports/` |
| Physics | MuJoCo (`org.mujoco`), not PhysX | MuJoCo / URLab, not Chaos |
| Policy runtime | Unity Sentis / ONNX | NNE / ONNX Runtime |

### 2. Physics & Realism
- **Collision:** Primitive colliders only (capsules, spheres, boxes). Non-adjacent parts collide with each other.
  Zero penetration at rest, at joint limits and through motion.
- **Fidelity:** Gravity −9.81 m/s², anatomical mass, realistic joint, torque and speed limits. Skeleton joints
  match between the 3D files and the simulator.
- **Smooth motion:** Rewards carry action-rate/jerk penalties (or low-pass filtering): fluid motion, no jitter.

### 3. Policy Architecture & Retraining
- **Hierarchy:** A few small policies, one per motor skill family (locomotion, balance/get-up, task skills). Cover
  variants with command inputs (velocity, gait, target, style), not new policies. Layered rule-based directors
  (game flow → body → skill) switch policies on physical state (contacts, centre of mass, falls) through one
  transition table, mirrored in every runtime. Policies never pick each other.
- **Hand-overs:** All policies of a body share the observation layout, joint order, action map, timing and
  motion-style reward. Preload every policy. Pass on the last action; never snap poses or blend outputs. Train each
  policy on the states it inherits (get-up from real falls).
- **Frozen interface:** Append inputs only at the end, with zero-initialised weights; never reorder or remove them.
  If the interface must break, distil the old policy into the new layout.
- **One brain per character:** Every new character gets its own training run and its own policies, so it moves and
  fights with its own personality. Never reuse another character's brain, even when the bodies are close. Personality
  comes from the card (body, strength, speed, toughness, stamina) and its style reward weights, learned in training,
  not scripted by directors. Train from scratch, pass every ladder level and the gate (§4) for that character, and
  train against the existing roster. Keep UI, game flow and director rules outside the policies. Randomise mass,
  strength, ground and pushes so each brain is robust, not so it can be shared.
- **Before any change:** List the policies it touches (body physics → that body's; rules → the policies acting
  under them; new skill → its policy plus director rows; visuals → none) and log that in `/DOCS`. Afterwards, re-run
  every policy's regression scenarios (task + realism). Tag each checkpoint with its interface and physics versions.

### 4. Training, Gates & Reports
- **Execution:** Close the editor for runs over 30 min or when free RAM drops below 4 GB. Save and commit first.
- **Cleanup:** Delete failed runs, short tests (<5,000 steps) and unneeded checkpoints. Never delete a run written
  to in the last 24 h or one a queue will resume.
- **Graduation gate:** Export ONNX only after the target score holds for 100 straight episodes with no NaN gradients.
- **Telemetry:** Run TensorBoard with every run. Log metrics and progress to `/DOCS/<YYYYMMDD>/` so later agents
  can pick up where a run left off. Stream to Azure Table Storage when asked.
- **Reports & updates:** Runs over 30 min get an HTML report with metric charts and run-over-run deltas. Estimate
  training time at startup. Send hourly updates (status grid, key improvements, short summary).

### 5. Communication
- **Large changes:** Over 100 lines of code: offer 3 roadmap/architecture questions.
- **Summary:** Responses over 100 words end with a ~20-word TL;DR.
- **Problem solving:** End with a proposed fix for the app's main open issue and ask before acting on it.

## CI/CD and Azure (same section in every Po repo)

- A push to `master` deploys the web build (`.github/workflows/deploy-web.yml`) to its Azure Static Web App. Nothing is deployed by hand.
- Tests pass on any machine: no drive-letter, temp-folder or working-directory assumptions, and no live config value hard-coded as an expected result (read it from the same config the app reads).
- A red run on the default branch gets an ntfy push from the CI watcher in PoPunkouterSoftware. Fix it, or say why it is red.
