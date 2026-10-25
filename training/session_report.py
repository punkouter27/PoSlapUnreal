"""Session progress report for an unattended spar queue (AGENTS.md: long runs).

Three skill stages by training steps - Beginner = first 10 %, Intermediate = 45-55 %,
Advanced = final 10 % - comparing mean reward and success rate per role, plus loss curves
(policy/value), entropy and the action noise, and the change vs the previous stage.

    .venv/Scripts/python.exe session_report.py spar3h2_queue
"""
import base64
import io
import json
import sys
import time
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).parent
RUNS = HERE / "runs"
STAGES = [("Beginner", 0.0, 0.10), ("Intermediate", 0.45, 0.55),
          ("Advanced", 0.90, 1.0)]
# role tag -> (reward key, success key, success from the row)
ROLES = {
    "strike": ("strike_return", "clean hits", lambda r: r.get("strike_hit", 0.0)),
    "absorb": ("absorb_return", "slaps absorbed", lambda r: r.get("absorb_ok", 0.0)),
    "guard": ("guard_return", "kept his feet", lambda r: 1 - r.get("guard_fall", 0.0)),
    "locomotion": ("episode_return", "kept his feet", lambda r: 1 - r.get("fall_rate", 0.0)),
}


def read_jsonl(p):
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def chart(series, title, ylabel="reward"):
    """series: [(label, xs, ys, color)] - each line carries its own x."""
    fig, ax = plt.subplots(figsize=(6.4, 2.7), dpi=110)
    for label, xs, ys, color in series:
        ax.plot(xs, ys, label=label, color=color, linewidth=1.4)
    ax.set_title(title, fontsize=10)
    ax.set_xlabel("million session steps", fontsize=8)
    ax.set_ylabel(ylabel, fontsize=8)
    ax.grid(alpha=.3)
    ax.legend(fontsize=6)
    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def stage_stats(rows):
    """{role: [(stage, mean reward, success rate)]} inside each stage's share of steps."""
    if not rows:
        return {}
    s0 = rows[0]["steps"]
    span = max(1, rows[-1]["steps"] - s0)
    out = {}
    for role, (rk, _, ok) in ROLES.items():
        if rk not in rows[-1]:
            continue
        out[role] = []
        for name, lo, hi in STAGES:
            sel = [r for r in rows if lo * span <= (r["steps"] - s0) <= hi * span and rk in r]
            if not sel:
                sel = [rows[-1]]
            out[role].append((name, sum(r[rk] for r in sel) / len(sel),
                              sum(ok(r) for r in sel) / len(sel)))
    return out


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "spar3h2_queue"
    queue_p = RUNS / (name + ".json")
    queue = json.loads(queue_p.read_text())
    log = read_jsonl(RUNS / (name + ".jsonl"))

    body, all_rows, cum = [], [], 0.0
    xs = []
    stage_table = ""
    charts = []
    for stage in queue:
        run = stage.get("run")
        if not run:
            continue
        d = RUNS / run
        tele = read_jsonl(d / "telemetry.jsonl")
        meta = json.loads((d / "meta.json").read_text()) if (d / "meta.json").exists() else {}
        if not tele:
            continue
        rows = [dict(r) for r in tele]
        # session steps: each run's own steps, offset by the runs before it
        span = rows[-1]["steps"] - rows[0]["steps"]
        for r in rows:
            r["sess_steps"] = cum + (r["steps"] - rows[0]["steps"])
            r["_run"] = run
        cum += span
        all_rows.extend(rows)
        ss = stage_stats(rows)
        for role, vals in ss.items():
            rk, what, _ = ROLES[role]
            cells = "".join("<td>%.1f</td><td>%.0f %%</td>" % (rew, 100 * s)
                            for _, rew, s in vals)
            gain = vals[-1][1] - vals[0][1]
            stage_table += ("<tr><th>%s &middot; %s<br><small>%s</small></th>%s"
                            "<td>%+.1f</td></tr>"
                            % (run, role, what, cells, gain))
        body.append(
            "<tr><td>%s</td><td>%s</td><td>%s</td><td>%.1f M</td><td>%s</td><td>%s</td></tr>"
            % (run, stage.get("char", "?"), stage.get("level", "?"),
               meta.get("steps", 0) / 1e6,
               ("%.3f" % meta["best_exam"]) if meta.get("best_exam") is not None else "-",
               (json.dumps(meta["graduated"]) if meta.get("graduated") else "-")))

    # session-wide charts (x = cumulative session steps): one line per run
    COLORS = ["#3b6fd8", "#d8743b", "#2fa05a", "#8e5bd0", "#c2356e", "#3aa8b0"]
    runs_in_order = []
    for stage in queue:
        if stage.get("run") and stage["run"] not in runs_in_order:
            runs_in_order.append(stage["run"])

    def series(key):
        out = []
        for i, run in enumerate(runs_in_order):
            rs = [r for r in all_rows if r.get("_run") == run]
            if rs:
                out.append((run, [r["sess_steps"] / 1e6 for r in rs],
                            [r.get(key, 0.0) for r in rs], COLORS[i % len(COLORS)]))
        return out

    charts.append(chart(series("strike_return"), "strike reward (session)"))
    charts.append(chart(series("absorb_return"), "absorb reward (session)"))
    charts.append(chart(series("policy_loss"), "policy loss (session)", "policy loss"))
    charts.append(chart(series("value_loss"), "value loss (session)", "value loss"))
    charts.append(chart(series("entropy"), "policy entropy (session)", "entropy"))
    charts.append(chart(series("strike_hit"), "clean-hit rate (session)", "hit rate"))

    # session three-stage comparison, over the whole session's steps
    sess_rows = sorted(all_rows, key=lambda r: r["sess_steps"])
    sess_stage_rows = ""
    for role, (rk, what, ok) in ROLES.items():
        if not sess_rows or rk not in sess_rows[-1]:
            continue
        total = sess_rows[-1]["sess_steps"]
        cells = ""
        first = None
        for sname, lo, hi in STAGES:
            sel = [r for r in sess_rows if lo * total <= r["sess_steps"] <= hi * total and rk in r]
            if not sel:
                sel = [sess_rows[-1]]
            rew = sum(r[rk] for r in sel) / len(sel)
            suc = sum(ok(r) for r in sel) / len(sel)
            if first is None:
                first = rew
            cells += "<td>%.1f</td><td>%.0f %%</td>" % (rew, 100 * suc)
        sess_stage_rows += ("<tr><th>%s<br><small>%s</small></th>%s"
                            "<td>%+.1f</td></tr>" % (role, what, cells, rew - first))

    milestones = "".join(
        "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
        % (e.get("t", "")[11:], e.get("event", ""), e.get("run", "-"),
           e.get("level", "-"), e.get("best_exam", "-"))
        for e in log if e.get("event") in ("stage", "milestone", "crash", "final",
                                          "final_skipped", "stopped"))

    html = f"""<!doctype html><meta charset="utf-8"><title>Spar session report</title>
<style>body{{font:15px system-ui;max-width:980px;margin:24px auto;padding:0 16px;line-height:1.45}}
img{{width:100%;border:1px solid #eee;margin-bottom:10px}}
table{{border-collapse:collapse;margin:12px 0}} td,th{{padding:4px 10px;border-bottom:1px solid #ccc;text-align:right}}
td:first-child,th:first-child{{text-align:left}}small{{color:#777;font-weight:normal}}
h2{{margin-top:26px}}</style>
<h1>Unattended spar session &mdash; {name}</h1>
<p>Generated {time.strftime('%Y-%m-%d %H:%M:%S')}. {len(queue)} stages, both athletes alternating
in the mixed arena: each stage trains one athlete against the other's latest frozen brain
(adversarial curriculum), so the policy keeps meeting a moving target.</p>
<h2>Stages</h2>
<table><tr><th>run</th><th>athlete</th><th>level</th><th>steps</th><th>best exam</th><th>graduated</th></tr>
{''.join(body)}</table>
<h2>Session skill stages (Beginner = first 10 %, Intermediate = 45-55 %, Advanced = final 10 % of session steps)</h2>
<table><tr><th>role</th>{''.join('<th>%s<br>reward</th><th>%s<br>success</th>' % (n, n) for n, _, _ in STAGES)}<th>gain</th></tr>
{sess_stage_rows}</table>
<h2>Per-run skill stages (Beginner = first 10 %, Intermediate = 45-55 %, Advanced = final 10 %)</h2>
<table><tr><th>run / role</th>{''.join('<th>%s<br>reward</th><th>%s<br>success</th>' % (n, n) for n, _, _ in STAGES)}<th>gain</th></tr>
{stage_table}</table>
<h2>Loss curves and reward curves</h2>
{''.join(f'<img src="data:image/png;base64,{c}">' for c in charts)}
<h2>Milestones</h2>
<table><tr><th>time</th><th>event</th><th>run</th><th>level</th><th>best exam</th></tr>
{milestones}</table>
"""
    out = RUNS / (name + "_report.html")
    out.write_text(html, encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
