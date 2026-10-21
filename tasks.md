# PoSlap v1 — Build Checklist

Spec: [BLUEPRINT.md](BLUEPRINT.md) · Rules: [AGENTS.md](AGENTS.md) · PRD: Power Slap Biomechanics Simulation v1.0

> **GATE:** Phase A does not start until the user approves this list.

## Phase 0 — Housekeeping (done)
- [x] Kickoff interview, plan confirmed
- [x] Movement Blueprint approved
- [x] Unreal MCP plugin enabled (`ModelContextProtocol` + `AllToolsets`, autostart :8000), `.mcp.json` registered
- [x] Editor throttling off (`bThrottleCPUWhenNotForeground=False`), Python Editor Script plugin on

## Phase A — Setup: mesh → simulated body
- [ ] A1 `git init` on `master`, `.gitignore` (Binaries/Intermediate/Saved/DerivedDataCache/.vs, training checkpoints/logs), first commit
- [ ] A2 `DOCS/` with a one-page project summary (agents read it first)
- [ ] A3 Blender: archive `GRANDPA.glb` original as `Content/Art/Raw/GRANDPA_raw.blend`
- [ ] A4 Blender: remove forearm spike artifact, decimate to a game budget (~20k tris), scale to card height 1.70 m, feet on Z=0, facing −Y (Blender) = +X (MuJoCo)
- [ ] A5 Blender: build a 30-motor skeleton matching Blueprint §2.3 (pelvis root, waist, chest, neck, head, shoulders, elbows, wrists, hips, knees, ankles), auto-weight, pose-test (swing arm, squat) with `look` screenshots
- [ ] A6 Export rigged `Content/Art/Exports/Grandpa_rigged.glb` + `.fbx`; save `Content/Art/Raw/Grandpa_rigged.blend`
- [ ] A7 Character card `training/characters/grandpa.json` (height, mass, strength, speed, flexibility, tint)
- [ ] A8 Rig validator: bone hierarchy, Z-up frames, symmetry, lengths → printed report (fails loudly on a bad mesh)
- [ ] A9 Rig + card → MuJoCo model `training/models/grandpa_slap.xml` (solo) and `slap_arena.xml` (2 athletes, podium, mat, foot boxes): primitive colliders inside the skin, masses per Blueprint §2.2, 30 motors capped by card strength/speed
- [ ] A10 Self-collision check: zero overlap in reference pose, rest stance, full slap swing arc; only parent–child pairs excluded
- [ ] A11 Balance check: COM over mid-foot in neutral stance; passive drop test (model doesn't explode, settles)
- [ ] A12 Pin MuJoCo to URLab's submodule version in the training venv; record version + model hash in `training/fingerprint.json`

## Phase B — Dry Run: simulator ↔ Unreal, untrained brain
- [ ] B1 Clone URLab into `Plugins/UnrealRoboticsLab` (with submodules), run `third_party/build_all.ps1`, build project on UE 5.8 (fallback: install 5.7)
- [ ] B2 Import `slap_arena.xml` via URLab → Articulation Blueprint; Chaos off on every athlete/podium/floor/prop component
- [ ] B3 Coordinate check: markers at MuJoCo (0,0,0), (1,0,0), (0,1,0) m land at UE (0,0,0), (100,0,0), (0,−100,0) cm
- [ ] B4 Skinned Grandpa follows MuJoCo bodies bone-by-bone (display only); Red/Blue tints from one mesh
- [ ] B5 Fixed 200 Hz physics clock independent of frame rate; 60 fps render blends last two states
- [ ] B6 Random-weights ONNX brain (exported from the trainer) loads via NNE and drives both athletes at 50 Hz — proves the load path, not the behaviour
- [ ] B7 Replay test harness: same recorded 5 s action stream in MuJoCo (Python) and URLab; per-step body diff report (target ≤ 1 mm)
- [ ] B8 Portrait 9:16 camera (side-on, both athletes framed), PIE window 1080×1920 scaled
- [ ] B9 Build stamp `YYMMDD` bottom-right, from git commit date via build script
- [ ] B10 Reset-in-place (no spawn/destroy): out-of-bounds or knockdown → both athletes back to foot boxes
- [ ] B11 Knockdown detector from MuJoCo contacts (non-sole vs floor/podium/mat), logged in UE

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
