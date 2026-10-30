"""CPU-only card, model and staging checks. These are the checks a CI runner can make
without a GPU (verify_realism.py needs CUDA for the env steps).

    .venv/Scripts/python.exe verify_cards.py

Every expectation is DERIVED from the same file the app reads - no live value is
hard-coded here (the physics rate comes from the model's timestep, the joint set from
the reference arena, the card numbers from the card itself).
"""
import hashlib
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
DATA = ROOT / "Content" / "Data"
MODELS = HERE / "models"

# The arena XML that defines the shared body layout every other model must match.
REFERENCE_ARENA = MODELS / "grandpa_vs_kim_arena.xml"

FAILURES = 0


def fail(msg):
    global FAILURES
    print(f"  [FAIL] {msg}")
    FAILURES += 1


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def cards():
    """Every distinct card the roster uses."""
    roster = json.loads((HERE / "roster.json").read_text())
    return list(dict.fromkeys(r["card"] for r in roster))


def check_cards():
    print("\n=== [1/5] Character cards ===")
    before = FAILURES
    for name in cards():
        card_path = HERE / "characters" / f"{name}.json"
        if not card_path.exists():
            fail(f"{name}: no card at {card_path}")
            continue
        card = json.loads(card_path.read_text())
        for key in ("name", "display", "height_m", "mass_kg", "strength", "speed",
                    "flexibility", "toughness", "strike_mass", "stamina_per_strike",
                    "neck_damage_per_snap", "skeleton"):
            if key not in card:
                fail(f"{name}: card is missing '{key}'")
        if card.get("name") != name:
            fail(f"{name}: card's own name is '{card.get('name')}'")
        skel = HERE / card["skeleton"]
        if not skel.exists():
            fail(f"{name}: skeleton '{card['skeleton']}' not found")
        if card.get("strike_mass", 0) <= 0:
            fail(f"{name}: strike_mass is {card.get('strike_mass')} - the slap impulse would be zero (no KO possible)")
        print(f"  [PASS] {name}: {card['display']}, {card['height_m']} m, {card['mass_kg']} kg, "
              f"strike_mass {card['strike_mass']} kg")
    if FAILURES == before:
        print("  [PASS] every roster card is complete.")


def check_build():
    print("\n=== [2/5] Body build (rig, self-collision, balance, shoulder cone) ===")
    before = FAILURES
    for name in cards():
        res = subprocess.run(
            [sys.executable, str(HERE / "build_body.py"), str(HERE / "characters" / f"{name}.json")],
            capture_output=True, text=True,
        )
        if res.returncode != 0:
            fail(f"{name}: build_body.py failed\n{res.stdout[-2000:]}\n{res.stderr[-2000:]}")
            continue
        for line in res.stdout.splitlines():
            if line.startswith(("A8 ", "A9 ", "A10 ", "A11 ", "A12 ", "A13 ")):
                print("    " + line)
        for expect in ("A9 realism limits", "A10 self-collision", "A11 balance", "A12 fingerprint", "A13 shoulder cone"):
            if expect not in res.stdout:
                fail(f"{name}: build did not report '{expect}'")
    if FAILURES == before:
        print("  [PASS] every card builds and passes A8-A13.")


def check_staging():
    """The packaged (Android) build reads Content/Data. A staged card or fingerprint that
    is a build behind is the H3 regression: the athlete binds with the wrong numbers."""
    print("\n=== [3/5] Packaged staging (Content/Data) is not a build behind ===")
    before = FAILURES
    for name in cards():
        src_card = HERE / "characters" / f"{name}.json"
        dst_card = DATA / f"{name}.json"
        if not dst_card.exists():
            fail(f"{name}: {dst_card} is missing - a packaged build has no character card")
        elif sha(src_card.read_text()) != sha(dst_card.read_text()):
            fail(f"{name}: the staged card differs from training/characters/{name}.json")

        src_fp = MODELS / f"{name}_fingerprint.json"
        dst_fp = DATA / f"{name}_fingerprint.json"
        if not dst_fp.exists():
            fail(f"{name}: {dst_fp} is missing - a packaged build has no fingerprint")
            continue
        if sha(src_fp.read_text()) != sha(dst_fp.read_text()):
            fail(f"{name}: the staged fingerprint is a build behind (stale sha256)")
        fp = json.loads(dst_fp.read_text())
        solo = MODELS / f"{name}_solo.xml"
        if solo.exists() and fp.get("sha256") and sha(solo.read_text()) != fp["sha256"]:
            fail(f"{name}: fingerprint sha256 does not match models/{name}_solo.xml")
        print(f"  [PASS] {name}: card + fingerprint staged and current.")
    if FAILURES == before:
        print("  [PASS] Content/Data matches training/.")


CORNER_PREFIXES = ("red_", "blue_")
ACTUATOR_TAGS = ("motor", "position", "velocity", "general", "damper")


