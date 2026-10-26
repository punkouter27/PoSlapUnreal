"""The league: Elo and scouting numbers per brain from every logged match (Python and Unreal, Logs/Gameplay/).

    .venv/Scripts/python.exe scouting.py            -> models/league.json (Unreal's tale of the tape, the web page's tape)

A match counts when its match_start names both brains (match.py and USlapController log them from 2026-10-26); older
tournament matches are credited through Logs/Tournament/*/results.jsonl and roster.json. Elo in log order, K 24 from
1500; a brain against itself moves nobody's rating. Per brain, as striker: KO rate per landed strike, clean-palm rate,
mean palm speed at the touch, self-falls per turn struck, mean HIC (Unreal's "impact" lines); as defender: strikes
absorbed without a KO.
"""
import json
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent
OUT = HERE / "models" / "league.json"
K, START = 24.0, 1500.0


def tournament_brains():
    """log file name -> {corner: brain} for tournament matches logged before match_start named the brains."""
    roster = {r["name"]: Path(r["brain"]).stem for r in json.loads((HERE / "roster.json").read_text())}
    out = {}
    for res in (ROOT / "Logs" / "Tournament").glob("*/results.jsonl"):
        for line in res.read_text().splitlines():
            r = json.loads(line)
            if r["red"] in roster and r["blue"] in roster:
                out[Path(r["log"]).name] = {"red": roster[r["red"]], "blue": roster[r["blue"]]}
    return out


def main():
    known = tournament_brains()
    elo = defaultdict(lambda: START)
    st = defaultdict(lambda: defaultdict(float))
    logs = sorted((ROOT / "Logs" / "Gameplay").glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime)
    counted = 0
    for log in logs:
        try:
            ev = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines() if l.strip()]
        except (ValueError, OSError):
            continue   # a log still being written, or broken
        start = next((e for e in ev if e["type"] == "match_start"), None)
        result = next((e for e in ev if e["type"] == "result"), None)
        brains = (start or {}).get("brains") or known.get(log.name)
        if not (start and result and brains) or result["how"] == "blow-up":
            continue
        counted += 1
        for e in ev:
            if e["type"] == "turn_end":
                s, d = brains[e["striker"]], brains["blue" if e["striker"] == "red" else "red"]
                st[s]["turns"] += 1
                st[s]["self_falls"] += any(f.startswith(f"{e['striker']}: knocked down") for f in e["fouls"])
                if e["touch"] is not None:
                    st[s]["touches"] += 1
                    st[s]["clean"] += bool(e["palm"])
                    st[s]["speed_sum"] += e["speed"]
                    st[s]["kos"] += bool(e["ko"])
                    st[d]["absorbed"] += not e["ko"]
                    st[d]["faced"] += 1
            elif e["type"] == "impact":
                st[brains[e["striker"]]]["hic_sum"] += e["hic"]
                st[brains[e["striker"]]]["hics"] += 1
        r, b = brains["red"], brains["blue"]
        w = result.get("winner")
        for x in (r, b):
            st[x]["matches"] += 1
        if w:
            st[brains[w]]["wins"] += 1
            st[brains["blue" if w == "red" else "red"]]["losses"] += 1
        else:
            st[r]["draws"] += 1
            st[b]["draws"] += 1
        if r != b:   # a brain against itself teaches the ladder nothing
            score = 0.5 if not w else 1.0 if w == "red" else 0.0
            expect = 1.0 / (1.0 + 10 ** ((elo[b] - elo[r]) / 400.0))
            elo[r], elo[b] = elo[r] + K * (score - expect), elo[b] - K * (score - expect)

    div = lambda a, b: round(a / b, 3) if b else 0.0
    league = {}
    for name, x in st.items():
        league[name] = dict(elo=round(elo[name], 1), matches=int(x["matches"]), wins=int(x["wins"]), losses=int(x["losses"]),
                            draws=int(x["draws"]), ko_rate=div(x["kos"], x["touches"]), clean_rate=div(x["clean"], x["touches"]),
                            speed=div(x["speed_sum"], x["touches"]), self_fall=div(x["self_falls"], x["turns"]),
                            absorbed=div(x["absorbed"], x["faced"]), hic=div(x["hic_sum"], x["hics"]))
    league = dict(sorted(league.items(), key=lambda kv: -kv[1]["elo"]))
    OUT.write_text(json.dumps({"matches": counted, "brains": league}, indent=1), encoding="utf-8")
    print(f"{counted} matches -> {OUT}")
    print(f"{'brain':32} {'elo':>6} {'W-L-D':>9} {'KO':>5} {'clean':>6} {'m/s':>5} {'fall':>5} {'absorb':>6}")
    for n, x in league.items():
        print(f"{n:32} {x['elo']:6.0f} {x['wins']:>3}-{x['losses']}-{x['draws']:<3} {x['ko_rate']:5.0%} {x['clean_rate']:6.0%} "
              f"{x['speed']:5.1f} {x['self_fall']:5.0%} {x['absorbed']:6.0%}")


if __name__ == "__main__":
    main()
