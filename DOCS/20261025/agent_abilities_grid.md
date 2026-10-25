# Agent Abilities & Physical Behaviors Matrix

> **Document Version:** `20261025`  
> **Engine:** Unreal Engine 5.8 | **Simulator:** MuJoCo 3.10.0 | **Characters:** Grandpa, Kim, 16-Roster Architecture  
> **Target Branch:** `master`

---

## Tier 1 (Quick Look)

* **Physical Skill Matrix:** Tracks seven core physical capabilities acquired through reinforcement learning: Planted Upright Balance, Kinetic Chain Wind-Up, Rapid Terminal Slap Whip, Impact Absorption & Neck Bracing, COM Post-Impact Recovery, World-Space Eye-Contact Gaze, and Post-Slap Watch Poise.
* **Character Archetypes:**
  * **Grandpa (Heavyweight Kinetic):** Exceptional kinetic power (Grade S), high KO generation (Grade S), upright stability (Grade A), moderate recovery agility (Grade B+).
  * **Kim (Athletic Counter-Striker):** Outstanding slap whip speed (Grade S), stance stability (Grade S), agile COM recovery (Grade A+), lighter neck KO resilience (Grade B+).
* **Deterministic Behavior Parity:** Behaviors operate under identical mathematical constraints in standalone Python training and Unreal Engine 5.8 runtime.

---

## Tier 2 (Core Mechanics)

### 1. Agent Abilities & Proficiency Matrix

Proficiency Grades: **S** (Elite / Flawless), **A** (Proficient / Tournament Standard), **B** (Functional / Passable), **C** (Novice / Needs Tuning).

| Biomechanical Ability | Training Curriculum Tier | Grandpa (Slot 1) | Kim (Slot 2) | Future Archetypes (Slots 3–16) Target | Description & Mechanical Function |
|---|---|---|---|---|---|
| **Planted Upright Balance** | L0 (Stand) & L1 (Guard) | **A+** (97% survival) | **S** (96% crouch balance) | Grade A+ (min 95% no-fall) | Maintains feet planted inside $20 \times 25$ cm box under 150–300 N pushes. |
| **Kinetic Chain Wind-Up** | L2 (Strike) & L4 (Duel) | **S** (Deep 34° trunk rotation) | **A** (Compact 25° rotation) | Grade A (min 20° trunk twist) | Sequential energy transfer from hips through belly and chest to striking arm. |
| **Terminal Slap Whip** | L2 (Strike) & L4 (Duel) | **S** (12.8 m/s terminal speed) | **S** (11.6 m/s rapid snap) | Grade A (min 8.0 m/s palm impact) | Late elbow and wrist snap delivering open palm to legal cheek target zone. |
| **Neck Bracing & Impact Absorption** | L3 (Absorb) | **A+** (Resilient, 4% damage/snap) | **B+** (Lighter neck, 5% damage/snap) | Grade A (withstand 15 N·s impulse) | Dynamic stiffening of neck yaw/nod motors upon detecting incoming strike. |
| **COM Balance Recovery** | L3 (Absorb) & L4 (Duel) | **B+** (Recover within 4.2 cm) | **A+** (Recover within 2.8 cm) | Grade A (recover COM &le; 5.0 cm) | Restores center of mass over planted soles within 5.0 s recovery window. |
| **World-Space Eye-Contact Gaze** | Runtime Gaze Servo | **S** (4.8° median error) | **S** (4.5° median error) | Grade S (median error &le; 6.0°) | Defender squares up and holds opponent's eyes until palm impact occurs. |
| **Post-Slap Watch Poise** | Runtime Poise Watch | **A** (4.2° watch, 3% self-down) | **A+** (4.0° watch, 2% self-down) | Grade A (self-down &le; 5.0%) | Striker holds gaze on opponent while neural brain commands upright balance. |

### 2. Behavioral Deep-Dive

* **1. Kinetic Chain Wind-Up vs. Terminal Whip:**
  * **Grandpa:** Employs an expansive kinetic chain. Trunk twists to -34°, followed by full shoulder extension and an arm swing sweeping through a 110° arc. Produces 12.8 m/s velocity with high strike momentum (3.0 kg effective strike mass).
  * **Kim:** Uses a compact, explosive whip. Minimizes torso movement, holds elbow high in guard, and delivers late elbow extension (reaching 140°) and wrist flexion. Striking occurs in 1.40 s compared to Grandpa's 2.20 s.
* **2. Impact Absorption & Neck Damage Dynamics:**
  * Slap impact delivers a 10 ms force pulse (`xfrc_applied = strike_mass * hand_velocity / 0.01s`).
  * Grandpa's toughness ($1.0$) gives stronger initial neck motor torque ($20.0$ Nm), absorbing hits with minimal head snap.
  * Kim's toughness ($0.85$) allows higher initial head snap, reducing neck strength by 5% per m/s head velocity. However, her lower stance allows rapid center-of-mass re-centering.
* **3. World-Space Eye-Contact & Watch Poise:**
  * Operates via `USlapController::ApplyGaze()` and `training/match.py`:
    * Defender's neck yaw and nod goals walk smoothly at $0.08$ rad/step toward the opponent's eye coordinates in world space.
    * At the moment of contact, the gaze servo disengages, allowing the neural brain's neck bracing to absorb the strike.
    * Post-strike, the striker tracks the defender's head at up to $0.25$ rad/step without imposing artificial body joint freezes.

---

## Tier 3 (Setup Guide)

### 1. Activating & Tuning Behavioral Servos

To verify behavioral servo settings in Unreal Engine:

* **Eye Contact Gaze Servo Toggle:**
  * Default state: `bEyeContact = true`.
  * Command-line disable for A/B testing:
    ```powershell
    UnrealEditor.exe PoSlapUnreal.uproject /Game/Levels/Arena -game -SlapEyeContact=0
    ```
* **Post-Touch Watch Poise Verification:**
  * Striker's body must remain fully brain-commanded. Never apply hard joint pose freezes (`melt-to-stand` or `pose-freeze`) to the striker's legs or waist; hard constraints cause 7–9 self-knockdowns per match.
  * Verify post-match hold duration in `USlapController`:
    * `PostMatchHold = 4.0` s (allows spectator viewing of KO collapse or final stance).
    * `TurnHold = 2.0` s (pause between rounds before reset to stand pose).

### 2. Testing Behavioral Abilities via CLI

Execute the following test scripts from `training/`:

* **Test 1: Head-to-Head Exam (100 Matches):**
  ```powershell
  training/.venv/Scripts/python.exe training/match.py exam training/models/brains/grandpa_final_vs_kim.onnx --blue training/models/brains/kim_final_vs_grandpa.onnx --matches 100
  ```
* **Test 2: Strike Speed & Palm Contact Audit:**
  ```powershell
  training/.venv/Scripts/python.exe training/slap_demo.py test --char grandpa
  training/.venv/Scripts/python.exe training/slap_demo.py test --char kim
  ```
* **Test 3: Eye Contact Angle Verification:**
  ```powershell
  training/.venv/Scripts/python.exe training/match.py play training/models/brains/grandpa_final.onnx --slap-eye-contact 1
  ```

---

*Summary: Matrix mapping 7 physical behaviors across Grandpa and Kim, detailing biomechanics, proficiency grades, and setup rules.*
