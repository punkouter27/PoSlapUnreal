"""Generate plain-language offline HTML training reports (hourly status & final summary)."""
import argparse, html, json, time
from pathlib import Path
from night_report import COLORS, PRETTY, RUNS, read_jsonl

ROOT = Path(__file__).resolve().parent.parent


def svg_chart(title, unit, series, width=720, height=220):
    """Render a self-contained inline SVG learning curve with start/peak/current annotations."""
    pts = [p for s in series for p in s["pts"]]
    if not pts:
        return f"<div class='card'><h3>{html.escape(title)}</h3><p class='muted'>Collecting initial samples...</p></div>"
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    x0, x1 = min(xs), max(xs) + (1e-6 if min(xs) == max(xs) else 0.0)
    y0, y1 = min(0.0, min(ys)), max(ys) + (1.0 if min(ys) == max(ys) else 0.0)
    L, R, T, B = 54, 18, 22, 30
    px = lambda v: L + (v - x0) / (x1 - x0) * (width - L - R)
    py = lambda v: height - B - (v - y0) / (y1 - y0) * (height - T - B)
    grid = "".join(f"<line x1='{L}' y1='{py(y0+(y1-y0)*i/4):.1f}' x2='{width-R}' y2='{py(y0+(y1-y0)*i/4):.1f}' stroke='#e2e8f0'/>"
                   f"<text x='{L-6}' y='{py(y0+(y1-y0)*i/4)+4:.1f}' text-anchor='end' font-size='11' fill='#64748b'>{y0+(y1-y0)*i/4:.1f}</text>" for i in range(5))
    paths, ann = [], []
    for s in series:
        d = " ".join(f"{'L' if i else 'M'}{px(x):.1f},{py(y):.1f}" for i, (x, y) in enumerate(s["pts"]))
        paths.append(f"<path d='{d}' fill='none' stroke='{s['color']}' stroke-width='2.5'/>")
        best_x, best_y = max(s["pts"], key=lambda p: p[1])
        cur_x, cur_y = s["pts"][-1]
        ann.append(f"<circle cx='{px(best_x):.1f}' cy='{py(best_y):.1f}' r='4' fill='{s['color']}'/>"
                   f"<text x='{min(width-90, px(best_x)+6):.1f}' y='{max(14, py(best_y)-6):.1f}' font-size='11' fill='{s['color']}'>{html.escape(s['name'])} peak {best_y:.1f}{unit}</text>"
                   f"<circle cx='{px(cur_x):.1f}' cy='{py(cur_y):.1f}' r='3' fill='#0f172a'/>")
    return f"<div class='card'><h3>{html.escape(title)}</h3><svg viewBox='0 0 {width} {height}' style='width:100%;height:auto'>{grid}{''.join(paths)}{''.join(ann)}<text x='{(L+width-R)/2:.0f}' y='{height-6}' text-anchor='middle' font-size='11' fill='#64748b'>Training minutes</text></svg></div>"


def load_queue_state(queue_name):
    """Load queue stages, active/completed run telemetry, and overall ETA."""
    queue = json.loads((RUNS / f"{queue_name}.json").read_text())
    active = next((s for s in queue if s.get("run") and not s.get("done")), None)
    done = [s for s in queue if s.get("done")]
    pending = [s for s in queue if not s.get("done")]
    runs = {s["run"]: {"stage": s, "tele": read_jsonl(RUNS / s["run"] / "telemetry.jsonl"),
                       "meta": json.loads((RUNS / s["run"] / "meta.json").read_text()) if (RUNS / s["run"] / "meta.json").exists() else {}}
            for s in queue if s.get("run")}
    rem_min = sum(float(s.get("minutes", 0)) for s in pending)
    if active and active["run"] in runs and runs[active["run"]]["tele"]:
        rem_min = max(0.0, rem_min - runs[active["run"]]["tele"][-1].get("min", 0.0))
    return queue, active, done, pending, runs, rem_min


