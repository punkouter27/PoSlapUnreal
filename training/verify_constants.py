"""The three engines must agree on the numbers they share (DOCS/BRAIN_IO.md).

    .venv/Scripts/python.exe verify_constants.py

training/constants.py is the single source of truth INSIDE training/. The C++ (Source/PoSlapUnreal) and the
JS (Web/sim.js) are mirrors of it. This checks that the mirrors still carry the same VALUE, so a number
changed in one engine only is caught here instead of in a silent parity break.

The expectation is the python constant itself, never a literal written here: the check fails when the three
engines disagree, not when a number happens to be different from a number typed into this file.
"""
import math
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import constants  # noqa: E402

FAILURES = 0


def fail(msg):
    global FAILURES
    print(f"  [FAIL] {msg}")
    FAILURES += 1


def read(rel):
    path = ROOT / rel
    if not path.exists():
        fail(f"{rel} is missing")
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")


def value_of(text, ident):
    """The value an identifier is given, however the line is written.

    'const MIN_STAMINA = 0.7, MIN_NECK = 0.5' -> 0.5 for MIN_NECK.
    """
    m = re.search(re.escape(ident) + r"\s*=\s*([0-9]+(?:\.[0-9]+)?)", text)
    return float(m.group(1)) if m else None


# name, C++ file, C++ identifier, the python constant the C++ mirror must equal,
#         JS file, JS identifier (or None), the literal the JS mirror must contain (or None),
#         the python constant the JS mirror must equal
MIRROR_CHECKS = [
    ("OBS", "Source/PoSlapUnreal/SlapControllerInternal.h", "NumObs", constants.OBS,
     "Web/sim.js", "OBS", None, constants.OBS),
    ("LPF_HZ", "Source/PoSlapUnreal/SlapControllerInternal.h", "LpfHz", constants.LPF_HZ,
     "Web/sim.js", "LPF_HZ", None, constants.LPF_HZ),
    ("LPF_FROM_OBS", "Source/PoSlapUnreal/SlapControllerInternal.h", "LpfFromObs", constants.LPF_FROM_OBS,
     "Web/sim.js", "LPF_FROM_OBS", None, constants.LPF_FROM_OBS),
    ("REACT", "Source/PoSlapUnreal/SlapControllerInternal.h", "React", constants.REACT,
     "Web/sim.js", "REACT", None, constants.REACT),
    ("HILL_K", "Source/PoSlapUnreal/SlapControllerInternal.h", "HillK", constants.HILL_K,
     "Web/sim.js", "HILL_K", None, constants.HILL_K),
    ("MIN_STAMINA", "Source/PoSlapUnreal/SlapController.h", "MinStamina", constants.MIN_STAMINA,
     "Web/sim.js", "MIN_STAMINA", None, constants.MIN_STAMINA),
    ("MIN_NECK", "Source/PoSlapUnreal/SlapController.h", "MinNeck", constants.MIN_NECK,
     "Web/sim.js", "MIN_NECK", None, constants.MIN_NECK),
    ("DECIM", "Source/PoSlapUnreal/SlapControllerInternal.h", "ControlEvery", constants.DECIM,
     "Web/sim.js", None, "% 4", constants.DECIM),
]


def check_mirrors():
    print("=== The mirrors still carry the same value ===")
    for (name, cpp_rel, cpp_id, cpp_want, js_rel, js_id, js_lit, js_want) in MIRROR_CHECKS:
        cpp, js = read(cpp_rel), read(js_rel)

        got = value_of(cpp, cpp_id)
        if got is None:
            fail(f"{name}: {cpp_rel} does not declare '{cpp_id}'")
        elif got != float(cpp_want):
            fail(f"{name}: {cpp_rel} says {got}, training says {cpp_want}")
        else:
            print(f"  [PASS] {name}: {cpp_rel} '{cpp_id}' = {got:g}")

        if js_id:
            got = value_of(js, js_id)
            if got is None:
                fail(f"{name}: {js_rel} does not declare '{js_id}'")
            elif got != float(js_want):
                fail(f"{name}: {js_rel} says {got}, training says {js_want}")
            else:
                print(f"  [PASS] {name}: {js_rel} '{js_id}' = {got:g}")
        else:
            if js_lit not in js:
                fail(f"{name}: {js_rel} does not contain '{js_lit}'")
            else:
                print(f"  [PASS] {name}: {js_rel} contains '{js_lit}'")