def per_athlete_layout(path):
    """The joint and actuator names of ONE athlete, with the corner prefix stripped.

    A solo model names its joints bare (``root``, ``waist_twist``); an arena model
    prefixes them per corner (``red_root``, ``blue_root``). Every model must describe
    the same body, so the per-athlete list is what has to match.
    """
    root = ET.parse(path).getroot()
    joints = [(j.get("name"), j.get("type", "hinge")) for j in root.iter("joint") if j.get("name")]
    acts = [m.get("joint") for m in root.iter()
            if m.tag in ACTUATOR_TAGS and m.get("joint")]
    jg, ag, free = {}, {}, {}
    for name, jtype in joints:
        for p in CORNER_PREFIXES:
            if name.startswith(p):
                jg.setdefault(p, []).append(name[len(p):])
                if jtype == "free":
                    free.setdefault(p, []).append(name[len(p):])
                break
        else:
            jg.setdefault("", []).append(name)
            if jtype == "free":
                free.setdefault("", []).append(name)
    for n in acts:
        for p in CORNER_PREFIXES:
            if n.startswith(p):
                ag.setdefault(p, []).append(n[len(p):])
                break
        else:
            ag.setdefault("", []).append(n)
    # The pelvis is the free-floating root: it has a joint but no motor, by design.
    return ({k: sorted(v) for k, v in jg.items()},
            {k: sorted(v) for k, v in ag.items()},
            {k: sorted(v) for k, v in free.items()})


def check_models():
    print("\n=== [4/5] Every model shares the same body layout ===")
    before = FAILURES
    if not REFERENCE_ARENA.exists():
        fail(f"reference model {REFERENCE_ARENA} is missing")
        return
    ref_j, ref_a, _ = per_athlete_layout(REFERENCE_ARENA)
    ref_joint = ref_j.get("red_", [])
    ref_act = ref_a.get("red_", [])
    for xml in sorted(MODELS.glob("*.xml")):
        jg, ag, free = per_athlete_layout(xml)
        for corner, joints in jg.items():
            if joints != ref_joint:
                fail(f"{xml.name} [{corner or 'solo'}]: joint set differs from the reference "
                     f"(missing {sorted(set(ref_joint) - set(joints))}, "
                     f"extra {sorted(set(joints) - set(ref_joint))})")
        for corner, acts in ag.items():
            if acts != ref_act:
                fail(f"{xml.name} [{corner or 'solo'}]: actuator set differs from the reference "
                     f"(missing {sorted(set(ref_act) - set(acts))}, "
                     f"extra {sorted(set(acts) - set(ref_act))})")
            else:
                # every joint needs a motor, except the free-floating pelvis root
                motors_needed = [j for j in jg.get(corner, []) if j not in free.get(corner, [])]
                if sorted(motors_needed) != sorted(acts):
                    fail(f"{xml.name} [{corner or 'solo'}]: {len(motors_needed)} motorised joints "
                         f"but {len(acts)} actuators - "
                         f"{sorted(set(motors_needed) ^ set(acts))} has no motor")
    cfg = json.loads((HERE / "configs" / "joint_and_strength_config.json").read_text())
    hz = cfg.get("physics_engine", {}).get("physics_hz")
    dt = None
    for xml in sorted(MODELS.glob("*_solo.xml")):
        m = re.search(r'timestep="([0-9.eE+-]+)"', xml.read_text())
        if m:
            dt = float(m.group(1))
            break
    if dt and hz:
        model_hz = round(1.0 / dt)
        if model_hz != hz:
            fail(f"the model's timestep ({dt} s = {model_hz} Hz) disagrees with the config's physics_hz ({hz})")
        else:
            print(f"  [PASS] the model steps at {model_hz} Hz, the config says {hz} Hz.")
    if FAILURES == before:
        print(f"  [PASS] {len(list(MODELS.glob('*.xml')))} models share "
              f"{len(ref_joint)} joints / {len(ref_act)} actuators per athlete.")


def check_brains():
    print("\n=== [5/5] Brains, cards and the roster line up ===")
    before = FAILURES
    roster = json.loads((HERE / "roster.json").read_text())
    card_names = set(cards())
    for entry in roster:
        if entry["card"] not in card_names:
            fail(f"roster entry '{entry['name']}' points at an unknown card '{entry['card']}'")
        brain = HERE / entry["brain"]
        if not brain.exists():
            fail(f"roster entry '{entry['name']}' points at a missing brain {entry['brain']}")
    brains_dir = ROOT / "Content" / "Data" / "Brains"
    if brains_dir.exists():
        for bin_file in sorted(brains_dir.glob("*.bin")):
            stem = bin_file.stem
            if not (MODELS / "brains" / f"{stem}.onnx").exists():
                fail(f"Content/Data/Brains/{bin_file.name} has no source brain "
                     f"training/models/brains/{stem}.onnx")
    if FAILURES == before:
        print(f"  [PASS] {len(roster)} roster entries and the packaged brains all resolve.")


def main():
    print("=== PoSlap card / model / staging verification (CPU only) ===")
    check_cards()
    check_build()
    check_staging()
    check_models()
    check_brains()
    if FAILURES:
        print(f"\n{FAILURES} CHECK(S) FAILED.")
        return 1
    print("\nALL CARD / MODEL / STAGING CHECKS PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