def build_html(queue_name, final=False):
    """Build plain-English self-contained HTML for hourly status or final summary."""
    queue, active, done, pending, runs, rem_min = load_queue_state(queue_name)
    cur = runs.get(active["run"]) if active else (runs.get(done[-1]["run"]) if done else None)
    last = cur["tele"][-1] if cur and cur["tele"] else {}
    ret = last.get("strike_return", last.get("guard_return", last.get("return")))
    fall = last.get("strike_fall", last.get("guard_fall", last.get("absorb_fall")))
    kl, blow = last.get("kl"), last.get("blowups", 0)
    top3 = [
        ("Combat Score (Reward)", f"{ret:.1f}" if ret is not None else "Warming up", "Measures how well the fighter lands clean, fast hits while keeping balance. Higher scores mean smarter timing and stronger slaps."),
        ("Stumble & Fall Rate", f"{100*fall:.0f}%" if fall is not None else "Warming up", "Shows how often a fighter loses footing while swinging or defending. Lower percentages mean realistic, planted footwork."),
        ("Learning & Physics Health", f"KL {kl:.4f} / Blow-ups {blow}" if kl is not None else "Initializing GPU", "Tracks whether training adjustments are smooth (KL near 0.01-0.02) and free of physics glitches (0 blow-ups).")
    ]
    roster = []
    for c in ("kim", "grandpa", "matt", "mom", "tong"):
        brains = sorted((RUNS.parent / "models" / "brains").glob(f"{c}*.onnx"))
        c_done = [s for s in done if s.get("char") == c]
        status = f"Training {active['level']}" if active and active.get("char") == c else (f"Completed {len(c_done)} stage(s)" if c_done else "Queued")
        roster.append(f"<tr><td><b>{PRETTY[c]}</b></td><td>{status}</td><td>{sum(1 for s in queue if s.get('char')==c and not s.get('done'))} queued</td><td>{', '.join(b.stem for b in brains[:3]) or 'none'}</td></tr>")
    ret_series, fall_series, offset = [], [], 0.0
    for s in queue:
        if s.get("run") and s["run"] in runs and runs[s["run"]]["tele"]:
            tele = runs[s["run"]]["tele"]
            c = COLORS.get(s.get("char", "grandpa"), "#2a78d6")
            ret_series.append({"name": f"{PRETTY.get(s.get('char',''), '')} {s['level']}", "color": c, "pts": [(offset + t.get("min", 0), t.get("strike_return", t.get("guard_return", t.get("return", 0)))) for t in tele if any(k in t for k in ("strike_return", "guard_return", "return"))]})
            fall_series.append({"name": f"{PRETTY.get(s.get('char',''), '')} {s['level']}", "color": c, "pts": [(offset + t.get("min", 0), 100 * t.get("strike_fall", t.get("guard_fall", t.get("absorb_fall", 0)))) for t in tele if any(k in t for k in ("strike_fall", "guard_fall", "absorb_fall"))]})
            offset += tele[-1].get("min", 0)
    run_ckpts = [f"<li><code>{html.escape(s['run'])}/{f}</code> ({(RUNS/s['run']/f).stat().st_size//1024} KB)</li>" for s in queue if s.get("run") for f in ("best.pt", "latest.pt") if (RUNS / s["run"] / f).exists()]
    weights = run_ckpts + [f"<li><code>models/brains/{html.escape(p.name)}</code> ({p.stat().st_size//1024} KB)</li>" for p in sorted((RUNS.parent / "models" / "brains").glob("*.onnx"))]
    title = "PoSlap Final Training Summary" if final else "PoSlap Hourly Training Status"
    eta_txt = "Stopped / Complete" if final else f"~{rem_min/60:.1f} hours upper cap remaining ({len(pending)} stages left; faster on early graduation)"
    extra = f"<div class='card'><h2>Saved Weights &amp; Next Priorities</h2><p>Active checkpoints live in <code>training/runs/</code> and shipped weights in <code>training/models/brains/</code>. Next priorities: resume Kim L4 via <code>--resume</code>, run <code>calibrate_rules.py</code>, then train Grandpa, Matt, Mom, Tong, and the 5-fighter league.</p><ul>{''.join(weights)}</ul></div>" if final else ""
    return f"""<!doctype html><meta charset='utf-8'><title>{title}</title><style>body{{font-family:system-ui,sans-serif;max-width:980px;margin:24px auto;padding:0 16px;background:#f8fafc;color:#0f172a}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}}.card{{background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:14px;margin:12px 0}}table{{width:100%;border-collapse:collapse}}th,td{{text-align:left;padding:6px 8px;border-bottom:1px solid #e2e8f0;font-size:14px}}.muted{{color:#64748b;font-size:13px}}</style>
<h1>{title}</h1><p class='muted'>Updated {time.strftime('%Y-%m-%d %H:%M:%S')} &middot; Active: <b>{html.escape((active or {}).get('run','none'))}</b> &middot; ETA: {html.escape(eta_txt)}</p>
<div class='grid'>{''.join(f"<div class='card'><div class='muted'>{h}</div><h2>{v}</h2><p>{d}</p></div>" for h,v,d in top3)}</div>
<div class='card'><h2>Fighter Skill Overview</h2><table><tr><th>Fighter</th><th>Status</th><th>Remaining</th><th>Existing Brains</th></tr>{''.join(roster)}</table></div>
{svg_chart('Learning Progress (Combat Score)', '', [s for s in ret_series if s['pts']])}
{svg_chart('Balance & Stability (Fall Rate %)', '%', [s for s in fall_series if s['pts']])}
{extra}</body></html>"""


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--queue", default="overnight_queue")
    ap.add_argument("--out", default=None)
    ap.add_argument("--final", action="store_true")
    a = ap.parse_args()
    out = Path(a.out) if a.out else ROOT / ("training_final_summary.html" if a.final else "training_status_hourly.html")
    out.write_text(build_html(a.queue, a.final), encoding="utf-8")
    print("wrote", out)
