"""The numbers the three engines share: the one source of truth.

Unreal and the browser do not carry their own copies any more: `python gen_constants.py` writes
Source/PoSlapUnreal/SlapShared.gen.h (namespace SlapGen) and Web/shared.gen.js from this module and from
rules.json, and the C++ and JS read those. Change a value here (or in rules.json), run gen_constants.py, commit
the two generated files with it; `verify_constants.py` fails when they are stale.

MUSCLE_TC is carried by the model (<position timeconst>), so it is the same in all three by construction.
The parity checks still decide: `replay_check.py` (MuJoCo vs URLab), `match.py compare` (Python vs Unreal match
events), `node Web/check.mjs` (the browser engine).
"""
from __future__ import annotations

import math
import os

OBS = 142                       # DOCS/BRAIN_IO.md, interface 5 (108 before memory and fatigue, 130 before the own
                                # palm, 136 before foot contacts, 140 before player command vector; older brains read first n)
OBS_V3 = 136                    # interface 3: every shipped brain (2026-11-04)
OBS_V4 = 140                    # interface 4: foot contacts added (heel_l, toe_l, heel_r, toe_r)
FOOT = slice(136, 140)          # interface 4: heel_l, toe_l, heel_r, toe_r (a sole box touching a world geom;
                                # heel = contact behind the box centre along its long axis, toe = at or ahead of it)
CMD = slice(140, 142)           # interface 5: strike power/accuracy [-1, +1] (140), defence brace timing [0, +1] (141)
INTERFACE = 5                   # the interface new checkpoints are tagged with (DOCS/BRAIN_IO.md)
# (OBS_EXT = 148, an unused 12-float locomotion extension at 136-147, was retired 2026-11-04: no brain ever read it)


# The observation layout as data (DOCS/BRAIN_IO.md is the prose): (first index, one past the last, name). Appended to,
# never reordered; verify_constants.py checks that it tiles 0..OBS and that the document lists every range.
LAYOUT = (
    (0, 30, "joint angle - stand pose"), (30, 60, "joint velocity x 0.1"), (60, 61, "pelvis height"),
    (61, 64, "gravity in the pelvis frame"), (64, 67, "pelvis linear velocity, heading frame"),
    (67, 70, "pelvis angular velocity x 0.25"), (70, 100, "previous action"), (100, 101, "role"), (101, 102, "turn clock"),
    (102, 105, "target - pelvis (role 0: own foot box)"), (105, 108, "opponent hand, REACT (+ daze) decisions late"),
    (108, 126, "memory: 102-107 at 5, 10, 15 decisions ago"), (126, 128, "the opponent's last strike: time, speed"),
    (128, 129, "stamina"), (129, 130, "neck strength"), (130, 133, "own palm - pelvis"), (133, 136, "own palm facing"),
    (136, 140, "foot contacts: heel_l, toe_l, heel_r, toe_r"),
    (140, 141, "strike command: power (+1) vs accuracy (-1), 0 neutral"),
    (141, 142, "defence command: brace timing (+1 brace, 0 neutral)"),
)


def interface_of(n_in):
    """A brain's interface from its input width: 108 -> 1, 130 -> 2, 136 -> 3, 140 -> 4, 142 -> 5."""
    return 5 if n_in >= 142 else 4 if n_in >= 140 else 3 if n_in >= 136 else 2 if n_in >= 130 else 1


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

# ---------------------------------------------------------------- the match rules (training/rules.json)
# One file for the three engines: match.py and the envs read it here; Unreal and the browser read the copies
# gen_constants.py writes (SlapShared.gen.h, Web/shared.gen.js) together with the constants above.
import json as _json
from pathlib import Path as _Path

RULES = {k: v for k, v in _json.loads((_Path(__file__).resolve().parent / "rules.json").read_text()).items() if not k.startswith("_")}


def rules2(physics_version):
    """Rules 2 (no scripted kick, the stun, the count, flinch and clubbing fouls, judge noise, the daze) from physics 4."""
    # SLAP_RULES=2 forces them on any physics (python only, an A/B switch: Unreal and the browser go by the fingerprint)
    return os.environ.get("SLAP_RULES") == "2" or int(physics_version) >= RULES["rules2_from_physics"]


def daze_lag(neck_strength):
    """Rules 2: the extra decisions a damaged neck adds to the defender's reaction (0 at full strength)."""
    return int(RULES["daze_react"] * (1.0 - neck_strength) / (1.0 - MIN_NECK) + 0.5)
