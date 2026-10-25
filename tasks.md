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
- [x] Physics/brain audit (2026-10-22, e73efa1): action rule reaches every joint's range (Grandpa's slap needed shoulder raise +57°, the old rule capped +4°), damping ratio 0.8 from each joint's load, arm armature 0.005, per-joint speed caps (arms 20/25 rad/s × card), solver iterations 20, own palm inputs (BRAIN_IO 136), L4 approach 30, queue stops on a non-graduation. Grandpa rebuilt; Unreal = URLab model 0.000 mm, live 0.000 cm (130- and 136-input brains). Replay vs XML 5.4 mm, but only after the stream's fall at 3.9 s (a 1e-6 rad nudge alone gives 36–85 mm). Kim not yet rebuilt (her L0 run was live)
- [x] L1 → L5 in order, each graduating before the next — both athletes through L5 (2026-10-24, commit 9d37030)
  - 2026-10-22: last night's L2 "graduation" predates the podium in the training model (run loaded 03:03, podium added 04:14): its left hand hits the podium (knockdown), and L1 best drops 94 → 28 % with the podium. Strike-only training had also wiped out the guard.
  - Fix: **one brain, every role**: each world plays guard / strike / absorb per the level's mix; a level passes only when every role's exam passes
  - [x] L1 guard with the podium: fine-tune reached 76 % (exam needs 95 %); now trained inside L2 as 30 % of worlds (GUARD exam = L1's) — graduated: `grandpa_L1_best.onnx`, `kim_L1.onnx`
  - [x] L2 strike 0.7 + guard 0.3, from the podium-aware L1 brain — graduated: `grandpa_L2.onnx`, `kim_L2.onnx`
  - [x] L3 absorb 0.6 + strike 0.4: one scripted slap to the face (impulse 3 → 15 N·s; 15 = estimated median Grandpa slap), opponent hand seen coming; pass = up + COM back within 5 cm after 5 s, ≥ 90 % — graduated: `grandpa_L3.onnx`, `kim_L3.onnx`
  - [x] L4 self-play in the arena (`envs/arena.py`, built + smoke-tested): KO rate per landed strike 10–40 %, striker self-falls ≤ 5 % — graduated: `grandpa_L4.onnx`, `kim_L4.onnx`, finals `*_final.onnx` (mixed-spar winners promoted 2026-10-24)
  - [x] L5 full match (`training/match.py`): 100 matches each without blow-ups (Grandpa 600/600 palm strikes 12.8 m/s, Kim 600/600 11.6 m/s); Unreal's match log = Python's event for event (12/12 incl. a KO, 22/22)

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
- [x] Reactive FX (user: GFX & audio picks) — key + striker's rim flare on a touch, truss chase + drained/vignetted grade on a KO, cheek flush (neck damage + sting, follows the head) and greyer skin (stamina) on `M_Grandpa` (`broadcast_fx.add_wear`), slap spatialised at the contact; `-SlapFxTest=<s>` fakes a strike + KO for checks (`SCREENSHOTS/261022_reactive_fx`). Skipped: MetaSounds friction, decals, mesh upgrade, perf HUD
- [x] Performance pass (60 fps), save, mark complete — re-checked 2026-10-24 with the trained champion brains at 540×960: 89–113 fps average (occasional ~50 ms shader/load spikes), comfortably over the 60 fps target
- [x] Eye contact (user 2026-10-24: "the person getting slapped should look his opponent in the eye before the slap") — gaze servo in BOTH engines, identical maths: the defender's neck goals walk to meet the striker's eyes for the whole turn, released the instant the slap lands (brace wins); observations untouched, no retraining. Bench 0.00°, live 4–6° (baseline 62°), L5 compare PASS 28/28, kills `SLAP_EYE_CONTACT=0` / `-SlapEyeContact=0` (`SCREENSHOTS/261024_eye_contact`)
- [x] Post-slap poise (user 2026-10-24: "after the slapper slaps he should stay standing up and balanced and await to see if his opponent falls") — final design after QA: the servo's post-touch contribution is the WATCH only (striker's eyes track the defender through the recovery window and the inter-turn hold, display-only, parity untouched); the BODY deliberately stays brain-commanded. An imposed pose hold was tried and rejected: freezing any joints after the touch fought the brain's balance and multiplied striker self-knockdowns (variants: melt-to-stand 6/6 turns, release-pose 7–9/3 matches, arm-only 6/4 — vs a ~1.7/5 baseline), even at barely-there strengths. Gaze bias lives in WORLD space (the chest whips during follow-through). Final battery: 5/5 KOs, self-downs 3/5, gaze 4.2° median. Lesson filed in SUMMARY.md
- [x] Anti-fall reward refinement (user 2026-10-25: "focus on not falling down after slapping for both of them") — the old economics paid strikers +146 for a clean hit vs only −30 for a self-fall (tumbling after a smack was profitable) and gave them flat zeros through recovery (no signal to ride out their own recoil). New reward in `envs/arena.py _reward()`: stick-the-landing payout (+0.5/upright-step after the touch ≈ a hit's value over a calm recovery), striker self-fall −150 (cancels a clean hit), box crossing −60; defender terms untouched (parity safe — training-side only). 4×60 min alternating L4 queue (`antifall_queue.json`). **Benchmark: self-downs 5 → 0 per 10 matches**, hits/speeds intact (11–16.5 m/s), KOs 5/10. Side effect: turn-ending fouls shifted from tumbles to box shuffles (10 → 25; no longer ending in falls). Brains reimported into `/Game/Brains` (levels pick them up in place)
- [x] Mobile portrait UI — Slate corner anchors in the safe zone (title, FPS, MENU | debug, build stamp), tabbed bottom-sheet menu, `-SlapUiTest` (`SCREENSHOTS/261022_mobile_ui`)
- [x] Menu actions (user 2026-10-22) — MATCH tab: pick each corner's brain (`/Game/Brains`, only those made for that corner's character), START NEW MATCH (`USlapController::RequestMatch`: brains built on the game thread, swapped on the physics thread, the match in progress abandoned unscored); ROSTER tab: every card's tape, style, brains (`SCREENSHOTS/261022_menu_actions`)

