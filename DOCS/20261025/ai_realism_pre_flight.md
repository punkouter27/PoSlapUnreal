# AI Realism — Top 10 Options & Pre-Flight Configuration Guide

> **Document Version:** `20261025`  
> **Status:** **READY-TO-RUN SETUP (Active Simulation & Training Not Started)**  
> **Engine:** Unreal Engine 5.8 + URLab | **Physics:** MuJoCo 3.10.0 (200 Hz) | **Control:** NNE / PyTorch PPO (50 Hz)

---

## 1. Top 10 Proposed AI Realism Options

| # | Option | Pillar | What It Adds | Config / Target |
|---|---|---|---|---|
| **1** | **Anatomical Anti-Hyperextension & Inertial PD Firmness ($\zeta = 0.8$)** | Body & Physics | Strictly clamps hinge/ball ranges to human ligament limits (`[0°, 140°]` knee/elbow, bounded 3-DoF spine) and derives joint damping $K_v$ from each joint's stand-pose link inertia so limbs neither oscillate nor freeze. | `joint_and_strength_config.json` |
| **2** | **Second-Order Muscle Activation Filter & Reflex Latency** | Senses & Control | Passes neural position targets through a biomechanical rate-limiter ($\dot{\theta}_{\max} \times \text{stamina}$) plus an optional EMA low-pass filter ($\alpha = 0.75$) and `0–20 ms` spinal/cortical delay to eliminate robotic twitching. | `sensory_profile.json` |
| **3** | **Continuous Metabolic Stamina & Effort Fatigue Curve** | Body & Physics | Extends turn-based stamina with continuous intra-round metabolic drain proportional to squared motor torque ($\sum \tau_i^2$) and passive recovery during calm stance, naturally slowing peak joint speed and force under prolonged exertion. | `joint_and_strength_config.json` |
| **4** | **Clean Proprioceptive Foot-Ground & Phase Awareness (+12 floats)** | Senses & Control | Augments the 136-float `BRAIN_IO` contract with clean sole positions in the heading frame, binary ground contact flags, normalized vertical ground reaction loads ($F_z / mg$), and a stride phase clock—with zero scene raycasts. | `sensory_profile.json` |
| **5** | **Heel-to-Toe Alternating Gait & Foot-Clearance Shaping** | Natural Behavior | Rewards cyclic alternating foot swing/stance phases, minimum `6 cm` mid-swing foot clearance, and heel-strike-to-toe-off progression so walking and stepping never degenerate into shuffling. | `training_realism_config.json` |
| **6** | **Anti-Exploit Penalty Suite (No Flailing, Dragging, or Jitter)** | Natural Behavior | Penalizes four classic RL exploits: uncorrelated upper-arm angular velocity (flailing), horizontal foot velocity during ground contact (leg dragging), second-order action jerk $\|a_t - 2a_{t-1} + a_{t-2}\|^2$ (vibration), and non-sole floor contact (instant terminal penalty). | `training_realism_config.json` |
| **7** | **3-Stage Locomotion-to-Combat Curriculum Ladder** | Natural Behavior | Structures motor learning into **Stage 1** (Stand & 150–300 N Shove Recovery), **Stage 2** (Steady 1.0–1.4 m/s Natural Walk), and **Stage 3** (2.5–3.5 m/s Jog, Smooth Yaw Turn, Planted Stop & $\pm 8^\circ$ Slopes) prior to arena combat. | `training_realism_config.json` |
| **8** | **Anthropometric Foot-Arch & Dual-Contact Sole Geometry** | Body & Physics | Splits or shapes sole contact evaluation into heel and forefoot pressure zones with realistic Earth friction (`0.9` baseline, `0.6–1.2` randomized) and automatic center-of-mass over mid-foot verification (`< 4 cm` fore/aft, `< 2 cm` lateral). | `joint_and_strength_config.json` |
| **9** | **Antagonist Co-Contraction Impedance Modulation** | Body & Physics | Allows the policy (or bracing reflex) to modulate joint stiffness $K_p$ between relaxed compliance during locomotion/recovery and high co-contraction stiffness during impact absorption or heavy shoves. | `joint_and_strength_config.json` |
| **10** | **Zero-Overhead Multi-Instance GPU Hygiene (4,096 Worlds)** | Fast Sim & Hygiene | Runs 4,096 side-by-side MuJoCo Warp instances captured in CUDA Graphs at `~53,000 steps/s` with Chaos completely disabled (`NoCollision`), zero raycasts, and zero host-device syncs per physics step. | `training_realism_config.json` |

