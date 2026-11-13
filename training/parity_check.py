"""Python vs browser: the same seeded matches through training/match.py and Web/sim.js, referee event for event.

    training/.venv/Scripts/python.exe training/parity_check.py            (from the repo root or from training/)
    ... parity_check.py --stun 0.55      also plays rules 2 with a low stun threshold (stuns, counts, count-outs)

Plays Grandpa vs Kim on the shipped brains: rules 1 (what ships), then rules 2 forced on the same physics-1 models
(SLAP_RULES=2 on both sides - no brain trained on physics 4 exists yet), each with first striker red and blue. Every
log is replayed by `node Web/parity.mjs`, which compares strike, foul, knockdown, stun, count, count_end, turn_end,
round and result (exact; after a stun the fall's own timings within a tolerance - see parity.mjs).
Needs: mujoco, numpy, onnxruntime (the training venv has them) and `npm ci` in Web/. Exit 1 on any mismatch.
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
RED, BLUE = "models/brains/grandpa_final_vs_kim.onnx", "models/brains/kim_final_vs_grandpa.onnx"   # loader.js "gk"


def play(rules, stun, specs):
    """Run in a child process: the rules switch is read when match.py is imported."""
    env = dict(os.environ)
    env.pop("SLAP_RULES", None)
    if rules == 2:
        env["SLAP_RULES"] = "2"
    code = ("import sys, constants, match\n"
            f"stun = {stun!r}\n"
            "if stun is not None: constants.RULES['stun_speed'] = stun\n"
            f"for seed, first in {specs!r}:\n"
            f"    print(match.play({RED!r}, first, blue={BLUE!r}, character='grandpa', blue_character='kim', seed=seed)[2])\n")
    out = subprocess.run([sys.executable, "-c", code], cwd=HERE, env=env, capture_output=True, text=True)
    if out.returncode:
        sys.exit(f"match.py failed:\n{out.stderr[-2000:]}")
    return [Path(p) for p in out.stdout.split()], env


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stun", type=float, default=None, help="also play rules 2 with this stun_speed on both sides (0.55 stuns the shipped brains)")
    a = ap.parse_args()
    runs = [(1, None, [(5, 0), (11, 1)]), (2, None, [(5, 1), (2, 0)])] + ([(2, a.stun, [(3, 1), (4, 0)])] if a.stun else [])
    failed = 0
    for rules, stun, specs in runs:
        logs, env = play(rules, stun, specs)
        if stun is not None:
            env["SLAP_STUN_SPEED"] = str(stun)
        for path in logs:
            r = subprocess.run(["node", "Web/parity.mjs", str(path), "gk"], cwd=ROOT, env=env, capture_output=True, text=True)
            lines = (r.stdout + r.stderr).strip().splitlines()
            print(f"rules {rules}{f' (stun {stun})' if stun is not None else ''}: " + " | ".join(x.strip()[:300] for x in lines[-3:] if "jsonl:" not in x))
            failed += r.returncode != 0
            path.unlink(missing_ok=True)
    print("PARITY " + ("FAILED" if failed else "PASS") + f" ({sum(len(s) for _, _, s in runs) - failed}/{sum(len(s) for _, _, s in runs)})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