## Phase D — Roster: second athlete (Kim)
User 2026-10-22: "make this person the 2nd player in the roster, train her and give her a unique slapping and balancing style". Rule: a new mesh runs the whole ladder on her own before any pairing.
- [x] D1 `kimrigged.fbx` (Mixamo-style auto-rig, 28 bones) → our 17-bone athlete: `Scripts/rig_mixamo.py` (headless Blender) bakes the T-pose, scales to 1.63 m, merges vertex groups (spine1+spine2+clavicles → chest, toes → foot), 4 weights/vertex; `Content/Art/Raw/Kim_raw.blend` (original), `Kim_rigged.blend`, `Content/Art/Exports/Kim_rigged.fbx`, `training/characters/kim_skeleton.json`
- [x] D2 Card `kim.json`: 1.63 m, 60 kg, STR 0.60, SPD 0.95, FLX 1.00, TGH 0.85, fitter (stamina 0.035/strike), lighter neck (0.05/snap); `style` = her balancing stance + her reference-slap guess, timing, whip delays
- [x] D3 Body: A8 rig OK (length ranges widened for her short shin/forearm), A10 clean (thighs shrunk 10 %), A11 balance in her style stance: COM +1.2 cm ahead of mid-feet (plain upright: -8.1 cm, fell back), up 3 s; root lowered to 0.789 m so the bent legs stand on the floor. Torso bands now scale with height (Grandpa's model byte-identical). **2026-10-24: her model turned out stale** (predated the e73efa1 audit: no arm armature, solver iterations 100) — rebuilt all four body/arena variants at the audited spec and reimported her three levels
- [x] D4 Ladder L0 → L1 → L2 → L3: `overnight.py --char kim --queue kim_queue.json` — graduated: `kim_L0.onnx` … `kim_L3.onnx` (L4 via `kim_final.onnx`)
- [x] D5 Her reference slap (`slap_demo.py search --char kim`): 2nd search palm-first 8.9 m/s, stays up (1st: 3.8 m/s, hand on the podium). To redo after the action-rule change (e73efa1)
- [x] D6 Kim vs Grandpa test (user 2026-10-22: test the pairing now, before her ladder ends): `/Game/Levels/Arena_grandpa_vs_kim` (build_scene.py SLAP_RED=grandpa SLAP_BLUE=kim), per-corner fingerprints in USlapController, her mesh + M_Kim (wear params), her L0 WIP brain; bone-body gap 0.0000 cm both; UI test PASS on it (`SCREENSHOTS/261022_kim_vs_grandpa`)
- [ ] D7 After her ladder: her graduated brains ✅ (`kim_final_vs_grandpa.onnx` promoted from the head-to-head winner 2026-10-24), roster.json entry ✅ (Kim added; mixed tournaments now run — `tournament.py` plays every pairing), pairing levels ✅ (three arenas rebuilt at the audited model spec with champion defaults). Remaining: the menu itself picks the pairing level per corner character (today each pairing IS its own level; switching pairings = loading that level — deferred, dynamic level travel complicates Android packaging)
- [x] D8 16-slot roster (user: "I will eventually add 16 players"): ROSTER tab is a 4 x 4 grid, cards take `"slot"`, open slots are placeholders with the add-a-player steps. 16 athletes = 120 pairings: one arena model + level per pairing, built on demand

## Phase E — Android phone build (in progress)
Checked 2026-10-22: Android SDK/NDK r27c valid for UE 5.8 (Turnkey).
- [x] E1 MuJoCo 3.10.0 for android-arm64 (NDK CMake toolchain), URLab Build.cs Android branch (libmujoco.so staged; editor-only CoACD/ZMQ left out) — done: built with NDK r27c / Clang 18.0.3 via 	hird_party/MuJoCo/build_android.ps1, staged to 	hird_party/install/MuJoCo/lib/Android/arm64-v8a/libmujoco.so (43 MB); URLab.Build.cs patched with Android platform branch
- [x] E2 Brain on Android: NNE's platform-neutral Basic CPU runtime, the MLP rebuilt from the ONNX weights at load; desktop keeps ORT for the bit-exact matching check — done: NNERuntimeBasicCpu enabled in .uproject; USlapController::MakeNet falls back to NNERuntimeBasicCpu on Android/mobile
- [x] E3 Runtime data in the package: fingerprints, cards (read from 	raining/ today) → Content/Data, staged; /Game/Brains already always cooked — done: staged grandpa.json, kim.json, grandpa_fingerprint.json, kim_fingerprint.json to Content/Data/; LoadJson checks Content/Data/; DefaultGame.ini configured with DirectoriesToAlwaysStageAsNonUFS
- [x] E4 Mobile settings: portrait, arm64, Vulkan + ES3.1, Lumen off, textures ≤ 2K (Kim's FBX import also left 4 × 4K textures in /Game/Athletes/Kim, unused by M_Kim), **30 fps target** (user 2026-10-22) — done: AndroidRuntimeSettings (Portrait, arm64-v8a, Vulkan, ES3.1, minSDK 28, targetSDK 34) in DefaultEngine.ini; DefaultDeviceProfiles.ini (30 fps cap, Lumen off, max 2K texture); 4 unused 4K textures purged from Content/Athletes/Kim
- [ ] E5 Device run: install over adb, touch through the menu, a full match
