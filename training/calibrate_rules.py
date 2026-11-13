"""Read the rules-2 thresholds off real matches: head speed, pre-touch head movement, stuns, counts, fouls.

    .venv/Scripts/python.exe calibrate_rules.py <red.onnx> [--blue <onnx>] [--char kim] [--blue-char grandpa]
                                                [--physics 4] [--matches 48] [--rules 2]

Plays `matches` seeded matches (start angles jittered as in the exam) and prints the percentiles the numbers in
rules.json are set from:
  stun_speed   against "head speed in the stun window": the share of landed strikes at or above it is the stun rate
  flinch_m     against "head move during the swing": it should sit above what a braced, honest defender does
and what the rules then called (stuns, counts beaten or not, fouls, decisions).
--rules 2 forces rules 2 on a physics below 4 (python only: SLAP_RULES=2), to see what present brains would get.
Nothing is written: change rules.json by hand, run gen_constants.py, and commit both generated files with it.
"""
import argparse
import json
import os
import sys
from multiprocessing import Pool


def one(args):
    import match
    red, blue, rc, bc, i = args
    res, turns, path = match.play(red, i % 2, blue=blue, character=rc, blue_character=bc, seed=i, jitter=match.EXAM_JITTER)
    ev = [json.loads(line) for line in open(path)]
    os.remove(path)
    return res, turns, [e for e in ev if e["type"] in ("realism", "stun", "count_end", "foul")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("onnx")
    ap.add_argument("--blue", default=None)
    ap.add_argument("--char", default="grandpa")
    ap.add_argument("--blue-char", default=None)
    ap.add_argument("--physics", type=int, default=4)
    ap.add_argument("--matches", type=int, default=48)
    ap.add_argument("--rules", type=int, default=None, choices=(2,))
    a = ap.parse_args()
    os.environ["SLAP_PHYSICS"] = str(a.physics)   # before the workers start: they read both from the environment
    if a.rules:
        os.environ["SLAP_RULES"] = str(a.rules)
    import numpy as np
    from constants import RULES, rules2
    with Pool(min(16, a.matches)) as p:
        out = p.map(one, [(a.onnx, a.blue, a.char, a.blue_char, i) for i in range(a.matches)])
    turns = [t for _, ts, _ in out for t in ts]
    landed = [t for t in turns if t["touch"] is not None]
    ev = [e for _, _, es in out for e in es]
    pct = lambda xs: "  ".join(f"p{q} {np.percentile(xs, q):.2f}" for q in (10, 25, 50, 75, 90, 99)) if len(xs) else "-"
    r2 = rules2(a.physics)
    print(f"physics {a.physics}, rules {2 if r2 else 1}: {a.matches} matches, {len(turns)} turns, {len(landed)} strikes landed "
          f"({sum(t['palm'] for t in landed)} palm-led)")
    print(f"  hit speed (m/s)                    {pct([t['speed'] for t in landed])}")
    print(f"  head speed peak, whole window      {pct([t['head_peak'] for t in landed])}")
    win = [e["head_window"] for e in ev if e["type"] == "realism" and e.get("head_window") is not None]
    print(f"  head speed in the stun window      {pct(win)}    <- stun_speed is {RULES['stun_speed']}"
          f" ({sum(x >= RULES['stun_speed'] for x in win)} of {len(win)} at or above)")
    print(f"  head move during the swing (m)     {pct([e['head_off_m'] for e in ev if e['type'] == 'realism'])}    <- flinch_m is {RULES['flinch_m']}")
    print(f"  ends       { {e: sum(str(t['end']) == e for t in turns) for e in sorted({str(t['end']) for t in turns})} }")
    print(f"  results    { {h: sum(r['how'] == h for r, _, _ in out) for h in sorted({r['how'] for r, _, _ in out})} }")
    counts = [e for e in ev if e["type"] == "count_end"]
    print(f"  KO per landed strike {sum(t['ko'] for t in landed) / max(1, len(landed)):.0%}; stuns {sum(t.get('stun', 0) > 0 for t in landed)}; "
          f"counts {len(counts)} ({sum(e['up'] for e in counts)} beaten, median {np.median([e['count'] for e in counts]) if counts else 0:.1f} s)")
    print(f"  fouls      { {w: sum(any(x == w for _, x in t['fouls']) for t in turns) for w in sorted({x for t in turns for _, x in t['fouls']})} }")


if __name__ == "__main__":
    sys.exit(main())