---

## 2. Saved Configuration Files Inventory

All joint tunings, strength ceilings, proprioceptive profiles, and curriculum configs have been saved to clean JSON configuration files in `training/configs/`:

1. **[joint_and_strength_config.json](../../training/configs/joint_and_strength_config.json)**
   * 30-DoF anatomical joint limits (knees/elbows `[0°, 140°]`, spine twist/flex/side, shoulders, wrists, hips, ankles, neck).
   * Inertial PD firmness formula:
     $$K_p = \frac{\tau_{\max}}{0.35\text{ rad}}, \qquad K_v = \max\left(0.05 K_p,\; 2\zeta\sqrt{K_p I_{\text{stand}}} - 0.5\right) \quad (\zeta = 0.8)$$
   * Earth gravity (`-9.81 m/s²`), surface friction (`0.9` base, `0.6–1.2` randomized), and 17-body anthropometric mass percentages.
   * Per-athlete strength, speed, flexibility, stamina, and continuous metabolic fatigue parameters (`grandpa` and `kim`).
   * Simplified collision primitive specification (capsules, spheres, boxes) and A10 self-overlap rules.

2. **[sensory_profile.json](../../training/configs/sensory_profile.json)**
   * Complete 136-float `BRAIN_IO` observation specification + 12-float locomotion proprioception extension (sole positions, contact states, normalized vertical load, and gait phase clock).
   * Smooth motor control pipeline: asymmetric range mapping, per-joint angular speed caps (`12–25 rad/s × speed × stamina`), `0–20 ms` reflex delay, and optional EMA muscle activation filter.
   * Performance hygiene checklist (Chaos off, zero raycasts, headless GPU training, CUDA graphs).

3. **[training_realism_config.json](../../training/configs/training_realism_config.json)**
   * `200 Hz` physics / `50 Hz` brain decision timing and `4096`-world MuJoCo Warp multi-instance setup.
   * Natural human movement reward weights and anti-cheat/exploit penalty weights.
   * Step-by-step milestone definitions (**Stage 1:** Stand & Shove Recovery, **Stage 2:** Steady Walk, **Stage 3:** Jog, Turn, Stop & Uneven Slopes) plus the L0–L5 arena combat graduation gates.

---

## 3. Pre-Flight Checklist (Verified — Ready to Run)

- [x] **Documentation Reviewed (`/DOCS`):** `BRAIN_IO.md`, `SUMMARY.md`, and `DOCS/20261025/*` verified.
- [x] **Build Date Watermark (`YYMMDD`):** Automatically stamped as `261025` in the bottom-right corner via `PoSlapUnreal.Build.cs` (`POSLAP_BUILD_VERSION`) and `ASlapHUD::BuildUi()`.
- [x] **Unreal Background CPU Throttling Off:** `bThrottleCPUWhenNotForeground=False` active in `Config/DefaultEditorPerProjectUserSettings.ini`.
- [x] **Chaos Physics & Visual Collision Disabled:** Runtime audit in `ASlapGameMode::StartPlay()` and `Scripts/build_scene.py` enforces `NoCollision` and `bSimulatePhysics=false` across all primitives; only simple capsules, spheres, and boxes collide in MuJoCo.
- [x] **Self-Collision & COM Balance Verified:** Parent-child exclusions active; `A10` self-collision and `A11` COM mid-foot checks configured in `training/build_body.py`.
- [x] **No Active Training Started:** All configs are saved and ready for inspection; no simulation or training processes have been launched.
