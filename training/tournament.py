"""Roster tournament: every entry of roster.json against every other, in CPU MuJoCo (match.py: the rules Unreal plays).

    .venv/Scripts/python.exe tournament.py [--matches 4] [--roster roster.json]

An entry is a fighter: a character card and his own brain. Each pairing plays `matches` matches, alternating corners and
the coin toss. Standings: 3 points a win, 1 a draw; then KOs. Writes Logs/Tournament/<YYMMDD_HHMM>/standings.html and
results.jsonl (every match's log stays in Logs/Gameplay/), and prints the Unreal command to watch any pairing.
v1: one arena model per card pair; until a second mesh exists every entry must use the same card (grandpa).
"""
import argparse, itertools, json, time
from multiprocessing import Pool
from pathlib import Path

import match

HERE = Path(__file__).parent


def one(job):
    red, blue, first = job
    res, turns, path = match.play(red["brain"], first, blue=blue["brain"])
    return dict(red=red["name"], blue=blue["name"], first=first, how=res["how"], winner=res["winner"], log=path,
                strikes={c: sum(t["touch"] is not None and t["palm"] for t in turns if t["striker"] == c) for c in ("red", "blue")})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--roster", default=str(HERE / "roster.json"))
    ap.add_argument("--matches", type=int, default=4, help="per pairing (even: both corners, both tosses)")
    a = ap.parse_args()
    roster = json.loads(Path(a.roster).read_text())
    cards = {e["card"] for e in roster}
    if len(cards) > 1:
        raise SystemExit(f"cards {sorted(cards)}: a mixed pairing needs its own <red>_vs_<blue> arena (build_body.py) and "
                         "per-corner fingerprints in match.py; add both with the second mesh")
    jobs = []
    for x, y in itertools.combinations(roster, 2):
        for i in range(a.matches):
            red, blue = (x, y) if i % 2 == 0 else (y, x)   # swap corners every match
            jobs.append((red, blue, (i // 2) % 2))         # and the toss every two
    with Pool(min(20, len(jobs))) as p:
        out = p.map(one, jobs)

    table = {e["name"]: dict(played=0, won=0, drawn=0, lost=0, ko=0, points=0) for e in roster}
    for r in out:
        for side in ("red", "blue"):
            s = table[r[side]]
            s["played"] += 1
            if r["winner"] is None:
                s["drawn"] += r["how"] == "draw"
            elif r["winner"] == side:
                s["won"] += 1
                s["ko"] += r["how"] == "KO"
            else:
                s["lost"] += 1
            s["points"] = 3 * s["won"] + s["drawn"]
    ranked = sorted(table.items(), key=lambda kv: (-kv[1]["points"], -kv[1]["ko"], kv[0]))

    dest = HERE.parent / "Logs" / "Tournament" / time.strftime("%y%m%d_%H%M")
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in out))
    brain = {e["name"]: e["brain"] for e in roster}
    rows = "".join(f"<tr><td>{i + 1}</td><td>{n}</td>" + "".join(f"<td>{s[k]}</td>" for k in ("played", "won", "drawn", "lost", "ko", "points"))
                   + "</tr>" for i, (n, s) in enumerate(ranked))
    games = "".join(f"<tr><td>{r['red']}</td><td>{r['blue']}</td><td>{r['first']}</td><td>{r['winner'] or '-'}</td><td>{r['how']}</td>"
                    f"<td>{r['strikes']['red']}-{r['strikes']['blue']}</td></tr>" for r in out)
    (dest / "standings.html").write_text(
        "<!doctype html><meta charset='utf-8'><title>Tournament</title><style>body{font:15px system-ui;margin:24px}"
        "td,th{padding:4px 10px;border-bottom:1px solid #ccc;text-align:left}</style>"
        f"<h1>Tournament {time.strftime('%Y-%m-%d %H:%M')}</h1><table><tr><th>#</th><th>Fighter</th><th>P</th><th>W</th>"
        f"<th>D</th><th>L</th><th>KO</th><th>Pts</th></tr>{rows}</table><h2>Matches</h2><table><tr><th>Red</th><th>Blue</th>"
        f"<th>Toss (0 red)</th><th>Winner</th><th>How</th><th>Clean strikes</th></tr>{games}</table>", encoding="utf-8")
    for i, (n, s) in enumerate(ranked):
        print(f"{i + 1}. {n:24s} {s['points']:3d} pts  W{s['won']} D{s['drawn']} L{s['lost']} KO{s['ko']}")
    print("standings:", dest / "standings.html")
    if len(ranked) >= 2:
        r, b = brain[ranked[0][0]], brain[ranked[1][0]]
        print(f"watch the top two in Unreal: UnrealEditor.exe PoSlapUnreal.uproject -game -SlapRedBrain=training/{r} -SlapBlueBrain=training/{b}")


if __name__ == "__main__":
    main()
