"""The numbers the three engines share (DOCS/BRAIN_IO.md): one source of truth inside training/.

The same constants are mirrored in C++ (Source/PoSlapUnreal/SlapController.cpp) and in JS (Web/sim.js).
Changing a value here means changing it in both mirrors too, or the parity checks (Blueprint 8) break:
`replay_check.py` compares MuJoCo against URLab bit for bit, `match.py compare` compares the Python and
the Unreal match engines event by event, and `node Web/check.mjs` runs the browser engine.

    constant      value        C++ mirror (Source/PoSlapUnreal)          JS mirror (Web/sim.js)
    OBS           136          SlapController.cpp: NumObs = 148          sim.js: export const OBS = 136
                              (136 base + 12 extended proprioception)
    OBS_EXT       148          SlapController.cpp: NumObs = 148          sim.js: (the browser engine has no
                                                                              extended layout: it reads OBS)
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

import os

OBS = 136                       # DOCS/BRAIN_IO.md (108 before memory and fatigue, 130 before the own palm;
                                # older brains read their first n)
OBS_EXT = 148                   # + 12 locomotion proprioception floats (foot pos, contacts, load, gait clock)
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

# The mirrors, as (file, needle) so a checker can prove the three engines still agree.
MIRRORS = {
    "OBS": ("Source/PoSlapUnreal/SlapController.cpp", "NumObs = 148", "Web/sim.js", "export const OBS = 136"),
    "LAGS": ("Source/PoSlapUnreal/SlapController.cpp", "Lags[3] = { 5, 10, 15 }", "Web/sim.js", "const LAGS = [5, 10, 15]"),
    "REACT": ("Source/PoSlapUnreal/SlapController.cpp", "React = 6", "Web/sim.js", "const REACT = 6"),
    "HILL_K": ("Source/PoSlapUnreal/SlapController.cpp", "HillK = 0.25", "Web/sim.js", "const HILL_K = 0.25"),
    "MIN_STAMINA": ("Source/PoSlapUnreal/SlapController.h", "MinStamina = 0.7", "Web/sim.js", "const MIN_STAMINA = 0.7"),
    "MIN_NECK": ("Source/PoSlapUnreal/SlapController.h", "MinNeck = 0.5", "Web/sim.js", "const MIN_NECK = 0.5"),
    "DECIM": ("Source/PoSlapUnreal/SlapController.cpp", "ControlEvery = 4", "Web/sim.js", "REACT"),
}
