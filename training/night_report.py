"""The night's training report: one HTML page, plain-language, with interactive SVG charts (no external files).

    .venv/Scripts/python.exe night_report.py [night1030_queue] [DOCS/20261030/night_training_report.html]

Reads runs/<queue>.json (the stages, with each run's train.py exam and match-exam gate), runs/<queue>.jsonl (the
events), runs/<queue>_results.json (every match exam and the finals), runs/<queue>_baseline.json (the shipped brains
before the night), and each run's telemetry.jsonl + runs/<run>.log (the train.py exams).  Charts follow the dataviz
method: one axis per chart, categorical colours in a fixed order (Grandpa blue, Kim orange, Matt aqua), a legend plus
direct labels, hover tooltips, a table view under every chart.
"""
import html, json, re, sys, time
from pathlib import Path

HERE = Path(__file__).parent
RUNS = HERE / "runs"
COLORS = {"grandpa": "#2a78d6", "kim": "#eb6834", "matt": "#1baf7a"}          # categorical slots 1-3 (validated)
SLOTS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
PRETTY = {"grandpa": "Grandpa", "kim": "Kim", "matt": "Matt"}


def read_jsonl(p):
    out = []
    if p.exists():
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def train_exams(run):
    """train.py's own exams from the run's console log, each with the steps it happened at."""
    p = RUNS / f"{run}.log"
    out, steps = [], 0
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = re.match(r"it\s+(\d+)\s+([\d.]+)M\s+([\d.]+) min", line)
        if m:
            steps = float(m.group(2))
        if line.startswith("exam "):
            try:
                d = eval(line.split(":", 1)[1].split("->")[0].strip(), {"__builtins__": {}})   # a python dict repr
            except Exception:
                continue
            out.append({"steps_m": steps, "passed": "PASS" in line, **d})
    return out


def pct(x):
    return "-" if x is None else f"{100 * x:.0f} %"


