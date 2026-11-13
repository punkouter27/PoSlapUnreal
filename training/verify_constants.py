"""The three engines must agree on the numbers they share.

    .venv/Scripts/python.exe verify_constants.py        (standard library only: CI runs it without the venv)

training/constants.py and training/rules.json are the one source of truth. Unreal and the browser do not type the
numbers: gen_constants.py writes Source/PoSlapUnreal/SlapShared.gen.h and Web/shared.gen.js, and the C++ and JS read
those. So the check is:
  1. the two generated files are what the generator would write today (nobody edited them, nobody forgot to run it);
  2. the engines really read them (the names are used, and no shared constant is typed as a literal beside them);
  3. the observation layout and the rates still hold.
No expected number is written in this file: every expectation is the python constant itself.
"""
import math
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

import constants  # noqa: E402
import gen_constants  # noqa: E402

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


def check_generated():
    print("=== The generated copies are current ===")
    for path, text in ((gen_constants.CPP, gen_constants.cpp_text()), (gen_constants.JS, gen_constants.js_text())):
        rel = path.relative_to(ROOT).as_posix()
        if not path.exists():
            fail(f"{rel} is missing: run training/gen_constants.py")
        elif path.read_text(encoding="utf-8").replace("\r\n", "\n") != text:
            fail(f"{rel} is stale or hand-edited: run training/gen_constants.py")
        else:
            print(f"  [PASS] {rel}")


# each engine file and the generated names it must read (a use, not a retyped number)
USES = {
    "Source/PoSlapUnreal/SlapControllerInternal.h": ["SlapGen::Decim", "SlapGen::Obs", "SlapGen::LpfHz", "SlapGen::LpfFromObs",
                                                     "SlapGen::Lags", "SlapGen::HillK", "SlapGen::React", "SlapGen::BoxX",
                                                     "SlapGen::KickSteps", "SlapGen::ControlHz"],
    "Source/PoSlapUnreal/SlapController.h": ["SlapGen::Rounds", "SlapGen::MinStamina", "SlapGen::MinNeck", "SlapGen::StrikeClock",
                                             "SlapGen::Recovery", "SlapGen::GazeGain", "SlapGen::GazeMaxStep", "SlapGen::WatchMaxStep"],
    "Source/PoSlapUnreal/SlapSwingRead.h": ["SlapGen::FeintMinPeak", "SlapGen::FeintPullback", "SlapGen::FeintAfterS",
                                            "SlapGen::FeintRearm", "SlapGen::FeintRearmS"],
    "Source/PoSlapUnreal/SlapReferee.cpp": ["SlapGen::JudgeWeights"],
    "Web/sim.js": ['from "./shared.gen.js"', "RULES.rounds", "RULES.strike_clock", "RULES.recovery", "RULES.box",
                   "RULES.kick_steps", "RULES.judge_weights"],
    "Web/broadcast.js": ['from "./shared.gen.js"'],
    "Web/app.js": ['from "./shared.gen.js"'],
    "Web/build.py": ["shared.gen.js"],
}
# a shared constant typed again as a literal: <name> = <number> in an engine file (the generated files are exempt)
RETYPED = {
    "Source/PoSlapUnreal": r"\b(NumObs|LpfHz|LpfFromObs|HillK|React|ControlEvery|MinStamina|MinNeck|StrikeClock|Recovery|Rounds|"
                           r"BoxX|BoxY|KickSteps|GazeGain|GazeMaxStep|WatchMaxStep|FeintMinPeak|FeintPullBack|FeintAfter|"
                           r"RearmShare|RearmAfter)\s*=\s*[0-9]",
    "Web": r"\b(OBS|LPF_HZ|LPF_FROM_OBS|REACT|HILL_K|MIN_STAMINA|MIN_NECK|ROUNDS|STRIKE_CLOCK|RECOVERY|KICK_STEPS|GAZE_GAIN|"
           r"GAZE_MAX_STEP|WATCH_MAX_STEP|FEINT_MIN_PEAK|FEINT_AFTER_S|FEINT_PULLBACK|FEINT_REARM|FEINT_REARM_S)\s*=\s*[0-9]",
}


def check_used():
    print("\n=== The engines read the generated copies ===")
    for rel, needles in USES.items():
        text = read(rel)
        missing = [n for n in needles if n not in text]
        if missing:
            fail(f"{rel} does not use {', '.join(missing)}")
        else:
            print(f"  [PASS] {rel}: {len(needles)} generated names in use")
    for folder, pattern in RETYPED.items():
        hits = []
        for path in sorted((ROOT / folder).glob("*")):
            if path.suffix not in (".h", ".cpp", ".js", ".mjs") or ".gen." in path.name:
                continue
            for n, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                if re.search(pattern, line.split("//")[0]):
                    hits.append(f"{path.relative_to(ROOT).as_posix()}:{n}: {line.strip()[:90]}")
        if hits:
            for h in hits:
                fail(f"a shared constant is typed as a literal: {h}")
        else:
            print(f"  [PASS] {folder}: no shared constant retyped as a literal")


