"""HTML report for a training run (AGENTS.md: long runs). Three skill stages by training steps (Beginner = first 10 %,
Intermediate = 45-55 %, Advanced = final 10 %) comparing mean reward and success rate per role, top-3 metric charts,
and the change vs the previous run of the same level.

    .venv/Scripts/python.exe report.py runs/<run>
"""
import base64, io, json, sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

STAGES = [("Beginner", 0.0, 0.10), ("Intermediate", 0.45, 0.55), ("Advanced", 0.90, 1.0)]
# role -> (reward key, success key, success from row); runs before 2026-10-22 logged one role as episode_return/fall_rate
ROLES = {
    "guard": ("guard_return", "kept his feet", lambda r: 1 - r["guard_fall"]),
    "strike": ("strike_return", "clean hits", lambda r: r["strike_hit"]),
    "absorb": ("absorb_return", "slaps absorbed", lambda r: r["absorb_ok"]),
    "all": ("episode_return", "kept his feet", lambda r: 1 - r["fall_rate"]),
}


def _chart(rows, key, title):
    fig, ax = plt.subplots(figsize=(6, 2.6), dpi=110)
    ax.plot([r["steps"] / 1e6 for r in rows], [r[key] for r in rows], color="#3b6fd8")
    ax.set_title(title, fontsize=10); ax.set_xlabel("million steps"); ax.grid(alpha=.3)
    buf = io.BytesIO(); fig.tight_layout(); fig.savefig(buf, format="png"); plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def stages(rows):
    """{role: [(stage, mean reward, success rate), ...]} over the rows inside each stage's share of the run's steps."""
    total = rows[-1]["steps"]
    out = {}
    for role, (rk, _, ok) in ROLES.items():
        if rk not in rows[-1]:
            continue
        out[role] = []
        for name, lo, hi in STAGES:
            sel = [r for r in rows if lo * total <= r["steps"] <= hi * total and rk in r] or [rows[-1]]
            out[role].append((name, sum(r[rk] for r in sel) / len(sel), sum(ok(r) for r in sel) / len(sel)))
    return out


def write_report(run: Path) -> Path:
    run = Path(run)
    rows = [json.loads(l) for l in (run / "telemetry.jsonl").read_text().splitlines() if l.strip()]
    meta = json.loads((run / "meta.json").read_text())
    prev = sorted((p for p in run.parent.glob(f"{meta['char']}_{meta['level']}_*/meta.json") if p.parent != run),
                  key=lambda p: p.stat().st_mtime)
    last = json.loads(prev[-1].read_text()) if prev else {}
    table = ""
    for role, st in stages(rows).items():
        rk, what, _ = ROLES[role]
        cells = "".join(f"<td>{rew:.0f}</td><td>{100 * s:.0f} %</td>" for _, rew, s in st)
        delta = f"{meta[rk] - last[rk]:+.0f}" if rk in meta and rk in last else "first run"
        table += f"<tr><th>{role} ({what})</th>{cells}<td>{delta}</td></tr>"
    head = "".join(f"<th>{n}<br>reward</th><th>{n}<br>success</th>" for n, _, _ in STAGES)
    keys = [k for k in ("strike_hit", "absorb_ok", "guard_return", "strike_return", "absorb_return", "episode_return", "fall_rate")
            if k in rows[-1]][:3]
    charts = "".join(f'<img src="data:image/png;base64,{_chart(rows, k, k.replace("_", " "))}">' for k in keys)
    html = f"""<!doctype html><meta charset="utf-8"><title>{run.name}</title>
<style>body{{font:15px system-ui;max-width:860px;margin:24px auto;padding:0 16px}} img{{width:100%}}
table{{border-collapse:collapse;margin:12px 0}} td,th{{padding:4px 10px;border-bottom:1px solid #ccc;text-align:right}}</style>
<h1>{run.name}</h1>
<p>{meta['level']}: {meta['minutes']:.0f} min, {meta['steps'] / 1e6:.0f} M steps. Exam: {meta['graduated'] or 'not passed'}.
NaN: {meta['nan']}.</p>
<table><tr><th>role</th>{head}<th>reward vs last run</th></tr>{table}</table>
<p>Stages by training steps: Beginner = first 10 %, Intermediate = 45-55 %, Advanced = final 10 %.</p>{charts}"""
    out = run / "report.html"
    out.write_text(html, encoding="utf-8")
    return out


if __name__ == "__main__":
    print(write_report(Path(sys.argv[1])))
