# Movement Blueprint — PoSlap v1

Status: **DRAFT — awaiting approval.** No code until approved.

One rule above all: **MuJoCo moves the athletes; Unreal only draws them.** Chaos is off for athletes, podium, floor and props.
The same model file, the same MuJoCo version and the same timing are used in training and in Unreal.

---

## 1. Character Card (one per athlete)

Every athlete is a mesh plus a card. New meshes later = new card, same pipeline, own training run.

| Attribute | Grandpa (v1) | What it changes |
|---|---|---|
| Mesh | `GRANDPA.glb` (rigged in Blender) | Look, limb lengths, collision-shape sizes |
| Height | 1.70 m | Mesh is scaled to this (file is 1.07 m today) |
| Mass | 78 kg | Split across body parts (§2.2) |
| Strength | 0.50 × trained adult | Max motor force at every joint |
| Speed | 0.75 × trained adult | Max joint speed |
| Flexibility | 0.85 × adult range | Joint limits shrink toward neutral |
| Tint | Red / Blue | Two copies of the same athlete |

## 2. Body Setup

### 2.1 Body parts and collision shapes

Simple shapes fitted *inside* the mesh skin so contacts look right.

| Part | Shape | Notes |
|---|---|---|
| Head | Sphere | **Face zone** = thin box on the cheek/jaw, the only legal target |
| Neck | Capsule | Short; carries the head's wobble |
| Chest, Belly, Pelvis | 3 capsules | Pelvis is the root (free-floating body) |
| Upper arm, Forearm | Capsules | |
| Hand | Flat box, standard hand length (10.8 % of height) | **Palm side** = the scoring surface; back of hand/wrist does not score. The scan's left hand is a fist, so its look ≠ the physics hand |
| Thigh, Shin | Capsules | |
| Foot | One box = the **sole** | The foot box is the only part allowed to touch the floor (a rolled shoe edge is not a knockdown) |

**Self-collision:** every pair of parts collides *except* a part and its direct parent (e.g. forearm–upper arm).
Checked automatically: zero overlap in the reference pose, the rest stance, and through a full slap swing arc.
Athlete-vs-athlete contact is on: palm vs face, plus any accidental body bumps.

### 2.2 Weight distribution (standard human proportions, scaled to card mass)

| Part | % body mass | Grandpa (kg) |
|---|---|---|
| Head + neck | 8.1 | 6.3 |
| Chest | 21.6 | 16.8 |
| Belly | 13.9 | 10.8 |
| Pelvis | 14.2 | 11.1 |
| Upper arm (each) | 2.8 | 2.2 |
| Forearm (each) | 1.6 | 1.2 |
| Hand (each) | 0.6 | 0.5 |
| Thigh (each) | 10.0 | 7.8 |
| Shin (each) | 4.65 | 3.6 |
| Foot (each) | 1.45 | 1.1 |

Centre of mass in neutral stance must sit over the middle of the feet (checked automatically when the body is built).

### 2.3 How the joints bend — 30 motors

| Joint | Ways it bends | Why it matters here |
|---|---|---|
| Waist (belly) | 3: twist, lean fwd/back, lean side | **Kinetic chain**: hips → trunk twist → shoulder → hand |
| Neck | 3: nod, turn, tilt | Head snaps on impact (head g-force); neck bracing |
| Shoulder (each) | 3: raise fwd, raise side, rotate | Wide horizontal swing |
| Elbow (each) | 1: bend | Whip at the end of the swing |
| Wrist (each) | 2: bend, side-tilt | Keeps the **palm** (not wrist) leading |
| Hip (each) | 3: fwd/back, side, twist | Stance, weight shift, hip drive |
| Knee (each) | 1: bend | Absorbing the hit, staying low |
| Ankle (each) | 2: up/down, roll | Balance on planted feet |

Every joint is a soft motor (spring + damper toward a target the brain picks), capped by the card's strength and speed.
Fingers are fixed (open palm), not simulated.

## 3. Coordinate Matching (MuJoCo → Unreal)

| | MuJoCo | Unreal |
|---|---|---|
| Up | +Z | +Z |
| Handedness | Right-handed | Left-handed |
| Units | metres, kg, seconds | **centimetres**, kg, seconds |

**Conversion rule** (every frame, every body part):

- Position: `UE (x, y, z) = MuJoCo (x × 100, −y × 100, z × 100)`
- Rotation: `UE quat (w, x, y, z) = MuJoCo quat (w, −x, y, −z)` (Y is mirrored)
- Facing: athletes face each other along the X axis. Red at −X, Blue at +X. Unreal's camera sits on the side.

URLab already does this conversion internally. We **verify** it in Phase B by putting markers on 3 known points (ground origin,
1 m along +X, 1 m along +Y) and checking they land at (0,0,0), (100,0,0), (0,−100,0) cm in Unreal. We don't re-implement it.

