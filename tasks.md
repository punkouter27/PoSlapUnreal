# PoSlap v1 — Build Checklist

Spec: [BLUEPRINT.md](BLUEPRINT.md) · Rules: [AGENTS.md](AGENTS.md) · PRD: Power Slap Biomechanics Simulation v1.0

> **GATE:** approved 2026-10-21. Phase A done 2026-10-21.

## Phase 0 — Housekeeping (done)
- [x] Kickoff interview, plan confirmed
- [x] Movement Blueprint approved
- [x] Unreal MCP plugin enabled (`ModelContextProtocol` + `AllToolsets`, autostart :8000), `.mcp.json` registered
- [x] Editor throttling off (`bThrottleCPUWhenNotForeground=False`), Python Editor Script plugin on

## Phase A — Setup: mesh → simulated body
- [x] A1 `git init` on `master`, `.gitignore` (Binaries/Intermediate/Saved/DerivedDataCache/.vs, training checkpoints/logs), first commit
- [x] A2 `DOCS/` with a one-page project summary (agents read it first)
- [x] A3 Blender: archive `GRANDPA.glb` original as `Content/Art/Raw/GRANDPA_raw.blend`
- [x] A4 Blender: remove forearm spike artifact, decimate to a game budget (~20k tris), scale to card height 1.70 m, feet on Z=0, facing −Y (Blender) = +X (MuJoCo)
- [x] A5 Blender: build a 30-motor skeleton matching Blueprint §2.3 (pelvis root, waist, chest, neck, head, shoulders, elbows, wrists, hips, knees, ankles), auto-weight, pose-test (swing arm, squat) with `look` screenshots
- [x] A6 Export rigged `Content/Art/Exports/Grandpa_rigged.glb` + `.fbx`; save `Content/Art/Raw/Grandpa_rigged.blend`
- [x] A7 Character card `training/characters/grandpa.json` (height, mass, strength, speed, flexibility, tint)
- [x] A8 Rig validator: bone hierarchy, Z-up frames, symmetry, lengths → printed report (fails loudly on a bad mesh)
- [x] A9 Rig + card → MuJoCo model `training/models/grandpa_slap.xml` (solo) and `slap_arena.xml` (2 athletes, podium, mat, foot boxes): primitive colliders inside the skin, masses per Blueprint §2.2, 30 motors capped by card strength/speed
- [x] A10 Self-collision check: zero overlap in reference pose, rest stance, full slap swing arc; only parent–child pairs excluded
- [x] A11 Balance check: COM over mid-foot in neutral stance; passive drop test (model doesn't explode, settles)
- [x] A12 Pin MuJoCo to URLab's submodule version in the training venv; record version + model hash in `training/fingerprint.json`

## Phase B — Dry Run: simulator ↔ Unreal, untrained brain
- [x] B1 Clone URLab into `Plugins/UnrealRoboticsLab` (with submodules), run `third_party/build_all.ps1`, build project on UE 5.8 (fallback: install 5.7) — done: MuJoCo submodule pinned to 3.10.0, URLab patched for UE 5.8 JSON keys (`Plugins/urlab_poslap.patch`)
- [x] B2 Import `slap_arena.xml` via URLab → Articulation Blueprint; Chaos off on every athlete/podium/floor/prop component — done: imported by `Scripts/build_scene.py`; runtime Chaos audit turns off collision/physics on every primitive (84 URLab components)
- [x] B3 Coordinate check: markers at MuJoCo (0,0,0), (1,0,0), (0,1,0) m land at UE (0,0,0), (100,0,0), (0,−100,0) cm — done: (0,0,0)/(1,0,0)/(0,1,0) m → (0,0,0)/(100,0,0)/(0,−100,0) cm exactly (self-check log)
- [x] B4 Skinned Grandpa follows MuJoCo bodies bone-by-bone (display only); Red/Blue tints from one mesh — done: bone-body gap 0.0000 cm both athletes; team colour = fresnel overlay on one mesh
- [x] B5 Fixed 200 Hz physics clock independent of frame rate; 60 fps render blends last two states — done: URLab paces 200 Hz (1000 steps in 5.08 s wall); render shows the latest snapshot (≤ 5 ms old at 200 Hz), so no blending was needed
- [x] B6 Random-weights ONNX brain (exported from the trainer) loads via NNE and drives both athletes at 50 Hz — proves the load path, not the behaviour — done: random-weights `random_brain.onnx` runs via NNE (ORT CPU) on the physics thread for both athletes
- [x] B7 Replay test harness: same recorded 5 s action stream in MuJoCo (Python) and URLab; per-step body diff report (target ≤ 1 mm) — done: **PASS, max 0.017 mm over 5 s** (`replay_check.py`). Fixes needed: options set from fingerprint (URLab drops child `<option>`), explicit free joint (URLab applies defaults to it), float32-exact MJCF
- [x] B8 Portrait 9:16 camera (side-on, both athletes framed), PIE window 1080×1920 scaled — done: CineCamera 9:16, auto exposure; `-ResX=540 -ResY=960`
- [x] B9 Build stamp `YYMMDD` bottom-right, from git commit date via build script — done: `POSLAP_BUILD_VERSION` from git commit date in `PoSlapUnreal.Build.cs`, drawn by `ASlapHUD`
- [x] B10 Reset-in-place (no spawn/destroy): out-of-bounds or knockdown → both athletes back to foot boxes — done: knockdown/out-of-bounds → both athletes reset to foot boxes (qpos/qvel), no spawn/destroy
- [x] B11 Knockdown detector from MuJoCo contacts (non-sole vs floor/podium/mat), logged in UE — done: any non-foot geom on floor/podium → logged knockdown (`LogSlap`)

## Phase C — Training Prep
- [ ] C1 `training/envs/slap.py` on MuJoCo Warp: observation (incl. role flag), 30 actions, 50 Hz control / 200 Hz physics
- [ ] C2 Randomisation per Blueprint §6 (pushes 150–300 N, friction 0.6–1.2, ±5 % mass, ±10 % strength, 0–20 ms delay)
- [ ] C3 Level configs L0–L5 with pass/fail checks + graduation (reward target over 100 episodes, zero NaN grads)
- [ ] C4 PPO trainer, TensorBoard autostart, checkpoint + ONNX export only on graduation
- [ ] C5 Housekeeping: prune runs/checkpoints/TB logs under 5,000 steps; HTML report for runs > 30 min (top-3 charts, delta vs last, Toddler/Child/Adult)
- [ ] C6 Telemetry to local storage (JSONL): actions, kinematics, strikes, outcomes
- [ ] C7 Smoke run: 5 min L0 on GPU, curves move, no NaNs, Unreal Editor closed

## Step 4 — Training & Verification (after Phase C)
- [ ] L0 Stand trained and graduated in MuJoCo
- [ ] **Matching Check** (Blueprint §8): replay ≤ 1 mm, live ≤ 1 cm, same outcome — STOP and fix on failure
- [ ] L1 → L5 in order, each graduating before the next

## Step 5 — Visual Polish & Gameplay (after L5)
- [ ] Arena art, lighting rig, tracking camera, 9:16 layout
- [ ] Match flow: coin toss, turns, 30 s clocks, rounds, KO, 10-point-must judges
- [ ] HUD telemetry (PRD §6); UI diff HTML reports in `SCREENSHOTS/`
- [ ] Performance pass (60 fps), save, mark complete
