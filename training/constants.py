"""The numbers the three engines share (DOCS/BRAIN_IO.md): one source of truth inside training/.

The same constants are mirrored in C++ (Source/PoSlapUnreal/SlapController.cpp) and in JS (Web/sim.js).
Changing a value here means changing it in both mirrors too, or the parity checks (Blueprint 8) break:
`replay_check.py` compares MuJoCo against URLab bit for bit, `match.py compare` compares the Python and
the Unreal match engines event by event, and `node Web/check.mjs` runs the browser engine.

    constant      value        C++ mirror (Source/PoSlapUnreal)          JS mirror (Web/sim.js)
    OBS           140          SlapControllerInternal.h: NumObs = 140    sim.js: export const OBS = 140
                              (interface 4: 136 + heel/toe contact per foot; older brains read their first n)
    LPF_HZ        8.0          SlapControllerInternal.h: LpfHz = 8.0     sim.js: const LPF_HZ = 8.0
                              (the action low-pass, interface 4 only: width >= LPF_FROM_OBS = 140)
    LAGS          (5, 10, 15)  SlapController.cpp: Lags[3]                sim.js: const LAGS = [5, 10, 15]
    REACT         6            SlapController.cpp: React = 6             sim.js: const REACT = 6
    HILL_K        0.25         SlapController.cpp: HillK = 0.25          sim.js: const HILL_K = 0.25
    MIN_STAMINA   0.7          SlapController.h: MinStamina               sim.js: const MIN_STAMINA = 0.7
    MIN_NECK      0.5          SlapController.h: MinNeck                  sim.js: const MIN_NECK = 0.5
    DECIM         4            SlapController.cpp: ControlEvery = 4      sim.js: (every 4th physics step)
    PHYSICS_HZ    200          the model's <option timestep="0.005">      sim.js: the WASM model's timestep
    CONTROL_HZ    50           the brain runs every 4th physics step     sim.js: the same
    MUSCLE_TC     0.035        build_body.MUSCLE_TC (the model carries it: every engine reads the model's
                              <position timeconst>), so it is the same number in all three by construction

Nothing here may change value without the two mirrors changing with it (see the table above).
"""
from __future__ import annotations

import math
import os

OBS = 140                       # DOCS/BRAIN_IO.md, interface 4 (108 before memory and fatigue, 130 before the own
                                # palm, 136 before the foot contacts; older brains read their first n)
OBS_V3 = 136                    # interface 3: every shipped brain (2026-11-04)
FOOT = slice(136, 140)          # interface 4: heel_l, toe_l, heel_r, toe_r (a sole box touching a world geom;
                                # heel = contact behind the box centre along its long axis, toe = at or ahead of it)
INTERFACE = 4                   # the interface new checkpoints are tagged with (DOCS/20261104/policy_change_log.md)
# (OBS_EXT = 148, an unused 12-float locomotion extension at 136-147, was retired 2026-11-04: no brain ever read it)


def interface_of(n_in):
    """A brain's interface from its input width: 108 -> 1, 130 -> 2, 136 -> 3, 140 -> 4."""
    return 4 if n_in >= 140 else 3 if n_in >= 136 else 2 if n_in >= 130 else 1


# the action low-pass (interface 4 only, item 43): a first-order filter at LPF_HZ on the joint targets, computed at
# the 50 Hz decision: target = goal + LPF_ALPHA (mapped - goal), then the per-joint rate limit. One state (the goal),
# identical in envs/*, brain_cpu.Athlete.decide, Web/sim.js decide and Unreal's Decide / walk-in.
LPF_HZ = 8.0
LPF_FROM_OBS = 140              # brains this wide (interface >= 4) were trained with it
LPF_ALPHA = 1.0 - math.exp(-2.0 * math.pi * LPF_HZ / 50.0)   # 0.634069 at 50 Hz


def lpf_on(n_in):
    return n_in >= LPF_FROM_OBS
LAGS = (5, 10, 15)              # memory: inputs 102-107 as they were 0.1, 0.2, 0.3 s ago (a wind-up, a feint)
REACT = 6                       # the defender sees the opponent's hand 6 decisions (0.12 s) late: human reaction
HILL_K = 0.25                   # Hill's force-velocity curvature (a / F0, human muscle ~0.25): see brain_cpu.hill
MIN_STAMINA, MIN_NECK = 0.7, 0.5   # fatigue floors (the card sets how fast an athlete gets there)
DECIM = 4                       # physics steps per brain step (200 Hz / 50 Hz)
PHYSICS_HZ, CONTROL_HZ = 200, 50
MUSCLE_TC = 0.035               # s: muscle activation time constant (build_body.MUSCLE_TC; the model carries it)

