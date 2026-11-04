# Physics 3 (`build_body.py --physics 3` -> `training/models/v3/`), 2026-11-04

Physics 2 plus the four approved collision fixes from `DOCS/20261103/collision_review.md`, the KO reward, and the
audit the user added in the morning (continuous collision, mass ratios, momentum, CPU cost). v1 (shipped) and v2 rebuild
byte-identical; nothing is staged into `Content/Data` for v3. Brains trained on it are named `*_v3`.

## What changed

| # | Item | Physics 2 | Physics 3 |
|---|---|---|---|
| 1 | **The face** | the head sphere only; the jaw is not covered, a slap landing low hits the neck capsule (no strike) | two **jaw capsules** on the head body (chin to each jaw angle: `JAW_CHIN (0.62, 0, -1.15) r`, `JAW_ANGLE (-0.05, +-0.55, -0.75) r`, radius `0.28 r`, r = sphere radius), the head's mass split by volume (the body keeps its card mass) |
| 1b | **The head sphere's centre** | bone midpoint **plus** the mesh's absolute fore-aft centre: right only for a rig whose head bone sits at y = 0 | the drawn head's centre (the mesh's fore-aft middle at the bone's mid height). Moves Kim's sphere **9.8 cm forward** (it sat at the back of her skull: her face was 21 cm ahead of its centre), Matt's 4 cm back, Grandpa's 0.9 cm back |
| 1c | **What the referee counts** | any touch of the striker's body on the head sphere | a touch on a face geom (sphere, `jaw_l`, `jaw_r`) whose **contact point lies inside the head's `face_zone` site**: x from -0.45 r (just behind the ear line) to r + 3 cm, y +-(r + 3 cm), z from the jaw's bottom - 3 cm to +0.40 r (cheekbone/brow). The back and the crown of the head do not count (Blueprint 2.1: cheek and jaw). Same box test in `match.py` (`in_face_zone`), the arena and slap envs (`envs/common.in_face_zone`); the L2 target is a face too (turned to the striker, jaw, zone) |
| 2 | **Hand to neck / jaw** | the hand vs the neck is a default contact mixed with the stiff hand (sank 29 mm, 162 contact-steps in 8 gk matches) | explicit stiff pairs (solref 0.01, solimp 0.99/0.999, 15 mm margin) for each hand vs the other's head, both jaws and the neck |
| 3 | **Limb-on-limb** | stiff head and hands only | every athlete geom stiff, and explicit stiff early pairs for the fast parts (`LIMB_TARGETS`: hands vs head, jaw, neck, chest, belly, upper arms, forearms; forearms vs head, jaw, neck), the bodies' dynamic contact excluded: 76 pairs in an arena (v2: 16) |
| 4 | **Per-foot friction (K11)** | the floor's `priority="1"` won every contact: the per-foot randomisation (Blueprint 6) never reached a sole | the sole boxes carry `priority="2"`, `condim="4"`, the floor's friction `0.9 0.02 0.01` and the stiff solref: a sole contact takes the **sole's** friction. Unrandomised play is unchanged (0.9 as before); in training each sole now really gets its own 0.6-1.2 |
| 5 | **Momentum (audit)** | the strike impulse (card `strike_mass` x v over 10 ms on the defender's head) is an external force: it creates strike_mass x v (~28 N s for Grandpa at 9.4 m/s) of momentum per strike | the same impulse with its **reaction** on the striker's touching part and the arm above it (hand, forearm, upper arm, split by mass, at each body's COM): `match.reaction_chain`, `envs/arena.py`. `SLAP_KICK_REACTION=0` turns it off |
| 6 | **KO reward** (L4 arena env) | a KO ended the striker's episode as terminal: he lost the rest of the window's landing pay (0.5/step, up to 125) and the time-out bootstrap, against +40 | a KO is a **time-out for the striker** (bootstrapped) when he is up and in his box; **`KO_DISP`** pays the defender's COM drift once as its peak grows (3-15 cm, 36 at most); +40 KO bonus and the -150 / -60 self-fall / box penalties unchanged. Default on from physics 3, `SLAP_KO_REWARD=0` = old |

Checks: `verify_realism.py` [2c] (new): jaw on the head with the head's mass kept, the face zone holds both jaw
capsules and the cheeks but not the back or crown, the hand-head/jaw/neck pairs with the margin, every athlete geom
stiff, a sole's contact takes its own friction (floor 1.17, left sole 0.61 -> contact 0.61), v2 unchanged. A10 zero
overlap at reference, stand, swing and stride arcs and A11 balance pass for all three cards (Kim's COM moves from +1.2
to +1.9 cm fore of mid-feet with her head forward; limit 4). The arena stance has no contact but the floor.

## The drawn face vs the shapes

`Scripts/blender_face_vertices.py` (new, read only) exports every vertex skinned >= 0.3 to head + neck (the lower jaw is
neck-skinned on Grandpa, all of Kim's lower face). Jaw capsule surfaces (forward/side/down normals) to the nearest
drawn vertex: Grandpa median 30 mm (beard), Kim 17, Matt 33; no capsule point outside the skin. The cranium sphere at
the cheeks (`collision_mesh_clip.py`, + = inside the skin): Grandpa -6.7 -> -3.6 mm, **Kim -4.8 -> +4.1 mm**.

## Measured: physics 2 vs physics 3, the v2 brains, real matches

`Scripts/collision_review.py --brains v2 --matches 8` (now counts the jaw as the head) and `training/physics_audit.py
strikes --matches 8` (new), CPU MuJoCo, match.py's loop, jittered exam starts. gg `grandpa_L4_v2`, gk
`grandpa_final_vs_kim_v2` vs `kim_final_vs_grandpa_v2`, kk `kim_L4_v2_candidate` (Kim's best self-play checkpoint,
exported for this only).

| | physics 2 (gg / gk / kk) | physics 3 (gg / gk / kk) |
|---|---|---|
| palm vs face, sink median / p90 / max (mm) | 8.7/21.5/28.8, 14.3/30.8/43.5, 12.7/31.7/36.9 | 8.6/19.0/27.1, 10.3/27.7/37.6, 12.4/17.9/20.6 |
| **first contact already > 15 mm deep** (the one-step catch) | 8.6 %, 18.3 %, 25.8 % | 5.5 %, 4.8 %, 19.2 % |
| hand into the other's neck | none, 28.6 mm (162 contact-steps), none | **none** (the jaw and the neck pairs catch it) |
| body vs body, max (mm) | 19.6, 28.6, 0.3 | **4.7, 4.4, -** (target <= 15) |
| self, max (mm) | 6.2, 21.1, 27.1 | 6.0, 19.2, 23.4 (a hand pressed into its own pelvis, slow: not a collision fault) |
| sole into the floor, max (mm) | 12.8, 23.2, 7.6 | 18.6, 16.4, 7.5 |
| tunnelling (a hand sweeping through a face shape in one step with no contact) | 0 | 0 |
| drawn palm in the drawn face, with the browser's mesh clamp | 0 % clipping | 0 % clipping (gk, kk) |
| energy | KE jump max 8.9-9.3 J, qvel max 36-40 | 7.2-9.8 J, 29-49; no blow-up |

### 1. Continuous collision (tunnelling and late contacts)

MuJoCo 3.10 has no continuous (swept) collision. `nativeccd` and `multiccd` are the convex narrow-phase algorithms
(native GJK/EPA instead of libccd, several contact points for flat convex pairs); our shapes are primitives with
analytic pair functions, so they do nothing here: an open-loop 5 s replay with either flag off gives **0.000 mm**
difference on every body. What a 9-10 m/s palm (45-50 mm a step at 200 Hz) does on our shapes, measured:
**no tunnelling** in 48 matches (the hand box is 3 cm thick, the jaw capsules 4.8-5 cm, the sphere 17-18 cm, and the
pairs' 15 mm margin engages them early), but a **late first contact** - already 15-37 mm deep - in 5-19 % of
palm-face touches. The remedies inside 200 Hz physics / 50 Hz brain: the 15 mm margin (kept: a larger one holds a
resting palm visibly off the skin, since MuJoCo's contact settles at the margin), `gap` (detects early but only acts
inside margin - gap: no effect on depth), and sub-stepping only the strike (rejected: it changes the timestep, Blueprint
4, and the URLab parity). Physics 3's stiff pairs bring the late contacts down (gk 18 -> 5 %); the rest is the
one-step catch, pushed out in 1-4 steps, and the drawn palm is held off the drawn face by the browser's mesh clamp
(Unreal: still to port, `collision_review.md` item 1).

### 2. Mass ratios

Every body of every card on v3 equals card mass x Blueprint 2.2 (worst error 0.0 kg; head + neck 8.1 % for all three);
the head body's mass is split cranium/jaw by volume (Grandpa 4.66 + 2 x 0.36 kg), the body total unchanged.

### 3. Momentum exchange and head kinematics

Momentum of both athletes per step vs gravity, floor/podium contact forces and `xfrc_applied` (`physics_audit.py`):
the simulation itself closes to a p99 residual of 0.10-0.26 N s per step (the contact force read-out); the strike
impulse does not - on v1/v2 it adds strike_mass x v (median 2 x 14.1 N s for Grandpa, 2 x 11.1 for Kim) of momentum
nobody gave up. On v3 the reaction on the striker's arm makes the net external impulse 0 (audit: `kick_J` 0.0).
Effect on the v2 brains (20 matches, v3, reaction off -> on): gg KO 4 -> 2 %, self-falls 0 -> 2 %; gk KO 9 -> 7 %,
self-falls 10 -> 8 %; kk KO 39 -> 27 %, self-falls 0 -> 0 %. No blow-ups; accepted.

Head kinematics after a strike (physics 2, gg, strike 9.4 m/s): head speed peak 3.3 m/s, 31 g, 2580 rad/s^2 (kk: 4.3
m/s, 44 g). Reference: Walilko, Viano & Bir, *Biomechanics of the head for Olympic boxer punches to the face*, Br J
Sports Med 2005;39:710-719 - punch speed ~9 m/s, effective punch mass ~2.9 kg, head delta-V ~3.2 m/s, ~58 g,
~6300 rad/s^2 (a Hybrid III head hit by a gloved fist; no comparable open-hand slap measurement found). The head
**velocity** matches; the peak accelerations read 1.5-2x lower because match.py differences positions over 5 ms steps
and the impulse is spread over 10 ms (a real impact pulse is ~10 ms with a sharper peak) - it is a read-out limit, not
a momentum one. The card's strike_mass (3.0 kg Grandpa) sits on the cited effective mass.

### 4. CPU cost

Open-loop replay of a recorded 5 s match (gk, best of 3, one core, the machine busy with GPU work): v1 124-161 us per
step, v2 207-214, **v3 170-204** (the stiff contacts and pairs; v3's 76 explicit pairs cost no more than v2's 16 here),
i.e. ~1.6 s of physics per 8000-step match. Candidates, with the largest body deviation over 5 s against v3 as built:
`tolerance 1e-6` 0.002 mm but no faster; `iterations 10` 300 mm; `noslip 2` 297 mm and 2x slower; elliptic cones 1.8 m;
island off slower; `ls_iterations 25` identical, no faster. **None accepted**: nothing faster stays within the 1 mm
parity tolerance (Blueprint 8), and the options are part of the URLab parity.

## The v2 brains on physics 3 (C): starting points

`match.py exam --matches 20` (L5: no blow-ups in any):

| pairing | physics 2: landed / KO per strike / self-falls / hand m/s | physics 3 (the final build) |
|---|---|---|
| gg `grandpa_L4_v2` | 99/99, 7 %, 2 %, 7.7 | 113/113, 2 %, 2 %, 6.7 |
| gk Grandpa red vs Kim | 105/106, 4 %, 0 %, 7.7 | 59/108, 7 %, 8 %, 1.7 |
| kg Kim red vs Grandpa | 101/104, 5 %, 2 %, 7.0 | 50/102, 10 %, 9 % , 2.4 (first build) |
| kk `kim_L4_v2_candidate` | 113/120, 0 %, 0 %, 7.8 | 55/65, 27 %, 0 %, 3.3 (many turns end early: the defender down before the strike) |

Grandpa's self-play brain transfers (every strike lands, cheek and jaw); everything that faces Kim breaks: her head now
sits 9.8 cm further forward, on her drawn face, and the referee counts only the face zone - the v2 slaps aimed at the
old sphere land half as often and slowly. So: Grandpa from `grandpa_L4_v2`, Kim from `kim_final_vs_grandpa_v2` (her
only graduated v2 brain), and the whole ladder L2 -> L4 for both, then the mixed pairings.

## The KO reward A/B (B)

Kim L4 vs Grandpa (frozen `grandpa_final_vs_kim_v2`), from `kim_final_vs_grandpa_v2`, physics 3 (first build), two
runs side by side, 4096 worlds, 15 min each (the GPU at 88 C throttled to 180 MHz: 3.1 M steps each, a small run).
Exam: `runs/exam_v3/exam_ab.py`, 400 worlds on the final v3 build; "Kim striking" forces her to strike every turn.

| | matchup: strikes / KO per strike / self-falls | Kim striking: strikes / KO / self-falls |
|---|---|---|
| start (`kim_final_vs_grandpa_v2` on v3) | 47 % / 14.4 % / 13.8 % | 74 % / 5.4 % / 5.2 % |
| A: old reward | 64 % / 10.9 % / 17.0 % | 84 % / **1.8 %** / 7.2 % |
| B: KO reward | 52 % / 15.5 % / 15.3 % | 66 % / **3.8 %** / 7.8 % |

Within 15 minutes A trained KO away (5.4 -> 1.8 %: the termination penalty at work), B kept and doubled it relative
to A (3.8 %) with the same self-falls; B landed fewer strikes (66 vs 84 %). The run is short (3 M steps against the
retrain's ~60-90 M per stage), so this is a direction, not a result. **Applied to all characters**: the termination
penalty is a property of the reward, not of Kim, and it pulled every brain away from KOs; the L4 exam band (10-40 %
KO per strike, strikes >= 90 %, self-falls <= 5 %) is the guard against overshooting (Grandpa's v2 self-play stood at
38 %).

## The retrain queue (`training/runs/retrain_v3_queue.json`, copy in `training/configs/`)

| # | char | level | min | from |
|---|---|---|---|---|
| 1 | grandpa | L2 | 45 | `runs/grandpa_L4_v2_night3_0109/best.pt` (critic warm-up 20, imitation 0.3) |
| 2 | grandpa | L3 | 45 | stage 1 |
| 3 | grandpa | L4 self-play | 75 | stage 2 |
| 4 | kim | L2 | 45 | `models/brains/kim_final_vs_grandpa_v2.pt` (warm-up 20, imitation 0.3) |
| 5 | kim | L3 | 45 | stage 4 |
| 6 | kim | L4 self-play | 75 | stage 5 |
| 7 | grandpa | L4 vs Kim (stage 6, frozen) | 75 | stage 3 |
| 8 | kim | L4 vs Grandpa (stage 7, frozen) - the gate | 75 | stage 6 |
| 9 | grandpa | L4 vs Kim (polish) | 45 | stage 7 |
| 10 | kim | L4 vs Grandpa (polish) | 45 | stage 8 |
| 11 | matt | L2 | 90 | `models/brains/matt_L1.pt` (warm-up 20, imitation 1.0 of the v1 reference: Matt has none on v2) |
| 12 | matt | L3 | 60 | stage 11 |
| 13 | matt | L4 self-play | 60 | stage 12 |

780 min of training + ~5 min per stage (exams, start-up, cool-downs) = **~14.1 h**; every stage `no_gate` (the queue
goes on) and stops early when it graduates. Slap references in `models/v3/`: Grandpa's and Kim's v2 references
(the v3 torques are v2's), Matt's v1 one. Outputs: `models/brains/<char>_<level>_v3.onnx` on graduation,
`<char>_final[_vs_<x>]_v3.pt/.onnx` at the end. Report: `runs/report_retrain_v3.cmd` waits for overnight.py to exit,
then writes `DOCS/20261104/retrain_v3_report.html`.

## What Unreal (`Source/`) and the browser (`Web/sim.js`) must mirror before a v3 brain ships

Both read the version from the fingerprint (`"physics": 3`); every item is a `Physics >= 3` branch.

1. **The face set.** `D.Face = {<prefix>head, <prefix>jaw_l, <prefix>jaw_r}` (geoms by name). The strike loop
   (`SlapMatch.cpp` ~L261 `C.geom[0] != D.Head ...`; `sim.js checkTouch` `g0 === D.head ...`) tests membership of the set
   instead of `== Head`.
2. **The face zone test**, after the set test: site `s = <prefix>face_zone`; `r = C.pos - site_xpos[s]`;
   `local[k] = sum_i site_xmat[9 s + 3 i + k] * r[i]`; a strike only if `|local[k]| <= site_size[3 s + k]` for k = 0, 1, 2
   (`match.in_face_zone`). A face touch outside the zone is not a strike (no event, no impulse): the turn goes on.
3. **The kick's reaction.** At the touch, `match.reaction_chain`: the touching geom's body and its parents while their
   name (without the prefix) starts with `hand`, `forearm` or `upperarm`; mass shares `body_mass / sum`. For the
   KICK_STEPS (2) physics steps of the kick: `xfrc_applied[b, 0:3] = -share_b * kick_force` (no torque), zero after.
4. Nothing else: the knockdown rule already counts the jaw (any non-sole geom of the athlete), the head readouts and
   the gaze use the `head` geom (it moved for Kim: her eye line and her obs change with the model, not the code), and
   URLab imports `priority`, `margin`, `<pair>` and `<exclude>` (MjGeom, MjContactPair). Then the URLab `.mjb` parity
   (`replay_check.py`, `match.py compare --physics 3 --mjb ...`) and `node Web/check.mjs` on v3.
5. Unreal's drawn hand clamp (`collision_review.md` item 1) is still open; the browser's mesh clamp already holds the
   drawn palm off the drawn face on v3 (0 % clipping).

## Started

The queue runs: `overnight.py --hours 15.5 --queue retrain_v3_queue.json`, **PID 43704**, started 2026-11-04 09:31,
hidden (`Start-Process -WindowStyle Hidden`, stdout/stderr `training/runs/retrain_v3_overnight.log/.err`), ETA
~23:40 tonight (15.5 h hard deadline 01:01). The report watcher (`runs/report_retrain_v3.cmd`, cmd PID 55984, hidden)
polls every 5 min for an `overnight.py` python process and then writes `DOCS/20261104/retrain_v3_report.html`.
TensorBoard (the 11-03 instance, :6006) already watches `training/runs`. The physics 3 envs' checkpoints carry
SLAP_KO_REWARD and SLAP_KICK_REACTION at their defaults (both on).
