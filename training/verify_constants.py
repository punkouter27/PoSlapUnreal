"""The three engines must agree on the numbers they share (DOCS/BRAIN_IO.md).

    .venv/Scripts/python.exe verify_constants.py

training/constants.py is the single source of truth INSIDE training/. The C++ (Source/PoSlapUnreal) and the
JS (Web/sim.js) are mirrors of it. This checks that the mirrors still carry the same VALUE, so a number
changed in one engine only is caught here instead of in a silent parity break.

The expectation is the python constant itself, never a literal written here: the check fails when the three
engines disagree, not when a number happens to be different from a number typed into this file.
"""
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
    ("OBS", "Source/PoSlapUnreal/SlapController.cpp", "NumObs", constants.OBS_EXT,
     "Web/sim.js", "OBS", None, constants.OBS),
    ("REACT", "Source/PoSlapUnreal/SlapController.cpp", "React", constants.REACT,
     "Web/sim.js", "REACT", None, constants.REACT),
    ("HILL_K", "Source/PoSlapUnreal/SlapController.cpp", "HillK", constants.HILL_K,
     "Web/sim.js", "HILL_K", None, constants.HILL_K),
    ("MIN_STAMINA", "Source/PoSlapUnreal/SlapController.h", "MinStamina", constants.MIN_STAMINA,
     "Web/sim.js", "MIN_STAMINA", None, constants.MIN_STAMINA),
    ("MIN_NECK", "Source/PoSlapUnreal/SlapController.h", "MinNeck", constants.MIN_NECK,
     "Web/sim.js", "MIN_NECK", None, constants.MIN_NECK),
    ("DECIM", "Source/PoSlapUnreal/SlapController.cpp", "ControlEvery", constants.DECIM,
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
    """The base layout (136) and the extended one (148) must be the same in both mirrors."""
    print("\n=== The observation layout ===")
    cpp = read("Source/PoSlapUnreal/SlapController.cpp")
    js = read("Web/sim.js")
    c_n = value_of(cpp, "NumObs")
    j_n = value_of(js, "OBS")
    if c_n is None:
        fail("SlapController.cpp does not declare NumObs")
    if j_n is None:
        fail("Web/sim.js does not declare OBS")
    if c_n is not None and j_n is not None:
        # the C++ runs the extended layout (base + locomotion proprioception), the browser the base one
        if c_n != constants.OBS_EXT:
            fail(f"SlapController.cpp NumObs is {c_n}, constants.OBS_EXT is {constants.OBS_EXT}")
        if j_n != constants.OBS:
            fail(f"Web/sim.js OBS is {j_n}, constants.OBS is {constants.OBS}")
        if constants.OBS_EXT < constants.OBS:
            fail(f"OBS_EXT ({constants.OBS_EXT}) is smaller than OBS ({constants.OBS})")
        print(f"  [PASS] C++ NumObs {c_n:g} (= base {constants.OBS} + {constants.OBS_EXT - constants.OBS} extended), "
              f"browser OBS {j_n:g}")


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
    cpp = read("Source/PoSlapUnreal/SlapController.cpp")
    if value_of(cpp, "ControlEvery") != constants.DECIM:
        fail(f"SlapController.cpp does not use ControlEvery = {constants.DECIM}")
    print(f"  [PASS] the model steps at {hz:.0f} Hz, the brain every {constants.DECIM} steps "
          f"({constants.CONTROL_HZ} Hz)")


def main():
    print("=== PoSlap constant parity (the three engines) ===")
    check_mirrors()
    check_observation_sizes()
    check_rates()
    if FAILURES:
        print(f"\n{FAILURES} CHECK(S) FAILED - the engines do not agree.")
        return 1
    print("\nALL THREE ENGINES AGREE ON THE SHARED NUMBERS.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
