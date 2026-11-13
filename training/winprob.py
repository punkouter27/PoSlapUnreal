"""Win probability: P(red wins) from the match state, fitted on every logged match (Python and Unreal, Logs/Gameplay/).

    .venv/Scripts/python.exe winprob.py                  -> models/winprob.json (Unreal's HUD, Web/live.py's /api/match)
    .venv/Scripts/python.exe winprob.py --simulate 40    first play 40 more logged matches over the roster's pairings

Display only: nothing here reaches the physics, the brains or the referee.

A logistic model with no bias term over five numbers that all change sign when the corners swap, so a level match is
50 % by construction and every match counts twice (as played, and mirrored):
    score      the judges' running totals, red minus blue, averaged over the three cards (points)
    downs      knockdowns taken, blue minus red
    neck       neck strength left, red minus blue (0..1 above the 0.5 floor)
    stamina    stamina left, red minus blue (0..1 above the 0.7 floor)
    late       score x the share of the match played (a lead late is worth more than the same lead early)
One sample at the start of a match and one after every turn. `state_features` is the one definition of those numbers:
Web/live.py imports it, and ASlapHUD::WinProb (SlapHUDWinProb.cpp) spells the same arithmetic out in C++.
"""
import argparse
import json
import math
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent
ROOT = HERE.parent
OUT = HERE / "models" / "winprob.json"
FEATURES = ("score", "downs", "neck", "stamina", "late")
MIN_STAMINA, MIN_NECK, TURNS = 0.7, 0.5, 6   # constants.py's fatigue floors; 3 rounds x 2 turns
L2 = 0.05


def left(v, floor):
    return min(1.0, max(0.0, (v - floor) / (1.0 - floor)))


def state_features(cards, downs, stamina, neck, turns_done):
    """cards: three [red, blue] running totals; downs / stamina / neck: [red, blue]; turns_done: turns ended so far."""
    score = sum(c[0] - c[1] for c in cards) / max(len(cards), 1)
    return [score, float(downs[1] - downs[0]), left(neck[0], MIN_NECK) - left(neck[1], MIN_NECK),
            left(stamina[0], MIN_STAMINA) - left(stamina[1], MIN_STAMINA), score * min(1.0, turns_done / TURNS)]


def probability(weights, x):
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, sum(w * v for w, v in zip(weights, x))))))


def samples(path):
    """One logged match -> ([features...], red won) or None (unfinished, a draw, a blow-up)."""
    cards, downs, cond, turns, xs, winner = [[0, 0]] * 3, [0, 0], {"red": [1.0, 1.0], "blue": [1.0, 1.0]}, 0, [], None
    snap = lambda: state_features(cards, downs, [cond["red"][0], cond["blue"][0]], [cond["red"][1], cond["blue"][1]], turns)
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return None
    for line in lines:
        if '"frame"' in line[:20]:
            continue
        try:
            e = json.loads(line)
        except ValueError:
            return None
        t = e.get("type")
        if t == "match_start":
            xs = [snap()]
        elif t == "knockdown":
            downs["red blue".split().index(e["athlete"])] += 1
        elif t == "turn_end":
            turns += 1
        elif t == "condition":      # after the turn's own turn_end: the state the next turn starts from
            cond = {"red": e["red"], "blue": e["blue"]}
            xs.append(snap())
        elif t == "round":
            cards = e["cards"]
            if xs:
                xs[-1] = snap()
        elif t == "result":
            winner = e.get("winner")
    if winner not in ("red", "blue") or not xs:
        return None
    return xs, 1.0 if winner == "red" else 0.0


def fit(X, y, steps=4000, lr=0.3):
    scale = np.maximum(np.abs(X).max(axis=0), 1e-9)   # features on one footing for the descent; weights go back unscaled
    Xs, w = X / scale, np.zeros(X.shape[1])
    for _ in range(steps):
        p = 1.0 / (1.0 + np.exp(-Xs @ w))
        w -= lr * (Xs.T @ (p - y) / len(y) + L2 * w)
    return w / scale


def dataset(matches):
    X, y, group = [], [], []
    for g, (xs, won) in enumerate(matches):
        for x in xs:
            X += [x, [-v for v in x]]       # as played, and with the corners swapped
            y += [won, 1.0 - won]
            group += [g, g]
    return np.array(X), np.array(y), np.array(group)


def simulate(n):
    """n logged matches over the roster's pairings that have an arena model, corners and first strike alternating."""
    import match
    roster = json.loads((HERE / "roster.json").read_text())
    pairs = [(a, b) for a in roster for b in roster
             if (HERE / "models" / f"{a['card']}_vs_{b['card']}_arena.xml").exists() and a is not b]
    for k in range(n):
        a, b = pairs[k % len(pairs)]
        t0 = time.time()
        res, _, log = match.play(str(HERE / a["brain"]), (k // len(pairs)) % 2, blue=str(HERE / b["brain"]),
                                 character=a["card"], blue_character=b["card"], seed=1000 + k)
        print(f"  {k + 1}/{n} {a['name']} v {b['name']}: {res} ({time.time() - t0:.0f} s) {Path(log).name}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--simulate", type=int, default=0, help="play this many more logged matches first")
    args = ap.parse_args()
    if args.simulate:
        simulate(args.simulate)
    seen, matches = set(), []
    for log in sorted((ROOT / "Logs" / "Gameplay").glob("*/*.jsonl")):
        s = samples(log)
        if s is None:
            continue
        key = json.dumps(s)             # an exact replay (a looping check run) counts once
        if key not in seen:
            seen.add(key)
            matches.append(s)
    if len(matches) < 8:
        raise SystemExit(f"only {len(matches)} finished matches in Logs/Gameplay: too few to fit (try --simulate 40)")
    X, y, group = dataset(matches)
    w = fit(X, y)
    # the honest error: each match predicted by a model that never saw it (Brier score; 0.25 = always saying 50 %)
    err = []
    for g in range(len(matches)):
        held = group == g
        wg = fit(X[~held], y[~held], steps=1500)
        err += list((1.0 / (1.0 + np.exp(-X[held] @ wg)) - y[held]) ** 2)
    out = {"features": list(FEATURES), "weights": [round(float(v), 5) for v in w], "matches": len(matches),
           "samples": int(len(y) // 2), "red_wins": int(sum(m[1] for m in matches)), "brier_held_out": round(float(np.mean(err)), 4),
           "brier_coin": 0.25, "fitted": time.strftime("%Y-%m-%d")}
    OUT.write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
