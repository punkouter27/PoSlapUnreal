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
- [x] C1 `training/envs/slap.py` on MuJoCo Warp: observation (incl. role flag), 30 actions, 50 Hz control / 200 Hz physics — done: `training/envs/slap.py`, BRAIN_IO obs (role-0 opponent inputs zeroed, both sides), fall = Unreal's contact rule; ~53k steps/s at 4096 worlds
- [x] C2 Randomisation per Blueprint §6 (pushes 150–300 N, friction 0.6–1.2, ±5 % mass, ±10 % strength, 0–20 ms delay) — done: friction 0.6–1.2, ±5 % mass+inertia, ±10 % strength, 0–20 ms delay, 150–300 N pushes, sensor noise. Skipped: per-foot friction (floor friction has priority), add if L0 needs it
- [x] C3 Level configs L0–L5 with pass/fail checks + graduation (reward target over 100 episodes, zero NaN grads) — done: `training/levels.py` L0–L5 with targets and exams; L0/L1 mechanics live, L2+ mechanics added as the ladder reaches them
- [x] C4 PPO trainer, TensorBoard autostart, checkpoint + ONNX export only on graduation — done: `train.py` (PPO from PoBox's `ppo.py`), TensorBoard autostart :6006, exam + ONNX export only on graduation (return over 100 episodes, no NaN)
- [x] C5 Housekeeping: prune runs/checkpoints/TB logs under 5,000 steps; HTML report for runs > 30 min (top-3 charts, delta vs last, Toddler/Child/Adult) — done: prune runs under 5,000 steps / no meta; `report.py` HTML (top-3 charts, delta vs last run, Toddler/Child/Adult) for runs > 30 min
- [x] C6 Telemetry to local storage (JSONL): actions, kinematics, strikes, outcomes — done: `runs/<run>/telemetry.jsonl` per iteration (stats + world-0 kinematics) and TensorBoard scalars
- [x] C7 Smoke run: 5 min L0 on GPU, curves move, no NaNs, Unreal Editor closed — done: 5 min, 15.7 M steps, no NaN, standing 1.9 s → 3.6 s, editor closed. Learning-rate collapse + strong pushes to tune in Step 4

## Step 4 — Training & Verification (after Phase C)
- [x] L0 Stand trained and graduated in MuJoCo — 46 min (+13 min before the noise cap), exam 97 % no-fall under full 150–300 N pushes; `training/models/brains/grandpa_L0.onnx`
- [x] **Matching Check** (Blueprint §8): replay 0.017 mm, **live 0.000 cm** (bit-identical vs URLab's model), same outcome
- [ ] L1 → L5 in order, each graduating before the next
  - 2026-10-22: last night's L2 "graduation" predates the podium in the training model (run loaded 03:03, podium added 04:14): its left hand hits the podium (knockdown), and L1 best drops 94 → 28 % with the podium. Strike-only training had also wiped out the guard.
  - Fix: **one brain, every role**: each world plays guard / strike / absorb per the level's mix; a level passes only when every role's exam passes
  - [ ] L1 guard with the podium: fine-tune reached 76 % (exam needs 95 %); now trained inside L2 as 30 % of worlds (GUARD exam = L1's)
  - [ ] L2 strike 0.7 + guard 0.3, from the podium-aware L1 brain (overnight queue, running)
  - [ ] L3 absorb 0.6 + strike 0.4: one scripted slap to the face (impulse 3 → 15 N·s; 15 = estimated median Grandpa slap), opponent hand seen coming; pass = up + COM back within 5 cm after 5 s, ≥ 90 % (queued after L2)
  - [ ] L4 self-play in the arena (`envs/arena.py`, built + smoke-tested): KO rate per landed strike 10–40 %, striker self-falls ≤ 5 %
  - [ ] L5 full match (`training/match.py`, built): 100 matches without blow-ups; Unreal's match log = Python's event for event (pipeline checked: 16/16 events identical)

## Step 5 — Visual Polish & Gameplay (after L5)
- [x] Arena art, lighting rig, tracking camera, 9:16 layout — done: tracking camera; arena v2 (`Content/Art/Raw/Arena_v2.blend` → `Exports/Arena.glb` + `.fbx` + `Textures/`): podium true to physics, tape foot boxes, mat seams/logos, ringside dressing, aisles, LED content, crowd variation, team rims, haze beams, grade; `Scripts/arena_env.py` (`SCREENSHOTS/261022_arena_v2`)
- [x] Match flow: coin toss, turns, 30 s clocks, rounds, KO, 10-point-must judges — done: `USlapController` = `training/match.py` step for step (3 judges weighing speed / head snap / stagger; fouls −1); JSONL to `Logs/Gameplay/<YYMMDD>/`
- [x] HUD telemetry (PRD §6); UI diff HTML reports in `SCREENSHOTS/` — round, clock, hand speed, head snap, referee call, judges' cards, result (PRD not in the repo: fields assumed); reports in `SCREENSHOTS/261022_*`

### Broadcast & intelligence features (user 2026-10-22: options 2-7, 9, 10 of 10; no replay, no viewer voting)
- [x] #2 Telemetry HUD — palm-speed gauge (clean 8 m/s line), defender head g (20 ms), balance rings + COM dots under the soles, condition bars, judges' round cards slide-in, tale of the tape, result banner, series tally (`SCREENSHOTS/261022_broadcast`)
- [x] #3 Impact effects (cosmetic) — Niagara spray (`/Game/FX/NS_SlapSpray`, engine DirectionalBurst), shockwave ring, flash, cheek ripple (WPO on `M_Grandpa`), shake, fringe; synthesised slap/crowd/bell (`Scripts/broadcast_audio.py`, `Scripts/broadcast_fx.py`)
- [x] #4 Director camera — wide / tension close-up / strike whip / KO crane; 4 s post-match hold (`PostMatchHold`) so a KO can be seen
- [x] #5 Memory + feints — BRAIN_IO 130: 0.1-0.3 s history of inputs 102-107, the opponent's last strike (126-127); L3's scripted slap keeps its timing half the time; L4 pays the striker for a defender who flinched before the touch. Checkpoints widen on resume
- [x] #6 Fatigue + damage — card `stamina_per_strike` (joint speed, floor 0.7), `neck_damage_per_snap` (neck forcerange, floor 0.5); inputs 128-129; `condition` event; envs randomise both (70 %). Python = Unreal: compare PASS (strikes, neck damage, KO), live 0.000 cm
- [x] #7 Roster + tournament — per-corner brains (`BlueBrain`, `-SlapRedBrain=` / `-SlapBlueBrain=` .onnx at runtime), `training/roster.json` (two Grandpas), `training/tournament.py` round-robin → `Logs/Tournament/`. Mixed-card pairings wait for a second mesh (own arena + per-corner fingerprints)
- [x] #9 AI commentator — `Scripts/commentator.py`: Claude (claude-opus-5-5, low effort) when credentials exist, else templates; Windows voice; HUD line from `Saved/Commentary/latest.txt`
- [x] Momentum bar (user: yes to the win bar) — each brain's critic vs its usual value in that role (per-role running mean/spread in the HUD), logistic of the difference; not a calibrated win probability (`SCREENSHOTS/261022_momentum`)
- [x] #10 KO clips + Pixel Streaming — `Scripts/clipper.py` (rolling 20 s buffer, KO / strike clips to `Saved/Clips`, 9:16, video only); `Scripts/stream.ps1` (PixelStreaming2 plugin, Epic's UE5.8 signalling server in `Saved/PixelStreaming`, http://localhost/)
- [ ] Performance pass (60 fps), save, mark complete — 69–79 fps at 540×960 with training on the GPU (fps logged every 10 s); re-check with a trained brain