# ---------------------------------------------------------------- the eye-contact servo
# The defender squares up and holds the striker's eyes through the whole turn (user, 2026-10-24).  The neck's
# yaw motor is walked to a gaze bias (a smooth PD, never a teleport); the brain owns every other joint and
# regains the neck the instant the slap lands (he braces for it).
# One source of truth: match.py (the match engine), envs/arena.py (the L4 training env) and the C++/JS mirrors
# (Unreal's ApplyGaze, Web/sim.js gazeApply) all use these numbers and the same maths.
#
# NB (2026-10-25 QA): an imposed post-slap POSE hold was tried and rejected - freezing any joints after the
# touch fought the brain's own balance and tripled striker self-knockdowns (7-9/3 matches vs a 1.3 baseline).
# The striker's "stay balanced" duty is served by leaving the brain in charge of the body: gaze only.
GAZE_GAIN = 1.0                 # the bias OWNS the neck goal pre-touch (the mandate is stronger than the brain's habit)
GAZE_MAX_STEP = 0.08            # rad per decision the bias may walk: the deliberate head turn of a man squaring up
WATCH_MAX_STEP = 0.25           # rad per decision while PLANTED: re-acquiring a snapping head inside a short window
GAZE_ON = os.environ.get("SLAP_EYE_CONTACT", "1") != "0"   # SLAP_EYE_CONTACT=0 disables (debug / A-B checks)

# ---------------------------------------------------------------- the feint read (match events)
# The rule Unreal's HUD classifies a swing with (SlapHUD.cpp, F10): the striker's palm peaked above FEINT_MIN_PEAK
# and, FEINT_AFTER_S after the peak and without a touch, is below FEINT_PULLBACK x the peak. A swing that comes
# back up to FEINT_REARM x the peak FEINT_REARM_S after it re-arms the read (a second swing).
FEINT_MIN_PEAK = 2.5            # m/s
FEINT_AFTER_S = 0.12            # s after the peak
FEINT_PULLBACK = 0.55           # share of the peak
FEINT_REARM, FEINT_REARM_S = 0.95, 0.2

# ---------------------------------------------------------------- physics versions (build_body.py PHYSICS)
# 1: the shipped models (training/models/), the physics every brain in models/brains trained on and every engine
#    plays. 2: the realism batch (training/models/v2/, DOCS/20261103/training_realism.md) for the retrain; its
#    fingerprints carry "physics": 2. SLAP_PHYSICS picks the version the envs, match.py and train.py load
#    (read at call time, so a CLI flag that sets it before the env is built is enough).


def physics():
    return int(os.environ.get("SLAP_PHYSICS", "1"))


def models_dir(version=None):
    """The models of a physics version: training/models (1) or training/models/v<n>. Brains stay in models/brains."""
    from pathlib import Path
    v = physics() if version is None else int(version)
    base = Path(__file__).resolve().parent / "models"
    return base if v == 1 else base / f"v{v}"

# The mirrors, as (file, needle) so a checker can prove the three engines still agree.
MIRRORS = {
    "OBS": ("Source/PoSlapUnreal/SlapControllerInternal.h", "NumObs = 140", "Web/sim.js", "export const OBS = 140"),
    "LPF_HZ": ("Source/PoSlapUnreal/SlapControllerInternal.h", "LpfHz = 8.0", "Web/sim.js", "const LPF_HZ = 8.0"),
    "LPF_FROM_OBS": ("Source/PoSlapUnreal/SlapControllerInternal.h", "LpfFromObs = 140", "Web/sim.js", "const LPF_FROM_OBS = 140"),
    "LAGS": ("Source/PoSlapUnreal/SlapController.cpp", "Lags[3] = { 5, 10, 15 }", "Web/sim.js", "const LAGS = [5, 10, 15]"),
    "REACT": ("Source/PoSlapUnreal/SlapController.cpp", "React = 6", "Web/sim.js", "const REACT = 6"),
    "HILL_K": ("Source/PoSlapUnreal/SlapController.cpp", "HillK = 0.25", "Web/sim.js", "const HILL_K = 0.25"),
    "MIN_STAMINA": ("Source/PoSlapUnreal/SlapController.h", "MinStamina = 0.7", "Web/sim.js", "const MIN_STAMINA = 0.7"),
    "MIN_NECK": ("Source/PoSlapUnreal/SlapController.h", "MinNeck = 0.5", "Web/sim.js", "const MIN_NECK = 0.5"),
    "DECIM": ("Source/PoSlapUnreal/SlapController.cpp", "ControlEvery = 4", "Web/sim.js", "REACT"),
}