def fmt(x, nd=1):
    return "-" if x is None else f"{x:.{nd}f}"


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "night1030_queue"
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else HERE.parent / "DOCS" / time.strftime("%Y%m%d") / "night_training_report.html"
    queue = json.loads((RUNS / f"{name}.json").read_text())
    events = read_jsonl(RUNS / f"{name}.jsonl")
    results_p = RUNS / f"{name}_results.json"   # night_driver.py: <queue stem>_results.json
    results = json.loads(results_p.read_text()) if results_p.exists() else {"exams": [], "finals": []}
    base_p = RUNS / f"{name.replace('_queue', '')}_baseline.json"
    baseline = json.loads(base_p.read_text()) if base_p.exists() else {}

    stages = [s for s in queue if s.get("run")]
    runs = {}
    for s in stages:
        tele = read_jsonl(RUNS / s["run"] / "telemetry.jsonl")
        runs[s["run"]] = {"stage": s, "tele": tele, "exams": train_exams(s["run"]),
                          "meta": json.loads((RUNS / s["run"] / "meta.json").read_text()) if (RUNS / s["run"] / "meta.json").exists() else {}}

    # ---------------------------------------------------------------- headline numbers
    t_start = next((e["t"] for e in events if e.get("event") == "start"), None)
    t_end = next((e["t"] for e in events if e.get("event") == "end"), None)
    total_steps = sum(r["meta"].get("steps", 0) for r in runs.values())
    total_min = sum(r["meta"].get("minutes", 0) for r in runs.values())
    gates = [e for e in events if e.get("event") == "match_exam"]
    promoted = [f for f in results.get("finals", []) if f.get("promoted")]
    graduated = [s for s in stages if s.get("graduated")]
    kpis = [("Stages trained", str(len(stages)), f"of {len(queue)} planned"),
            ("GPU hours", f"{total_min / 60:.1f}", "of training inside the 10 h window"),
            ("Simulated steps", f"{total_steps / 1e9:.2f} B", "physics decisions the brains learned from"),
            ("Levels graduated", str(len(graduated)), "train.py's own exam passed"),
            ("Match gates passed", f"{sum(1 for g in gates if g.get('passed'))} / {len(gates)}", "40 jittered full matches each"),
            ("Brains promoted", str(len(promoted)), "replaced a shipped final")]

    # ---------------------------------------------------------------- per-athlete chart data
    def series_for(char, key):
        out, i = [], 0
        for run, r in runs.items():
            if r["stage"]["char"] != char or not r["tele"] or key not in r["tele"][-1]:
                continue
            pts = [[round(t["min"], 2), t.get(key)] for t in r["tele"] if t.get(key) is not None]
            out.append({"name": run, "color": SLOTS[i % len(SLOTS)], "points": pts})
            i += 1
        return out

    athletes = []
    for char in ("grandpa", "kim", "matt"):
        rs = [r for r in runs.values() if r["stage"]["char"] == char]
        if not rs:
            continue
        charts = []
        for key, title, ylabel, caption in (
            ("strike_return", "Strike reward", "reward per episode",
             "What the striker earns per turn: closing on the face, a fast palm, staying up afterwards. Rising means the slap is getting better."),
            ("strike_hit", "Clean-hit rate", "share of turns",
             "Share of training turns where the hand reached the head at 8 m/s or more and the striker was still standing 1.5 s later."),
            ("strike_fall", "Striker self-fall rate", "share of turns",
             "Share of turns the STRIKER ended on the floor or the podium (the open issue for Grandpa vs Kim: 25 % before tonight). Lower is better."),
            ("absorb_return", "Absorb reward", "reward per episode",
             "What the defender earns: taking the slap, keeping the feet planted, bringing the centre of mass back."),
            ("absorb_ok", "Slaps absorbed", "share of turns",
             "Share of turns the defender stayed up 5 s after the slap with the body back over the feet."),
            ("guard_return", "Guard reward", "reward per episode",
             "Standing in the foot box, facing the target, under pushes (levels L0-L2)."),
            ("policy_loss", "Policy loss", "loss",
             "PPO's actor objective. Small and noisy is normal; a drift to large negative values means the policy is changing fast."),
            ("value_loss", "Value loss", "loss",
             "How well the critic predicts the reward to come. It jumps when the opponent or the rules change, then settles as the brain adapts."),
            ("entropy", "Policy entropy", "nats",
             "How random the actions still are. Falling entropy = the brain is committing to a technique; it must not collapse to zero."),
        ):
            s = series_for(char, key)
            if s:
                charts.append({"id": f"{char}_{key}", "title": title, "ylabel": ylabel, "caption": caption, "series": s,
                               "percent": key.endswith(("_hit", "_fall", "_ok"))})
        exams = []
        for run, r in runs.items():
            if r["stage"]["char"] == char:
                for e in r["exams"]:
                    exams.append({"run": run, **e})
        athletes.append({"char": char, "charts": charts, "exams": exams, "stages": [r["stage"] for r in rs]})

    # ---------------------------------------------------------------- the match gate: before vs after
    gate_rows = []
    for ex in results.get("exams", []):
        side = ex["sides"].get(ex["char"], {})
        gate_rows.append({"run": ex["run"], "char": ex["char"], "vs": ex["vs"], "passed": ex["passed"], "why": ex["why"],
                          "land": side.get("land_rate"), "self_fall": side.get("self_fall"), "ko": side.get("ko_per_landed"),
                          "speed": side.get("speed"), "wins": side.get("wins"), "matches": ex["matches"], "blowups": ex["blowups"],
                          "score": ex["score"]})
    base = baseline.get("gk", {})
    dumbbells = []
    for char, vs in (("grandpa", "kim"), ("kim", "grandpa")):
        b = base.get("sides", {}).get(char)
        after = [g for g in gate_rows if g["char"] == char and g["vs"] == vs]
        best = max(after, key=lambda g: g["score"]) if after else None
        if b or best:
            dumbbells.append({"label": f"{PRETTY[char]} striking {PRETTY[vs]}", "char": char,
                              "before": {"land": b and b["land_rate"], "self_fall": b and b["self_fall"], "ko": b and b["ko_per_landed"]},
                              "after": {"land": best and best["land"], "self_fall": best and best["self_fall"], "ko": best and best["ko"]},
                              "after_run": best and best["run"]})

    # ---------------------------------------------------------------- the story, in words
    story = []
    for s in stages:
        who = PRETTY.get(s["char"], s["char"])
        vs = f" against {PRETTY.get(s['vs'], s['vs'])}" if s.get("vs") else ""
        g = s.get("graduated")
        me = s.get("match_exam")
        line = f"<b>{who} {s['level']}{vs}</b> ({s['run']}, {fmt(s.get('train_minutes'))} min, {fmt((s.get('steps') or 0) / 1e6, 0)} M steps): "
        if g:
            line += "train.py's exam passed (" + ", ".join(f"{k} {pct(v) if isinstance(v, float) and v <= 1 else v}" for k, v in g.items() if k != "score") + "). "
        else:
            line += f"train.py's exam not passed (best score {fmt(s.get('best_exam'), 2)}). "
        if me:
            sd = me.get("side") or {}
            line += (f"Match gate <b>{'PASSED' if me['passed'] else 'failed'}</b>: lands {pct(sd.get('land_rate'))}, self-falls {pct(sd.get('self_fall'))}, "
                     f"KO per landed strike {pct(sd.get('ko_per_landed'))}, hand {fmt(sd.get('speed'))} m/s ({me['why']}).")
        if s.get("skipped"):
            line += f" Skipped: {s['skipped']}."
        story.append(line)
    for s in queue:
        if s.get("skipped") and not s.get("run"):
            story.append(f"<b>{PRETTY.get(s['char'], s['char'])} {s['level']}</b>: skipped, {s['skipped']}.")

    data = {"athletes": athletes, "gates": gate_rows, "dumbbells": dumbbells, "events": events, "baseline": base,
            "finals": results.get("finals", [])}
    esc = lambda x: html.escape(str(x))

    kpi_html = "".join(f'<div class="tile"><div class="k">{esc(k)}</div><div class="v">{esc(v)}</div><div class="s">{esc(s)}</div></div>' for k, v, s in kpis)
    story_html = "".join(f"<li>{s}</li>" for s in story)
    stage_rows = "".join(
        f"<tr><td>{esc(s['run'])}</td><td>{esc(PRETTY.get(s['char'], s['char']))}</td><td>{esc(s['level'])}{' vs ' + esc(PRETTY.get(s['vs'], s['vs'])) if s.get('vs') else ''}</td>"
        f"<td>{fmt(s.get('train_minutes'))}</td><td>{fmt((s.get('steps') or 0) / 1e6, 0)}</td><td>{fmt(s.get('best_exam'), 3)}</td>"
        f"<td>{'yes' if s.get('graduated') else 'no'}</td><td>{(('pass' if s['match_exam']['passed'] else 'fail: ' + esc(s['match_exam']['why'])) if s.get('match_exam') else '-')}</td>"
        f"<td><a href='../../training/runs/{esc(s['run'])}/report.html'>run report</a></td></tr>" for s in stages)
    gate_table = "".join(
        f"<tr><td>{esc(g['run'])}</td><td>{esc(PRETTY[g['char']])} vs {esc(PRETTY[g['vs']])}</td><td>{g['matches']}</td><td>{g['blowups']}</td>"
        f"<td>{pct(g['land'])}</td><td>{pct(g['self_fall'])}</td><td>{pct(g['ko'])}</td><td>{fmt(g['speed'])}</td><td>{g['wins']}</td>"
        f"<td>{'PASS' if g['passed'] else esc(g['why'])}</td></tr>" for g in gate_rows)
    if base:
        for char in ("grandpa", "kim"):
            b = base["sides"].get(char, {})
            gate_table = (f"<tr class='base'><td>before tonight (shipped)</td><td>{PRETTY[char]} vs {PRETTY['kim' if char == 'grandpa' else 'grandpa']}</td>"
                          f"<td>{base['matches']}</td><td>{base['blowups']}</td><td>{pct(b.get('land_rate'))}</td><td>{pct(b.get('self_fall'))}</td>"
                          f"<td>{pct(b.get('ko_per_landed'))}</td><td>{fmt(b.get('speed'))}</td><td>{b.get('wins')}</td><td>baseline</td></tr>") + gate_table
    ev_rows = "".join(f"<tr><td>{esc(e.get('t', '')[11:])}</td><td>{esc(e.get('event'))}</td><td>{esc(e.get('run') or e.get('char') or '')}</td>"
                      f"<td>{esc({k: v for k, v in e.items() if k not in ('t', 'event', 'run', 'char', 'side', 'resume', 'opponent')})[:160]}</td></tr>" for e in events)
    finals_html = "".join(f"<li>{esc(PRETTY[f['char']])} vs {esc(PRETTY[f['vs']])}: {'<b>promoted</b> to models/brains/' + esc(f['char']) + '_final_vs_' + esc(f['vs']) + ' from ' + esc(f['best_run']) if f['promoted'] else 'kept the shipped brain (best candidate ' + esc(f['best_run']) + ' scored ' + fmt(f['score'], 2) + ')'}</li>"
                          for f in results.get("finals", [])) or "<li>no finals written yet (the queue is still running)</li>"

    athlete_html = ""
    for a in athletes:
        who = PRETTY[a["char"]]
        charts = "".join(f'<figure><figcaption><b>{esc(c["title"])}</b> &middot; {esc(c["caption"])}</figcaption><div class="chart" id="{c["id"]}"></div>'
                         f'<details><summary>table view</summary><div class="tbl" id="{c["id"]}_tbl"></div></details></figure>' for c in a["charts"])
        ex_rows = "".join(f"<tr><td>{esc(e['run'])}</td><td>{fmt(e['steps_m'])}</td>" + "".join(f"<td>{esc(k)} {pct(v) if isinstance(v, float) and v <= 1 else fmt(v, 2)}</td>" for k, v in e.items() if k not in ('run', 'steps_m', 'passed')) + f"<td>{'PASS' if e['passed'] else 'not yet'}</td></tr>" for e in a["exams"])
        athlete_html += (f'<section><h2 style="border-left:6px solid {COLORS[a["char"]]};padding-left:10px">{who}</h2>'
                         f'<p class="muted">{len(a["stages"])} stage(s): ' + ", ".join(esc(s["run"]) for s in a["stages"]) + "</p>"
                         f'<div class="grid">{charts}</div>'
                         + (f"<details><summary>train.py's own exams during the night ({len(a['exams'])})</summary><table><tr><th>run</th><th>M steps</th><th colspan=5>result</th><th></th></tr>{ex_rows}</table></details>" if a["exams"] else ""))
        athlete_html += "</section>"

    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Night training report {esc(name)}</title>
