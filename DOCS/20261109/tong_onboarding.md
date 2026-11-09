# Tong joins the roster (2026-11-09)

User: "add new playing Tong to the roster" (`Tong.glb`, dropped in the repo root). The renders showed a baby in a onesie,
0.76 m tall, so Claude paused and asked; the user chose option 1: **a full-size fighter, the baby look a costume**
(not real toddler scale, not a mascot). Card values were not given: Claude picked them (below).

## Policies touched

Only Tong's own (new character -> its own training run, AGENTS.md section 3). No other brain, body or rule changed.
Grandpa, Kim, Matt and Mom's models are byte-identical except `models/v3/mom_fingerprint.json`, whose card text caught
up with Mom's current card when the Mom vs Tong pairing was built on physics 3 (no physical value changed).

## Rig

`Content/Art/Raw/Tong.glb` arrived rigged by an AI tool (19 bones: hips, spine, chest, neck, head, shoulder / upperarm /
forearm / hand.L|R, thigh / shin / foot.L|R), arms bent up, one leg stepped out, 12 cm off centre.
- `Scripts/rig_auto.py` (re-skin from scratch) found no heat-weight solution on this mesh (0 vertices weighted). It now
  drops a rig that arrives with the model before re-skinning, which any later rigged GLB needs anyway.
- New `Scripts/rig_glb.py`: keeps the model's own weights, renames the bones to Mixamo's, adds the toe / hand-end
  helpers, turns arms onto Grandpa's rest line (T-pose, 10 deg down) and legs straight down, mirrors the rest skeleton
  left to right, centres on the hips, writes a T-posed FBX. Then `Scripts/rig_mixamo.py ... tong 1.75` as for Matt.
- Result: `Content/Art/Exports/Tong_rigged.fbx`, `training/characters/tong_skeleton.json`; 17 bones, 21,513 vertices, 0
  unweighted; x2.26 scale.

## Card (`training/characters/tong.json`, slot 5) - values picked by Claude

| | Tong | Range on the roster |
|---|---|---|
| Height, mass | 1.75 m, 75 kg | 1.63-1.91 m, 60-109 kg |
| Strength | 0.65 | 0.50-0.85 |
| Speed | 0.90 | 0.55-0.95 |
| Flexibility | 1.00 | 0.85-1.00 |
| Toughness | 0.90 | 0.85-1.20 |
| Stamina per strike / neck damage per snap | 0.04 / 0.045 | 0.03-0.05 |
| Strike mass | 2.88 kg (3.0 per 78 kg) | |

Quick and supple, middling power, a lighter neck than the adults. The body keeps a baby's proportions at adult size: a
head sphere of 17.7 cm radius (Mom 7.9) and short thighs (0.18 H). `build_body.py` A8's length ranges are a scale/axis
sanity check (its own comment), so a card may now widen one with a stated reason: Tong's `length_ratio` thigh 0.16-0.28.
Stance: Mom's, hips extended 6 deg -> COM -0.7 cm fore/aft (A11 OK). `sparring.head` = (0.80, 0, 0.78): Mom's target
plus the 6 cm Tong's pelvis sits lower.

## Bodies

A8-A13 pass on physics 1 and 3; solo + all 8 pairings with Grandpa / Kim / Matt / Mom, both corner orders, no contacts
at stance. A11's passive drop test (PD holding the stance, no brain) tips Tong backward at 1.3 s whatever the stance;
the other athletes "pass" it only because they drift forward onto the podium (Mom: belly on the podium at 0.76 s).
Standing is L0's job, as for every athlete.

## Brain

`train.py --char tong --level L0 --minutes 30 --physics 1 --run tong_L0_1109`, from scratch (one brain per character).
The trainer's startup prune removed `runs/mom_L0_night1_1315` (from 2026-11-05, over 24 h old, no queue resumes it: the
v4 queue already resumed from it). The first start failed: the card had no `sparring` block (added).

## Audio

Voice pack: 100 placeholders cast at 330 Hz (`Scripts/voice_pack.py` VOICES: the costume's high, bright voice).
Announcer: 9 Tong lines (introductions, wins, trouble, champion). Like Mom, no grunt / breath of its own yet.