The skinned Grandpa mesh is moved bone by bone: each MuJoCo body part's position and rotation is copied to the matching bone. That's drawing only; no physics.

## 4. Timing

| Clock | Rate | Rule |
|---|---|---|
| Physics step | 200 Hz (5 ms) | Same in training and Unreal |
| Brain decision | 50 Hz (every 4 physics steps) | Same in training and Unreal |
| Screen | 60 fps | Smooth blend between the two most recent physics states. Display only, never fed back into physics. |

Physics never runs on Unreal's frame clock. A slow frame means Unreal catches up physics steps; it never stretches them.

**Who runs what in Unreal:** URLab steps MuJoCo (physics). The trained brain is an **ONNX** file run by Unreal's built-in **NNE**,
in the same process, every 4th physics step. No Python at game time. `urlab_bridge` (Python over ZMQ) is kept only as a dev
tool for testing brains in Unreal before they are exported.

## 5. Arena (all inside MuJoCo, drawn by Unreal)

| Item | Size / setting |
|---|---|
| Floor | Flat, high friction (0.9 base) |
| Podium | Rigid box between athletes, 0.2 m deep, top at 0.59 × height (1.00 m for Grandpa, just under the belly) |
| Foot boxes | Marked zones; **stance check**, not walls |
| Spacing | Pelvis-to-pelvis 0.85 m (PRD's shortest; reaching the face needs a lean) |
| Safety mat | Padded boundary ring; touching it with anything but soles = knockdown |
| Out of bounds | Any body part outside the mat area → episode/turn ends, athletes reset in place (no respawn) |

Reset = put both athletes back to their neutral pose at their foot boxes. They're never destroyed and re-created.

## 6. Training Conditions (randomised every episode)

- **Pushes:** random shoves of 150–300 N for 0.1–0.2 s to torso or head, random direction and timing.
- **Floor friction:** 0.6–1.2 (plus a separate random value per foot).
- **Body variation:** ±5 % mass per part, ±10 % motor strength, a little sensor noise, 0–20 ms action delay.
- **Opponent:** none (L0–L2), scripted strikes (L3), self-play (L4–L5).

## 7. Skill Ladder (approved targets)

Training starts in plain MuJoCo (fast, on the GPU). A brain is only brought into Unreal once it passes its level in MuJoCo.

| Level | Skill | Pass / Fail |
|---|---|---|
| **L0** | **Stand** | Upright 30 s, feet planted, under random pushes. **Pass:** ≥ 95 % of 100 episodes with zero non-sole contact. |
| **L1** | **Guard hold** | Stand in the foot box facing the opponent, head in target zone, 30 s. **Pass:** feet never leave the box in ≥ 95 %. |
| **L2** | **Strike** (fixed target) | Open-palm lateral swing hits a fixed head target. **Pass:** palm touches first (not wrist), hand ≥ 8 m/s, striker stays up, ≥ 90 % of attempts, inside the 30 s clock. |
| **L3** | **Absorb** (scripted hits) | Defender takes scripted strikes of rising strength. **Pass:** survives a median strike ≥ 90 %, centre of mass back within 5 cm in ≤ 5 s. |
| **L4** | **Duel** (self-play) | Trained striker vs trained defender. **Pass:** KO rate 10–40 % per strike, striker self-falls ≤ 5 %. |
| **L5** | **Full match** | 3 rounds, turn clock, recovery window, judges. **Pass:** 100 matches end-to-end with no sim blow-ups, Unreal replay matches MuJoCo. |

**One brain, two roles:** a striking/defending flag is one of its inputs. Grandpa's brain is trained from scratch.

**Graduation:** a level is passed only when its pass/fail target above holds **and** mean reward ≥ the level's target across
100 consecutive episodes with zero NaN gradients. Only then is the brain exported to ONNX.
TensorBoard runs with every training run. Runs over 30 min produce an HTML report (top-3 metric charts, change vs. last run,
Toddler / Child / Adult summaries). Unreal Editor stays closed during long training runs.

## 8. Matching Check (the gate before L1+)

Run the **same 5-second recording** (same start pose, same brain decisions) in MuJoCo and in Unreal/URLab:

- **Replay test** (recorded actions played back): every body part within **1 mm** at every physics step.
- **Live test** (brain running in both): pelvis and head within **1 cm** after 5 s, same fall/no-fall outcome.

Fail → stop, find the difference (version, timestep, model file, coordinates), fix, rerun. Nothing advances past L0 until this passes.

## 9. Knockdown (what the referee sees)

Knockdown = any part of an athlete **other than the soles** touching floor, podium or safety mat. MuJoCo's contact list decides it, not Unreal.
The same rule is the "fall" signal in training, so what the brain learns to avoid is exactly what ends a match.