def check_observation_sizes():
    """Interface 5: foot contacts (136-139) and command vector (140-141), each engine feeding a brain only its own width."""
    print("\n=== The observation layout ===")
    before = FAILURES
    io, js = read("Source/PoSlapUnreal/SlapBrainIO.cpp"), read("Web/sim.js")
    if (constants.FOOT.start, constants.FOOT.stop) != (constants.OBS_V3, constants.OBS_V4):
        fail(f"the foot contacts {constants.FOOT} are not at {constants.OBS_V3}..{constants.OBS_V4}")
    if (constants.CMD.start, constants.CMD.stop) != (constants.OBS_V4, constants.OBS):
        fail(f"the command vector {constants.CMD} is not appended at the end of the {constants.OBS_V4} inputs")
    if constants.interface_of(constants.OBS) != constants.INTERFACE:
        fail(f"interface_of({constants.OBS}) is {constants.interface_of(constants.OBS)}, INTERFACE is {constants.INTERFACE}")
    edges = [a for a, _, _ in constants.LAYOUT] + [constants.LAYOUT[-1][1]]
    if edges[0] != 0 or edges[-1] != constants.OBS or any(b != c for (_, b, _), (c, _, _) in zip(constants.LAYOUT, constants.LAYOUT[1:])):
        fail(f"constants.LAYOUT does not tile 0..{constants.OBS} without a gap or an overlap")
    doc = read("DOCS/BRAIN_IO.md")
    for a, b, name in constants.LAYOUT:
        row = f"| {a} |" if b == a + 1 else f"| {a}–{b - 1} |"
        if row not in doc:
            fail(f"DOCS/BRAIN_IO.md has no row '{row}' ({name})")
    if "FootContacts" not in io:
        fail("SlapBrainIO.cpp has no FootContacts (inputs 136-139)")
    if "footContacts" not in js:
        fail("Web/sim.js has no footContacts (inputs 136-139)")
    if "CmdPower" not in io or "CmdBrace" not in io:
        fail("SlapBrainIO.cpp has no CmdPower / CmdBrace (inputs 140-141)")
    if "cmdPower" not in js or "cmdBrace" not in js:
        fail("Web/sim.js has no cmdPower / cmdBrace (inputs 140-141)")
    if abs(constants.LPF_ALPHA - (1.0 - math.exp(-2.0 * math.pi * constants.LPF_HZ / constants.CONTROL_HZ))) > 1e-12:
        fail(f"LPF_ALPHA {constants.LPF_ALPHA} is not 1 - exp(-2 pi LPF_HZ / CONTROL_HZ)")
    if FAILURES == before:
        print(f"  [PASS] OBS {constants.OBS} (interface {constants.INTERFACE}: foot contacts {constants.FOOT.start}-"
              f"{constants.FOOT.stop - 1}, cmd {constants.CMD.start}-{constants.CMD.stop - 1}); "
              f"low-pass {constants.LPF_HZ:g} Hz (alpha {constants.LPF_ALPHA:.6f}) from width {constants.LPF_FROM_OBS}")


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
    elif constants.PHYSICS_HZ // constants.DECIM != constants.CONTROL_HZ:
        fail(f"{constants.PHYSICS_HZ} Hz / {constants.DECIM} is not {constants.CONTROL_HZ} Hz")
    else:
        print(f"  [PASS] the model steps at {hz:.0f} Hz, the brain every {constants.DECIM} steps ({constants.CONTROL_HZ} Hz)")


def check_rules():
    """rules.json is complete and sane (every engine reads the same keys from it)."""
    print("\n=== The match rules (rules.json) ===")
    before = FAILURES
    r = constants.RULES
    need = ("rounds", "strike_clock", "recovery", "box", "kick_steps", "judge_weights", "rules2_from_physics", "stun_speed",
            "stun_window_s", "stun_strength", "stun_s", "stun_max", "count_s", "up_hold_s", "up_height", "flinch_m",
            "judge_noise", "daze_react")
    for k in need:
        if k not in r:
            fail(f"rules.json has no '{k}'")
    if FAILURES == before:
        if len(r["judge_weights"]) != 3 or any(len(w) != 4 for w in r["judge_weights"]):
            fail("judge_weights is not 3 judges x 4 terms")
        if not 0.0 <= r["judge_noise"] < 1.0:
            fail(f"judge_noise {r['judge_noise']} is outside [0, 1)")
        if not 0.0 < r["stun_strength"] <= 1.0:
            fail(f"stun_strength {r['stun_strength']} is outside (0, 1]")
        if r["up_hold_s"] >= r["count_s"]:
            fail("up_hold_s is not shorter than count_s: nobody could beat the count")
    if FAILURES == before:
        print(f"  [PASS] {len(r)} rules; rules 2 from physics {r['rules2_from_physics']}")


def main():
    print("=== PoSlap constant parity (the three engines) ===")
    check_generated()
    check_used()
    check_observation_sizes()
    check_rates()
    check_rules()
    if FAILURES:
        print(f"\n{FAILURES} CHECK(S) FAILED - the engines do not agree.")
        return 1
    print("\nALL THREE ENGINES READ THE SAME NUMBERS.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
