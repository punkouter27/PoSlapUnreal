# UR_AGENT (agents.md)

### 1. Project Setup & Daily Workflow
- **Getting Started & Version Watermark:** Read the documentation in `/DOCS` first. Automatically stamp the build date (formatted as `YYMMDD`) in the bottom-right corner of the screen.
- **Git Rules:** Work directly on the `master` branch. Always save and commit your work before syncing or pulling updates.
- **Building Scenes & Art:** Create levels, objects, and scripts using automated Unreal tools. When editing Blender files, save the original file in `Content/Art/Raw/` and put the exported 3D model files in `Content/Art/Exports/`. Use headless Blender (`blender -b`) or the Blender MCP as needed to change the main scene.
- **Keep Unreal Running at Full Speed:** Turn off power-saving mode ("Use Less CPU when in Background") so Unreal runs smoothly even when you click away to another window.
- **UI Tracking:** Whenever you change the user interface, save annotated before-and-after screenshots and an HTML change report inside `SCREENSHOTS/`.

### 2. AI Training & Unreal Integration
- **Where to Train vs. Where to Render:** Train the AI using a fast external physics simulator (MuJoCo, or Newton). When testing in Unreal, run the simulator directly through URLab and skip Unreal's built-in physics engine (Chaos) entirely.
- **3D Character Check:** Make sure character skeletons, joint frames and bone directions match between the 3D files and the training simulator before starting.
- **Running the AI in the Game:** Export trained AI brains into standard model files (ONNX) and run them inside Unreal using its built-in neural network tools.
- **Free Up Computer Power:** Close the Unreal Editor without asking only when (a) a training run is expected to exceed 30 minutes, or (b) free system RAM is below 4 GB. Otherwise leave it open. Before closing the Unreal Editor, save all open assets and commit to `master`. If the editor cannot save cleanly, do not close it; ask the user instead. Clean up failed test runs and incomplete test logs automatically: a run is "failed" only if its process has exited with a non-zero code or a crash log exists. Never delete artifacts from runs that are still active or that completed within the last 24 hours.
- **Tracking Progress:** Start TensorBoard when the run launches. Only export an AI brain to ONNX once mean reward ≥ its target for 100 consecutive episodes with zero NaN gradients. For training sessions over 30 minutes, generate an HTML report with three skill stages — Toddler/Beginner = first 10% of training steps, Child/Intermediate = 45–55%, Adult/Advanced = final 10% — comparing mean reward and success rate at each stage, plus the top-3 metric charts and the change vs. the last run.
- **Save Gameplay Data:** Log the creature's joint actions, observations, movements, and episode scores to `Logs/Gameplay/<YYMMDD>/` as JSONL. Do not upload to cloud storage unless explicitly asked.

### 3. Realistic Body & Physics
- **Keep It Realistic:** Use real-world gravity (−9.81 m/s²), human mass distribution across body parts, and realistic joint limits and strength limits.
- **Simple Hitboxes:** Wrap body parts in simple shapes like capsules, boxes, and spheres. Never use the detailed visual 3D model for physics collisions.
- **Prevent Self-Clipping:** Check for zero overlap between body parts in the rest pose, the T-pose, at extreme joint limits, and through a swing. Allow limbs to bump into other limbs, but ignore joints directly connected to one another (like a forearm and hand).

### 4. Big-Picture Check-In
- **100-Line Check:** After completing and committing any single change that adds or modifies more than 100 lines in total, end your response with three optional, high-level questions about project direction, architecture, or the next major feature — unless the user has already asked their own questions.

### 5. Response Rule
- **Summary Rule:** Add a short summary (20 words or less) at the end of any response longer than 100 words.
