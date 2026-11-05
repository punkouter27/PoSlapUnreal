# Mom joins the roster (2026-11-05)

User: "add this model to the app as another player. must be trained" (`Downloads/momrigger/MomRigged.fbx`, AccuRig).
Card choices (user): 1.68 m / 80 kg, "tough and stubborn", train through L2 tonight.

## Rig (`Scripts/rig_mixamo.py`, new AccuRig/CC path, picked when the armature has `spine_01`)
- UE5 Mannequin names mapped onto our 17 bones; twist/finger/toe/breast/face bones join the anchor above them.
- A-pose arms straightened to Grandpa/Matt's rest (straight, 10 deg below horizontal, palms down).
- 92 CC shape keys (visemes/expressions) dropped: an armature can't be applied over them. Cheek morphs come later (`blender_cheek_morphs.py`).
- AccuRig's arm joints failed A8 (right shoulder 7 cm inside the chest, both elbows at the sleeve hem, upper arm 0.10 H).
  Fix: shoulders mirrored to the wider one (= the measured armpit edge), elbow at 56 % shoulder->wrist (0.186:0.146 H),
  upper/forearm weights re-split along the arm with a 6 cm blend; toe tips mirrored (the scan's right foot turns out).
- Outputs: `Content/Art/Raw/Mom_raw.blend`, `Mom_rigged.blend`, `Content/Art/Exports/Mom_rigged.fbx`, `training/characters/mom_skeleton.json`.

## Card and bodies
- `training/characters/mom.json`: strength 0.6, speed 0.7, flexibility 0.9, toughness 1.2, stamina 0.03, neck damage 0.03, strike mass 3.08 kg.
- Stance balanced by hip flexion (~1.1 cm COM per deg): knees 15, hips 3, ankles 11, waist 4 -> COM +0.1 cm.
- A8-A13 pass; solo + all 7 pairings with Grandpa/Kim/Matt built for physics 1 and 3. `verify_realism` passes; `verify_cards` only lacks `mom_final.onnx`.

## Reference slap: none clean
`slap_demo.py search` (150 it, twice) found no standing palm hit. Causes measured:
- every athlete drifts onto the podium ~0.7 s into passive stand targets (Grandpa 0.74 s, Matt 0.69, Mom 0.73), so the slap must land first;
- her reach is short like Kim's: best static pose leaves the hand centre 17 cm from the face (Kim 16, Grandpa 4, Matt 1);
- a lead hand carried in front hits the podium in the wind-up (the guess now keeps it at her side).
The falling result is kept as `models/mom_slap_demo_v1_falls.json` (not loaded). L2 trains with `--imit-start 0`
(no imitation: a falling reference taught Matt to fall, tasks.md J10) and `--reach-start 0.95`.

## Training (physics 1, the shipped physics; no v3 brain has graduated yet)
Queue `training/runs/mom_queue.json` via `overnight.py --hours 9 --char mom --queue mom_queue.json`, from 13:15:
L0 90 min -> L1 90 min -> L2 360 min. Log `runs/mom_queue.jsonl`; console `runs/mom_queue_console.log`.

## Not done
S2 walk-in brain (scripted stride is used), L3/L4 and the duels, Unreal arena level (needs `build_scene.py` with the editor),
web GLB, Android `.bin` export.

## Stopped 14:27 (user: finish up)
L0 stopped at 69 of 90 min, not graduated: 196.6 M steps, return 2,124 (target 2,400), falls 56 % at push 0.84 of full (needs >= 95 % no-fall at full). Rising: falls 68 % at push 0.58 (17 min) -> 44-56 % at 0.74-0.84. GPU throttled at 88 C (~40 % slower after 45 min). Checkpoint `runs/mom_L0_night1_1315/latest.pt`; candidate `models/brains/mom_L0_candidate.onnx` (not graduated). Resume: `overnight.py --char mom --queue mom_queue.json` with the L0 stage's `"resume"` set to that latest.pt.
