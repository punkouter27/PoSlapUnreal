"""HTML report for a training run (AGENTS.md: runs over 30 min). Top-3 metric charts, change vs the previous run
of the same level, and Toddler / Child / Adult summaries.

    .venv/Scripts/python.exe report.py runs/<run>
"""
import base64, io, json, sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

TOP3 = [("episode_return", "Episode return (higher is better)"), ("fall_rate", "Fall rate (lower is better)"),
        ("episode_s", "Seconds standing per episode")]


def _chart(rows, key, title):
    fig, ax = plt.subplots(figsize=(6, 2.6), dpi=110)
    ax.plot([r["min"] for r in rows], [r[key] for r in rows], color="#3b6fd8")
    ax.set_title(title, fontsize=10); ax.set_xlabel("minutes"); ax.grid(alpha=.3)
    buf = io.BytesIO(); fig.tight_layout(); fig.savefig(buf, format="png"); plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def write_report(run: Path) -> Path:
    run = Path(run)
    rows = [json.loads(l) for l in (run / "telemetry.jsonl").read_text().splitlines() if l.strip()]
    meta = json.loads((run / "meta.json").read_text())
    prev = sorted((p for p in run.parent.glob(f"{meta['char']}_{meta['level']}_*/meta.json") if p.parent != run),
                  key=lambda p: p.stat().st_mtime)
    last = json.loads(prev[-1].read_text()) if prev else None
    delta = lambda k: f"{meta[k] - last[k]:+.3f}" if last else "first run"
    stand = rows[-1]["episode_s"] if rows else 0
    tiers = {
        "Toddler": f"Grandpa practised {'and learned' if meta['graduated'] else 'but has not yet learned'} to stay on his feet. "
                   f"He now stands about {stand:.0f} seconds at a time.",
        "Child": f"After {meta['minutes']:.0f} minutes and {meta['steps'] / 1e6:.0f} million tries, he falls in "
                 f"{meta['fall_rate'] * 100:.0f}% of episodes while being pushed around.",
        "Adult": f"{meta['level']}: episode return {meta['episode_return']:.0f} ({delta('episode_return')} vs last run), "
                 f"fall rate {meta['fall_rate']:.3f} ({delta('fall_rate')}), exam {meta['graduated'] or 'not passed'}, NaN {meta['nan']}.",
    }
    charts = "".join(f'<img src="data:image/png;base64,{_chart(rows, k, t)}">' for k, t in TOP3)
    html = f"""<!doctype html><meta charset="utf-8"><title>{run.name}</title>
<style>body{{font:15px system-ui;max-width:760px;margin:24px auto;padding:0 16px}} img{{width:100%}} dt{{font-weight:600}}</style>
<h1>{run.name}</h1><dl>{''.join(f'<dt>{k}</dt><dd>{v}</dd>' for k, v in tiers.items())}</dl>{charts}"""
    out = run / "report.html"
    out.write_text(html, encoding="utf-8")
    return out


if __name__ == "__main__":
    print(write_report(Path(sys.argv[1])))
