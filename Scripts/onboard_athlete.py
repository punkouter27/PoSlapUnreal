"""Turnkey onboarding script for a new roster athlete (e.g. 3rd player).

Usage:
    training\\.venv\\Scripts\\python.exe Scripts/onboard_athlete.py <raw_fbx_or_glb> <name> --height 1.80 --mass 85.0

Steps automated:
1. Rigs and exports the mesh via Blender (`Scripts/rig_mixamo.py`) to `Content/Art/Exports/<Name>_rigged.fbx`
   and `training/characters/<name>_skeleton.json`.
2. Creates `training/characters/<name>.json` with anthropometric strike_mass scaling (`3.0 * mass / 78.0`)
   and default stance/slap style if not already present.
3. Runs `training/build_body.py` for `<name>` solo and all cross-pairings against existing roster athletes
   (`grandpa`, `kim`, etc.), verifying A8-A12 realism checks.
4. Searches for a clean reference slap (`training/slap_demo.py search --char <name>`).
5. Registers the athlete in `training/roster.json` and generates a ready-to-run round-robin training queue
   `training/runs/<name>_queue.json` (S1 -> S2 -> S3 -> L0-L3 -> L4 vs all opponents).
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRAINING = ROOT / "training"
CHARS = TRAINING / "characters"


def main():
    ap = argparse.ArgumentParser(description="Onboard a new roster athlete end-to-end.")
    ap.add_argument("mesh", help="Path to raw FBX/GLB (e.g. Content/Art/Raw/Player3.fbx)")
    ap.add_argument("name", help="Lowercase identifier (e.g. player3)")
    ap.add_argument("--display", default=None, help="Display name (default: uppercase name)")
    ap.add_argument("--slot", type=int, default=3, help="Roster slot number (default: 3)")
    ap.add_argument("--height", type=float, default=1.78, help="Height in meters (default: 1.78)")
    ap.add_argument("--mass", type=float, default=82.0, help="Mass in kg (default: 82.0)")
    ap.add_argument("--strength", type=float, default=0.75)
    ap.add_argument("--speed", type=float, default=0.85)
    ap.add_argument("--flexibility", type=float, default=0.95)
    ap.add_argument("--toughness", type=float, default=0.95)
    ap.add_argument("--blender", default="blender", help="Blender executable path")
    a = ap.parse_args()

    name = a.name.lower()
    display = a.display or name.upper()
    cap = name.capitalize()
    card_path = CHARS / f"{name}.json"
    skel_rel = f"characters/{name}_skeleton.json"
    export_rel = f"Content/Art/Exports/{cap}_rigged.fbx"

    if not (TRAINING / skel_rel).exists():
        print(f"[1/5] Rigging {a.mesh} via Blender (Scripts/rig_mixamo.py)...")
        subprocess.run([a.blender, "-b", "--python", str(ROOT / "Scripts" / "rig_mixamo.py"), "--",
                        str(Path(a.mesh).resolve()), name, str(a.height)], check=True)
    else:
        print(f"[1/5] Found existing skeleton {skel_rel}, skipping Blender rig step.")

    if not card_path.exists():
        print(f"[2/5] Creating character card {card_path}...")
        tmpl = json.loads((CHARS / "kim.json").read_text(encoding="utf-8"))
        card = {
            "name": name,
            "display": display,
            "slot": a.slot,
            "mesh": export_rel,
            "skeleton": skel_rel,
            "height_m": round(a.height, 2),
            "mass_kg": round(a.mass, 1),
            "strength": a.strength,
            "speed": a.speed,
            "flexibility": a.flexibility,
            "toughness": a.toughness,
            "stamina_per_strike": 0.04,
            "neck_damage_per_snap": 0.04,
            "strike_mass": round(3.0 * (a.mass / 78.0), 2),
            "notes": f"Roster athlete #{a.slot} ({display}), onboarded via Scripts/onboard_athlete.py.",
            "style": tmpl["style"],
        }
        card_path.write_text(json.dumps(card, indent=2), encoding="utf-8")
    else:
        print(f"[2/5] Using existing character card {card_path}.")

    print(f"[3/5] Building and validating MuJoCo body & cross-pairings for {name}...")
    subprocess.run([sys.executable, str(TRAINING / "build_body.py"), str(card_path)], check=True)
    existing = [p.stem for p in sorted(CHARS.glob("*.json"))
                if not p.stem.endswith("_skeleton") and p.stem != name]
    for other in existing:
        other_card = CHARS / f"{other}.json"
        subprocess.run([sys.executable, str(TRAINING / "build_body.py"), str(card_path), "--blue", str(other_card)], check=True)
        subprocess.run([sys.executable, str(TRAINING / "build_body.py"), str(other_card), "--blue", str(card_path)], check=True)

    print(f"[4/5] Searching reference slap trajectory for {name}...")
    subprocess.run([sys.executable, str(TRAINING / "slap_demo.py"), "search", "--char", name], check=True)

    print(f"[5/5] Updating training/roster.json and generating training/runs/{name}_queue.json...")
    roster_p = TRAINING / "roster.json"
    roster = json.loads(roster_p.read_text(encoding="utf-8")) if roster_p.exists() else []
    if not any(r.get("card") == name for r in roster):
        roster.append({"name": display, "card": name, "brain": f"models/brains/{name}_final.onnx"})
        roster_p.write_text(json.dumps(roster, indent=1), encoding="utf-8")

    queue = [
        {"char": name, "level": "S1", "minutes": 15, "keep_going": False, "no_gate": True},
        {"char": name, "level": "S2", "minutes": 20, "keep_going": False, "no_gate": True},
        {"char": name, "level": "S3", "minutes": 20, "keep_going": False, "no_gate": True},
        {"char": name, "level": "L0", "minutes": 30, "keep_going": False},
        {"char": name, "level": "L1", "minutes": 45, "keep_going": False},
        {"char": name, "level": "L2", "minutes": 60, "keep_going": False},
        {"char": name, "level": "L3", "minutes": 60, "keep_going": False},
        {"char": name, "level": "L4", "minutes": 90, "keep_going": True},
    ]
    for other in existing:
        queue.append({"char": name, "vs": other, "level": "L4", "minutes": 60, "keep_going": True, "no_gate": True})
        queue.append({"char": other, "vs": name, "level": "L4", "minutes": 60, "keep_going": True, "no_gate": True})
    q_path = TRAINING / "runs" / f"{name}_queue.json"
    q_path.parent.mkdir(parents=True, exist_ok=True)
    q_path.write_text(json.dumps(queue, indent=2), encoding="utf-8")
    print(f"Done! Ready to train {display} via: .venv\\Scripts\\python.exe overnight.py --char {name} --queue {name}_queue.json")


if __name__ == "__main__":
    main()

