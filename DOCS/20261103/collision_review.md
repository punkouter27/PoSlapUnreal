# Collision review: agent/agent and agent/scene, physics 1 (shipped) and physics 2 (`models/v2`), 2026-11-03

Real matches, CPU MuJoCo, `training/match.py`'s own loop (jittered exam starts, `EXAM_JITTER`), the shipped brains:
gg `grandpa_final`, gk `grandpa_final_vs_kim` vs `kim_final_vs_grandpa`, kk `kim_final`. 8 matches per pairing per
physics version, 48 matches, 263 turns, ~1.3 M physics steps. Nothing under `training/` was changed: the scripts wrap
`match.muscle_step` to read `d.contact` after each step.

| Script | What |
|---|---|
| `Scripts/collision_review.py --physics 1\|2 --pairing gg\|gk\|kk --matches 8` | per pair class: penetration, duration, approach speed, restitution, slip, friction use, energy; poses at every palm-head contact (`Saved/collision_review/p<v>_<pair>.json`, `_poses.npz`) |
| `Scripts/blender_rest_vertices.py` (Blender 5.2, headless, read only) | the hand and head vertices of the rigged meshes at rest, with normals (`Saved/collision_review/<name>_rest.npz`) |
| `Scripts/collision_mesh_clip.py` | the drawn palm vs the drawn face at every palm-head contact step, each vertex carried on its body as `Web/art.js` draws it; raw, with the old browser clamp, with the new mesh clamp (`mesh_clip.json`) |

Restitution = separation speed / approach speed along the contact normal (approach > 0.3 m/s only). Friction use =
|tangential force| / (mu x normal force) from `mj_contactForce`; 1.0 = sliding. KE jump = the largest rise of the
kinetic energy in one 5 ms step outside the slap's 10 ms impulse.

## Measured

Penetration in mm (median / p90 / max over contact-steps), contact duration in ms (median / max, per continuous contact).