<style>
:root{{color-scheme:light;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--muted:#8a8984;--grid:#e6e5e1;--line:#d9d8d3}}
@media (prefers-color-scheme: dark){{:root{{color-scheme:dark;--surface:#1a1a19;--ink:#f0efec;--ink2:#c3c2b7;--muted:#8f8e88;--grid:#2d2d2b;--line:#3a3a37}}}}
body{{font:15px/1.5 system-ui,sans-serif;background:var(--surface);color:var(--ink);max-width:1100px;margin:24px auto;padding:0 18px}}
h1{{font-size:26px;margin:0 0 4px}} h2{{margin:36px 0 8px}} .muted{{color:var(--ink2)}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:18px 0}}
.tile{{border:1px solid var(--line);border-radius:8px;padding:10px 12px}} .tile .k{{font-size:12px;color:var(--ink2)}}
.tile .v{{font-size:30px;font-weight:600;line-height:1.2}} .tile .s{{font-size:12px;color:var(--muted)}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(480px,1fr));gap:18px}}
figure{{margin:0;border:1px solid var(--line);border-radius:8px;padding:10px}} figcaption{{font-size:13px;color:var(--ink2);margin-bottom:6px}}
.chart svg{{width:100%;height:auto;display:block}} .chart text{{font-size:11px;fill:var(--ink2)}} .chart .grid line{{stroke:var(--grid)}}
.chart .axis line,.chart .axis path{{stroke:var(--line)}} .chart .lbl{{font-size:11px;fill:var(--ink);font-weight:600}}
.tip{{position:fixed;pointer-events:none;background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:6px 8px;font-size:12px;box-shadow:0 2px 8px rgba(0,0,0,.15);display:none;z-index:9}}
table{{border-collapse:collapse;margin:8px 0;font-size:13px}} td,th{{padding:4px 8px;border-bottom:1px solid var(--line);text-align:right}} td:first-child,th:first-child{{text-align:left}}
tr.base td{{background:rgba(42,120,214,.08)}} details{{margin:6px 0}} summary{{cursor:pointer;color:var(--ink2);font-size:13px}}
.legend span{{display:inline-block;margin-right:12px;font-size:12px}} .legend i{{display:inline-block;width:14px;height:3px;vertical-align:middle;margin-right:4px}}
.db svg{{width:100%;height:auto}} ul.story li{{margin:6px 0}}
</style></head><body>
<h1>Night training report &mdash; {esc(name)}</h1>
<p class="muted">Generated {time.strftime('%Y-%m-%d %H:%M')} &middot; queue started {esc(t_start)} {('&middot; ended ' + esc(t_end)) if t_end else '&middot; still running'} &middot; one RTX 5070 Ti laptop GPU, one stage at a time, match gates on the CPU alongside.</p>
<div class="kpis">{kpi_html}</div>

<h2>What happened, stage by stage</h2>
<ul class="story">{story_html}</ul>

<h2>The match gate: before and after</h2>
<p class="muted">The referee's view. Each candidate brain plays 40 full jittered matches in <code>match.py</code> (the engine the game mirrors, eye-contact servo on) against the other athlete's current brain. A gate passes when no match blows up, the athlete lands at least 85 % of its strikes, self-falls on at most 6 % of them and its KO rate per landed strike is inside the L4 band. The dumbbells show the shipped brain before tonight (hollow) against the best candidate after (filled).</p>
<div class="db" id="dumbbells"></div>
<details open><summary>every gate exam</summary><table><tr><th>run</th><th>pairing</th><th>matches</th><th>blow-ups</th><th>lands</th><th>self-falls</th><th>KO / landed</th><th>hand m/s</th><th>wins</th><th>verdict</th></tr>{gate_table}</table></details>
<h3>Final weights</h3><ul>{finals_html}</ul>

{athlete_html}

<h2>All stages</h2>
<table><tr><th>run</th><th>athlete</th><th>level</th><th>min</th><th>M steps</th><th>best exam</th><th>graduated</th><th>match gate</th><th></th></tr>{stage_rows}</table>
<h2>Event log</h2>
<details><summary>{len(events)} events</summary><table><tr><th>time</th><th>event</th><th>run / athlete</th><th>details</th></tr>{ev_rows}</table></details>
<div class="tip" id="tip"></div>
<script>
const DATA = {json.dumps(data)};
const tip = document.getElementById('tip');
function lineChart(el, chart) {{
  const W = 560, H = 260, m = {{l: 52, r: 110, t: 12, b: 34}};
  const xs = chart.series.flatMap(s => s.points.map(p => p[0])), ys = chart.series.flatMap(s => s.points.map(p => p[1]));
  if (!xs.length) return;
  let x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(0, ...ys), y1 = Math.max(...ys);
  if (chart.percent) {{ y0 = 0; y1 = 1; }}
  if (y1 === y0) y1 = y0 + 1;
  const X = v => m.l + (v - x0) / (x1 - x0 || 1) * (W - m.l - m.r), Y = v => H - m.b - (v - y0) / (y1 - y0) * (H - m.t - m.b);
  const fy = v => chart.percent ? Math.round(v * 100) + ' %' : (Math.abs(v) >= 100 ? v.toFixed(0) : Math.abs(v) >= 1 ? v.toFixed(2) : v === 0 ? '0' : v.toPrecision(2));
  let g = '<g class="grid">';
  const ticks = 4;
  for (let i = 0; i <= ticks; i++) {{ const v = y0 + (y1 - y0) * i / ticks; g += `<line x1="${{m.l}}" x2="${{W - m.r}}" y1="${{Y(v)}}" y2="${{Y(v)}}"/><text x="${{m.l - 6}}" y="${{Y(v) + 4}}" text-anchor="end">${{fy(v)}}</text>`; }}
  for (let i = 0; i <= 4; i++) {{ const v = x0 + (x1 - x0) * i / 4; g += `<text x="${{X(v)}}" y="${{H - m.b + 16}}" text-anchor="middle">${{v.toFixed(0)}}</text>`; }}
  g += `<text x="${{(m.l + W - m.r) / 2}}" y="${{H - 4}}" text-anchor="middle">minutes of training in the run</text></g>`;
  g += `<g class="axis"><line x1="${{m.l}}" x2="${{W - m.r}}" y1="${{H - m.b}}" y2="${{H - m.b}}"/></g>`;
  const short = n => n.replace(/^.*_n(\d+)_(\d{{2}})(\d{{2}})$/, 'round $1 ($2:$3)');
  const ends = chart.series.map(s => {{ const l = s.points[s.points.length - 1]; return {{ x: W - m.r, y: Y(l[1]) }}; }});   // labels live in the right margin, never over another run's line
  const order = ends.map((e, i) => i).sort((a, b) => ends[a].y - ends[b].y);   // direct labels pushed apart by 13 px
  for (let k = 1; k < order.length; k++) {{ const a = ends[order[k - 1]], b = ends[order[k]]; if (b.y - a.y < 13) b.y = a.y + 13; }}
  chart.series.forEach((s, i) => {{
    const d = s.points.map((p, j) => (j ? 'L' : 'M') + X(p[0]).toFixed(1) + ' ' + Y(p[1]).toFixed(1)).join(' ');
    g += `<path d="${{d}}" fill="none" stroke="${{s.color}}" stroke-width="2" stroke-linejoin="round"/>`;
    g += `<text class="lbl" x="${{ends[i].x + 6}}" y="${{ends[i].y + 4}}" fill="${{s.color}}">${{short(s.name)}}</text>`;
  }});
  g += `<line id="${{chart.id}}_x" x1="0" x2="0" y1="${{m.t}}" y2="${{H - m.b}}" stroke="var(--ink2)" stroke-dasharray="3 3" style="display:none"/>`;
  g += `<rect x="${{m.l}}" y="${{m.t}}" width="${{W - m.l - m.r}}" height="${{H - m.t - m.b}}" fill="transparent" id="${{chart.id}}_hit"/>`;
  el.innerHTML = `<svg viewBox="0 0 ${{W}} ${{H}}">${{g}}</svg>` +
    '<div class="legend">' + chart.series.map(s => `<span><i style="background:${{s.color}}"></i>${{s.name}}</span>`).join('') + '</div>';
  const hit = document.getElementById(chart.id + '_hit'), cross = document.getElementById(chart.id + '_x'), svg = el.querySelector('svg');
  hit.addEventListener('mousemove', ev => {{
    const r = svg.getBoundingClientRect(), vx = (ev.clientX - r.left) / r.width * W, mins = x0 + (vx - m.l) / (W - m.l - m.r) * (x1 - x0);
    cross.setAttribute('x1', vx); cross.setAttribute('x2', vx); cross.style.display = '';
    const rows = chart.series.map(s => {{ let best = s.points[0]; for (const p of s.points) if (Math.abs(p[0] - mins) < Math.abs(best[0] - mins)) best = p;
      return `<div><i style="display:inline-block;width:10px;height:10px;background:${{s.color}};margin-right:5px"></i>${{s.name}}: <b>${{fy(best[1])}}</b> at ${{best[0].toFixed(1)}} min</div>`; }});
    tip.innerHTML = `<div><b>${{chart.title}}</b></div>` + rows.join(''); tip.style.display = 'block';
    tip.style.left = (ev.clientX + 14) + 'px'; tip.style.top = (ev.clientY + 14) + 'px';
  }});
  hit.addEventListener('mouseleave', () => {{ tip.style.display = 'none'; cross.style.display = 'none'; }});
  const tbl = document.getElementById(chart.id + '_tbl');
  if (tbl) {{
    const step = Math.max(1, Math.floor(Math.max(...chart.series.map(s => s.points.length)) / 12));
    tbl.innerHTML = '<table><tr><th>run</th><th>minute</th><th>' + chart.ylabel + '</th></tr>' + chart.series.map(s => s.points.filter((p, j) => j % step === 0 || j === s.points.length - 1)
      .map(p => `<tr><td>${{s.name}}</td><td>${{p[0].toFixed(1)}}</td><td>${{fy(p[1])}}</td></tr>`).join('')).join('') + '</table>';
  }}
}}
DATA.athletes.forEach(a => a.charts.forEach(c => lineChart(document.getElementById(c.id), c)));
(function dumbbells() {{
  const el = document.getElementById('dumbbells'); if (!DATA.dumbbells.length) {{ el.innerHTML = '<p class="muted">No match-gate results yet.</p>'; return; }}
  const metrics = [['land', 'strikes landed', true], ['self_fall', 'striker self-falls', false], ['ko', 'KO per landed strike', null]];
  const W = 1000, rowH = 34, H = 30 + DATA.dumbbells.length * metrics.length * rowH + 10, L = 300, R = 60;
  const X = v => L + v * (W - L - R);
  let g = '';
  for (let i = 0; i <= 10; i += 2) g += `<line x1="${{X(i / 10)}}" x2="${{X(i / 10)}}" y1="20" y2="${{H - 10}}" stroke="var(--grid)"/><text x="${{X(i / 10)}}" y="14" text-anchor="middle" font-size="11" fill="var(--ink2)">${{i * 10}} %</text>`;
  let y = 30;
  DATA.dumbbells.forEach(d => {{
    const c = d.char === 'grandpa' ? '#2a78d6' : d.char === 'kim' ? '#eb6834' : '#1baf7a';
    metrics.forEach(([k, label, higherBetter]) => {{
      const b = d.before[k], a = d.after[k];
      g += `<text x="${{L - 10}}" y="${{y + 5}}" text-anchor="end" font-size="12" fill="var(--ink)">${{d.label}} &middot; ${{label}}</text>`;
      if (b != null && a != null) g += `<line x1="${{X(b)}}" x2="${{X(a)}}" y1="${{y}}" y2="${{y}}" stroke="${{c}}" stroke-width="3" stroke-linecap="round"/>`;
      if (b != null) g += `<circle cx="${{X(b)}}" cy="${{y}}" r="6" fill="var(--surface)" stroke="${{c}}" stroke-width="2"><title>before: ${{Math.round(b * 100)}} %</title></circle>`;
      if (a != null) g += `<circle cx="${{X(a)}}" cy="${{y}}" r="6" fill="${{c}}" stroke="var(--surface)" stroke-width="2"><title>after: ${{Math.round(a * 100)}} %</title></circle>`;
      const txt = (b != null ? Math.round(b * 100) + ' %' : '-') + ' \\u2192 ' + (a != null ? Math.round(a * 100) + ' %' : '-');
      g += `<text x="${{W - R + 6}}" y="${{y + 4}}" font-size="11" fill="var(--ink2)">${{txt}}</text>`;
      y += rowH;
    }});
  }});
  el.innerHTML = `<svg viewBox="0 0 ${{W + 70}} ${{H}}">${{g}}</svg><p class="muted" style="font-size:12px">Hollow = before tonight (the shipped brains), filled = best candidate of the night. Landed and KO: higher is better; self-falls: lower is better.</p>`;
}})();
</script></body></html>"""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    print("report:", out)


if __name__ == "__main__":
    main()