def check_observation_sizes():
    """Interface 4 (DOCS/BRAIN_IO.md): the same width in both mirrors, the foot contacts at the end (136-139), and each
    engine feeding a brain only its own width (a shipped 136 brain keeps working)."""
    print("\n=== The observation layout ===")
    cpp = read("Source/PoSlapUnreal/SlapControllerInternal.h")
    js = read("Web/sim.js")
    io = read("Source/PoSlapUnreal/SlapBrainIO.cpp")
    c_n, j_n = value_of(cpp, "NumObs"), value_of(js, "OBS")
    if c_n != constants.OBS or j_n != constants.OBS:
        fail(f"NumObs {c_n} / sim.js OBS {j_n}, constants.OBS is {constants.OBS}")
    if (constants.FOOT.start, constants.FOOT.stop) != (constants.OBS_V3, constants.OBS):
        fail(f"the foot contacts {constants.FOOT} are not appended at the end of the 136 inputs")
    if "FootContacts" not in io:
        fail("SlapBrainIO.cpp has no FootContacts (BRAIN_IO 136-139)")
    if "footContacts" not in js:
        fail("Web/sim.js has no footContacts (BRAIN_IO 136-139)")
    if abs(constants.LPF_ALPHA - (1.0 - math.exp(-2.0 * math.pi * constants.LPF_HZ / constants.CONTROL_HZ))) > 1e-12:
        fail(f"LPF_ALPHA {constants.LPF_ALPHA} is not 1 - exp(-2 pi LPF_HZ / CONTROL_HZ)")
    if not FAILURES:
        print(f"  [PASS] C++ NumObs {c_n:g}, browser OBS {j_n:g} (interface {constants.interface_of(constants.OBS)}: "
              f"foot contacts {constants.FOOT.start}-{constants.FOOT.stop - 1}); low-pass {constants.LPF_HZ:g} Hz "
              f"(alpha {constants.LPF_ALPHA:.6f}) from width {constants.LPF_FROM_OBS}")


def check_rates():
    """The physics rate comes from the model, the control rate from the step ratio."""
    print("\n=== The rates ===")
    solo = HERE / "models" / "grandpa_solo.xml"
    if not solo.exists():
        fail("models/grandpa_solo.xml is missing")
        return
    m = re.search(r'timestep="([0-9.eE+-]+)"', solo.read_text())
    if not m:
        fail("the model has no timestep")
        return
    hz = 1.0 / float(m.group(1))
    if round(hz) != constants.PHYSICS_HZ:
        fail(f"the model steps at {hz:.0f} Hz, constants.PHYSICS_HZ is {constants.PHYSICS_HZ}")
    if constants.PHYSICS_HZ // constants.DECIM != constants.CONTROL_HZ:
        fail(f"{constants.PHYSICS_HZ} Hz / {constants.DECIM} is not {constants.CONTROL_HZ} Hz")
    cpp = read("Source/PoSlapUnreal/SlapControllerInternal.h")
    if value_of(cpp, "ControlEvery") != constants.DECIM:
        fail(f"SlapControllerInternal.h does not use ControlEvery = {constants.DECIM}")
    print(f"  [PASS] the model steps at {hz:.0f} Hz, the brain every {constants.DECIM} steps "
          f"({constants.CONTROL_HZ} Hz)")


def check_feint():
    """The feint read: match.py's thresholds (constants.FEINT_*) are the ones Unreal's HUD (SlapSwingRead.h, since the
    2026-11-03 HUD split) and the browser (Web/sim.js) classify a swing with."""
    print("\n=== The feint read (match.py vs SlapSwingRead.h vs sim.js) ===")
    cpp, js = read("Source/PoSlapUnreal/SlapSwingRead.h"), read("Web/sim.js")
    for name, cpp_id, want in (("FEINT_MIN_PEAK", "FeintMinPeak", constants.FEINT_MIN_PEAK),
                               ("FEINT_PULLBACK", "FeintPullBack", constants.FEINT_PULLBACK),
                               ("FEINT_AFTER_S", "FeintAfter", constants.FEINT_AFTER_S),
                               ("FEINT_REARM", "RearmShare", constants.FEINT_REARM),
                               ("FEINT_REARM_S", "RearmAfter", constants.FEINT_REARM_S)):
        for rel, text, ident in (("SlapSwingRead.h", cpp, cpp_id), ("Web/sim.js", js, name)):
            got = value_of(text, ident)
            if got is None or got != float(want):
                fail(f"{name}: {rel} '{ident}' is {got}, training says {want}")
            else:
                print(f"  [PASS] {name}: {rel} '{ident}' = {got:g}")


def main():
    print("=== PoSlap constant parity (the three engines) ===")
    check_mirrors()
    check_observation_sizes()
    check_rates()
    check_feint()
    if FAILURES:
        print(f"\n{FAILURES} CHECK(S) FAILED - the engines do not agree.")
        return 1
    print("\nALL THREE ENGINES AGREE ON THE SHARED NUMBERS.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