| Pair class | Physics 1 (gg / gk / kk) | Physics 2 (gg / gk / kk) |
|---|---|---|
| **palm vs head** (a hand box on the other head) | pen 27/57/75, 22/50/74, 24/40/61; dur 55/75, 15/90, 30/45; approach 9.0, 8.1, 7.7 m/s; restitution 0.11, 0.03, 0.27 | pen 4/4/4 (1 touch), 10/25/30, 9/22/25; dur 5, 15/25, 13/20; approach 3.5, 9.1, 8.0 m/s; restitution 0.24, 0.12, 0.23 |
| other part vs head (forearm) | max 5, 15, - mm | none |
| **hand / forearm vs the other's neck** (the slap landing low) | hand_r-neck max 52 mm (732 contact-steps, gg), 32 (gk), 43 (kk); forearm_r-neck 30-49 | hand_r-neck max 12-15 mm, forearm_r-neck 20 mm (kk) |
| body vs body (arms, chest: bumps and blocks) | median 3.5-22, max 32-52 mm (incl. the neck rows above); slip median 0.7-1.1 m/s; friction saturated 41-67 % | median 1.9-3.2, max 10-30 mm (hand_r into the guard's upper arm 30 mm, gk); slip 0.55-1.4 m/s; saturated 37-53 % |
| self (one athlete's own parts) | median 0.2-0.9, max 20-39 mm (hand_l into own pelvis/belly, Kim) | median 0.08-0.7, max 12-29 mm; the welded pairs now collide (belly-upper arm 5 mm max) |
| **sole vs floor** | median 0.6-0.7, p90 1.0-1.5, max 12-34 mm; slip median 0.01 m/s; friction use median 0.16-0.21, saturated 0.8-1.8 %; no bounce | median 0.04-0.16, p90 0.35-2.4, max 7.5-19 mm; slip 0.0-0.01 m/s; use 0.21-0.35, saturated 6-8 % (condim 4) |
| non-sole vs floor | none (no knockdown in these 24 matches) | upper arms 18 mm (a fall, gk) |
| **hands vs podium** (the defender's brace, the striker's lean) | median 0.1-0.5, max 1.2-12.6 mm; friction use 0.64-0.97, saturated 10-49 % (gg: the brace slides) | median 0.07-0.5, max 0.8-13 mm; saturated 0-30 % |
| belly / chest / forearm vs podium | max 0.4 mm | max 1.2-5.1 mm |
| sole vs podium | none | max 7.6-8.5 mm (gg, gk: a foot into the podium's base) |
| energy | no blow-up in 24 matches; joint speed max 18.6-28.4 rad/s; KE jump max 1.7-4.3 J | no blow-up in 24 matches; joint speed max 30.5-41 rad/s; KE jump max 2.6-11.2 J |
| strikes landed / turns | 45/45, 42/42, 48/48 | 5/37, 38/41, 42/48 (the shipped brains on physics 2, as `training_realism.md` found) |

## The drawn meshes vs the collision shapes

| | Grandpa | Kim |
|---|---|---|
| head sphere vs the drawn cheek | the sphere's sides stand 6.7 mm **outside** the skin | 4.8 mm outside |
| the drawn jaw / chin | below the sphere: not covered | below and behind (hair): not covered |
| drawn hand vs the hand box (p90 of vertices outside the box) | 43 mm: the right hand is curled and rolled ~30 deg about the forearm against the box; the left hand is the scan's fist | 33 mm (fingers past the box's end); the palm plane matches the box (thin axis aligned) |

Where the palm lands on the drawn face (the deepest 5 % of the hand's vertices, in the defender's head frame): gg
straight **under** the sphere (the jaw and chin), kk under and behind (jaw, neck, hair), gk 60 deg off the front and
above (cheek and temple: Kim's taller opponent).

The drawn palm's depth in the drawn face at palm-head contact steps (deepest 5 % of the hand's vertices, mm; clipping =
share of contact steps with that depth > 0):

| | samples | raw: median / p90 / max, clipping | old browser clamp (hand box off the sphere) | **new mesh clamp** (`Web/art.js` clipPush) |
|---|---|---|---|---|
| physics 1 gg | 190 | 28 / 31 / 33, 98 % | 27 / 31 / 33, 98 % | -16 / -11 / -7, **0 %** |
| physics 1 gk | 408 | 18 / 35 / 38, 80 % | -8 / 35 / 37, 24 % | -10 / -7 / 7, **0.2 %** |
| physics 1 kk | 158 | 33 / 35 / 36, 100 % | 33 / 35 / 37, 100 % | -7 / -4 / -1, **0 %** |
| physics 2 gk | 20 | -5 / 7 / 18, 25 % | -11 / -1 / 7, 10 % | -10 / -7 / -5, **0 %** |
| physics 2 kk | 42 | 34 / 35 / 37, 100 % | 34 / 35 / 37, 100 % | -9 / -6 / -4, **0 %** |
| physics 2 gg | 0 | (the shipped brains landed 1 palm in 8 matches) | | |

The old clamp only pushed the hand box off the head sphere along the centre line. In the mirror matches the palm is at
the jaw, below the sphere, so the clamp never fired while the drawn palm sat ~3 cm inside the drawn jaw on almost every
contact frame. The new clamp works on the meshes themselves: the hand's and the head's vertices (weight >= 0.5) in
their MuJoCo body's frame from the bind pose; at a contact the drawn hand moves out along the mean normal of the face
vertices it is under, by the 95th-percentile depth + 2 mm, up to 5 times (checked in Python on the same frames with the
same rule). It errs outside: the deepest 5 % of the palm ends ~1 cm off the skin (the face curves away from a push
along the mean normal); at a 1-3 frame contact it reads as a touch. Cost (Node, brute force): 0.36 ms per hand at the
face with 530 hand x 640 face vertices (the Blender meshes; the web GLBs are decimated, so less), 0.07 us otherwise.
The browser runs the same rule a second time against the neck-skinned vertices (the lower jaw is skinned to the neck
bone, Kim's whole lower face): the numbers above cover the head-skinned face only.

## Verdicts

| Pair class | Physics 1 (shipped) | Physics 2 | Fix | When |
|---|---|---|---|---|
| **palm vs head** | **Not realistic.** The palm sinks 2-7 cm into a rigid skull's sphere for 15-90 ms. A slapped cheek compresses ~1-2 cm, and a slap's contact lasts ~5-15 ms | **Realistic.** Median 1 cm, max 3 cm (the one-step catch of a 9 m/s palm at 200 Hz), 12-25 ms, restitution 0.1-0.25 (flesh: no bounce) | none needed past physics 2. The residual one-step overshoot is display work (done: the mesh clamp) | the physics-2 retrain |
| **palm vs jaw / neck** (the slap landing low) | **Not realistic.** The head sphere stops above the jaw: in mirror matches the palm lands on the neck capsule first (up to 5 cm deep, a soft default contact), which is **not a strike** (no impulse, no `strike` event) until the hand slides up into the sphere. Visually the drawn palm sits 3 cm inside the jaw | **Better, not right.** Neck contacts 1.2-2 cm (the stiff hand geom mixes with the default neck), still no strike on the jaw | **Physics 3 proposal:** a jaw capsule on the head body fitted to the mesh's lower face (the rest-vertex data from `Scripts/blender_rest_vertices.py` gives its extent: Grandpa's head-skinned chin reaches 14 mm below the sphere, and the jaw below that and Kim's whole lower face are skinned to the neck bone, so the fit must take the neck-weighted face vertices too), part of the face that counts as a hit (the strike check's head geom set, all three engines), and an explicit stiff `<pair>` of each hand with the other's neck and jaw (solref 0.01, solimp 0.99/0.999, the 15 mm margin), as physics 2 does for hand-head. Needs a retrain and the `.mjb` parity re-run | after the physics-2 retrain |
| body vs body (arms, chest, guard) | **Not realistic in depth.** 2-5 cm into an arm or the chest at 6-10 m/s (the default solref 0.02); sliding and low restitution (0.1-0.3) are right | **Borderline.** Max 3 cm (hand into the guard's upper arm); 1-2 cm is soft-tissue plausible | physics 3: solref 0.01 on cross-athlete limb pairs (or on the forearm/upper-arm geoms), keep the damping ratio 1 | after the retrain |
| self (own parts) | Realistic in the median (0.2-0.9 mm); the hands resting on the belly/pelvis press 2-4 cm in at most | Realistic; the five welded pairs now collide (they never did in physics 1); max 1.2-2.9 cm (hand into own pelvis) | physics 3: stiffer hand geom vs own trunk (same as above) | after the retrain |
| **sole vs floor** | Realistic for standing (sub-mm, planted, no slip, friction well inside the cone, no bounce); a stomp sinks 3.4 cm, too soft | **Realistic.** Sub-mm median, max 0.75-1.9 cm (a shoe sole and a padded mat together compress ~1 cm), torsional friction (condim 4) | none | physics 2 |
| non-sole vs floor (knockdown) | not exercised (no knockdown in 24 matches); earlier reports: same softness as the soles | an upper arm 1.8 cm on a fall: plausible (a padded mat) | none | - |
| **hands vs podium** | Realistic depth (sub-mm median, max 1.3 cm); the defender's brace slides (friction saturated 49 % in gg): plausible for palms on a lacquered top | Realistic (max 1.3 cm, saturated 0-30 %) | none | - |
| body / sole vs podium | Realistic (0.4 mm) | Realistic (feet into the base 0.8 cm, belly 1.2 mm) | none | - |
| energy / blow-ups | Stable: no blow-up, KE never jumps more than 4.3 J in a step outside the slap | Stable: no blow-up; the stiffer contacts inject up to 11 J in a step (vs ~120 J in a 3 kg x 9 m/s slap): fine | keep the 1e3 qvel blow-up guard; watch it in the retrain's exams | - |
| drawn palm vs drawn face | the old browser clamp left the drawn palm 3 cm in the jaw on 98-100 % of mirror-match contacts | same (the meshes do not change with the physics) | **done (browser):** the mesh clamp, 0-0.2 % clipping. **Open (Unreal):** `SlapAthleteVisual.cpp` has no hand clamp at all | browser now; Unreal next |
| drawn hand vs hand box | Grandpa's right hand curled and rolled ~30 deg off the box, his left a fist; Kim's line up | same | art: a flat open-palm pose for Grandpa's right hand in Blender would need the bone's rest frame unchanged (it is: only the finger geometry would move) - not done, it changes the scan's look; the mesh clamp covers the visible symptom | a human decision |

## Left for Unreal (`Source/`, not touched here)

1. **Hand clamp.** Port `Web/art.js` `clipData` + `clipPush` to `SlapAthleteVisual.cpp`: at bind time keep the hand_l /
   hand_r / head vertices (weight >= 0.5) in their body's frame (UE: the skeletal mesh's render data, LOD 0, or a
   build-time export of the same arrays), and in the bone update move the hand bone out of the other athlete's face
   (mean normal of the nearest face vertices, 95th-percentile depth + 2 mm, up to 5 passes). Drawing only.
2. **Physics-2 strike impulse** from the touching geom's velocity (already listed in `training_realism.md`).
3. If physics 3 adds a jaw capsule, the referee's face set and the strike check change in all three engines.
