"""Training runs grid: training/runs/runs.html, one row per real training run under training/runs/, newest first.

    python benchmark/runs.py                                   rebuild training/runs/runs.html from the run folders
    python benchmark/runs.py --record <run-id> --ended "<how>" [--launcher <script>]
                                                               note this machine and how the run ended, then rebuild
    --runs-dir <dir>                                           another runs/ folder (a night driver's own farm)

The grid is rebuilt from what training/train.py leaves in each run folder (config_snapshot.json: start, command line,
args, commit; telemetry.jsonl: steps and minutes per iteration, restarting from 0 in every process that trained the
run; meta.json and crash.log / recovered.log: how it ended), so a run started by hand, or one whose launcher was
killed, still gets a row. What the folder cannot tell (which machine, how the run ended in the launcher's words) comes
from runs/<run>/run_meta.json, which --record writes; training/train.py and training/overnight.py call it when a run
ends, crash or not. Without it the Machine cell reads "-".

Steps are the trainer's own counter (steps_per_env x agents per iteration; agents = worlds, except arena self-play:
2 per world), summed over every session of the run. Speed is the run's own: agent steps per second of its last
session, measured between that session's first and last telemetry line (startup and kernel compile excluded), so a
resumed run is not credited with the steps of its earlier sessions. Benchmark is the newest benchmark/history.json
result on the same machine from before the run started (ms per step, env steps/s), for comparison.
Stdlib only; core.py next to this file supplies the machine description.
"""

from __future__ import annotations

import argparse
import datetime
import html
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
RUNS = ROOT / "training" / "runs"
OUT_NAME = "runs.html"
BENCH = HERE / "history.json"
META = "run_meta.json"
TITLE = "PoSlapUnreal"
SKIP = {"_benchmark"}  # the Benchmark's own throwaway run dir (benchmark/workload.py RUN)


# ---------------------------------------------------------------- collect


def _json(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _sessions(tele: Path) -> list[list[dict]]:
    """telemetry.jsonl split into sessions: each process that trained the run counts "min" (and "steps") from 0."""
    sessions: list[list[dict]] = []
    last = None
    try:
        lines = tele.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return sessions
    for line in lines:
        try:
            r = json.loads(line)
            m, s = float(r["min"]), float(r["steps"])
        except (ValueError, KeyError, TypeError):
            continue
        if last is None or m < last:
            sessions.append([])
        sessions[-1].append({"min": m, "steps": s})
        last = m
    return sessions


def _speed(sessions: list[list[dict]]) -> tuple[int | None, float | None]:
    """(steps over every session, agent steps/s of the last session)."""
    if not sessions:
        return None, None
    total = int(sum(s[-1]["steps"] for s in sessions))
    cur, rate = sessions[-1], None
    if len(cur) >= 2 and cur[-1]["min"] > cur[0]["min"]:
        rate = (cur[-1]["steps"] - cur[0]["steps"]) / ((cur[-1]["min"] - cur[0]["min"]) * 60)
    elif cur[-1]["min"] > 0:
        rate = cur[-1]["steps"] / (cur[-1]["min"] * 60)
    return total, rate


def _bench_for(host: str | None, before: float) -> dict | None:
    runs = [r for r in _json(BENCH).get("runs", []) if r.get("ok") and r.get("result")]
    if host:
        runs = [r for r in runs if r.get("machine", {}).get("hostname") == host]
    if not runs:
        return None
    stamp = lambda r: datetime.datetime.fromisoformat(r["when"]).timestamp()
    earlier = [r for r in runs if stamp(r) <= before]
    return (earlier or runs)[-1]


def _created(p: Path) -> float:
    st = p.stat()
    born = getattr(st, "st_birthtime", None)  # Windows (3.12+) / macOS: the folder's creation, i.e. the first session
    if born is None:
        born = st.st_ctime if os.name == "nt" else st.st_mtime
    return float(born)


def _ended(d: Path, rmeta: dict, tmeta: dict, end: float) -> str:
    """The launcher's words if it recorded them, else what the folder shows."""
    if rmeta.get("ended"):
        return rmeta["ended"]
    if (d / "crash.log").exists():
        lines = (d / "crash.log").read_text(encoding="utf-8", errors="replace").strip().splitlines()
        last = re.sub(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d ", "", lines[-1].strip()) if lines else ""
        return f"crashed: {last[:120]}" if last else "crashed"
    if tmeta:
        if tmeta.get("nan"):
            return "stopped: NaN in training"
        if tmeta.get("exit_code", 0) != 0:
            return f"crashed: exit code {tmeta['exit_code']}"
        return "finished (resumed after a crash)" if (d / "recovered.log").exists() else "finished"
    return "still running" if time.time() - end < 600 else "still running or killed"


def _config(args: dict, snap: dict) -> str:
    parts = [f"{args.get('char', '?')} {args.get('level', '?')}"]
    if args.get("physics") is not None:
        parts.append(f"physics {args['physics']}")
    if args.get("minutes") is not None:
        parts.append(f"{float(args['minutes']):g} min")
    if args.get("opponent"):
        parts.append("vs " + str(args["opponent"]).split(":", 1)[0])
    if args.get("field"):
        parts.append("field " + "+".join(x.split(":", 1)[0] for x in str(args["field"]).split(",")))
    for key, label in (("roles", "roles"), ("critic_warmup", "critic warmup"), ("obs_project", "obs project")):
        if args.get(key):
            parts.append(f"{label} {args[key]}")
    if args.get("keep_going"):
        parts.append("keep going")
    return " · ".join(parts)


def _warm(resume: str | None) -> list[str]:
    if not resume:
        return []
    p = Path(str(resume).replace("\\", "/"))
    return [f"{p.parent.name}/{p.name}" if p.parent.name else p.name]


def collect(runs: Path = RUNS) -> list[dict]:
    rows = []
    if not runs.is_dir():
        return rows
    for d in runs.iterdir():
        snap_p, tele_p = d / "config_snapshot.json", d / "telemetry.jsonl"
        if not d.is_dir() or d.name in SKIP or not (snap_p.exists() or tele_p.exists()):
            continue
        snap, rmeta, tmeta = _json(snap_p), _json(d / META), _json(d / "meta.json")
        args = snap.get("args") or {}
        sessions = _sessions(tele_p)
        start = _created(d)
        try:  # the snapshot is rewritten by every session: a lower bound only if the folder was copied since
            start = min(start, datetime.datetime.strptime(snap["started"], "%Y-%m-%d %H:%M:%S").timestamp())
        except (KeyError, TypeError, ValueError):
            pass
        stamps = [p.stat().st_mtime for p in (tele_p, d / "meta.json", d / "latest.pt", snap_p) if p.exists()]
        end = max(stamps) if stamps else start
        steps, rate = _speed(sessions)
        sha = snap.get("git_sha") or ""
        # the commit it trained from (the snapshot, taken at start) over the one checked out when it ended
        commit = (sha[:8] + (" dirty" if snap.get("git_dirty") else "")) if sha else rmeta.get("commit")
        rows.append(
            dict(
                run=d.name,
                start=start,
                wall=max(0.0, end - start),
                ended=_ended(d, rmeta, tmeta, end),
                sessions=len(sessions),
                steps=steps,
                rate=rate,
                bench=_bench_for(rmeta.get("host"), start),
                config=_config(args, snap) if args else None,
                envs=args.get("envs") or snap.get("worlds") or "-",
                warm=_warm(args.get("resume")),
                commit=commit,
                host=rmeta.get("host"),
                gpu=rmeta.get("gpu"),
                device=rmeta.get("device"),
                launcher=rmeta.get("launcher"),
            )
        )
    rows.sort(key=lambda r: r["start"], reverse=True)
    return rows


# ---------------------------------------------------------------- render


def _dur(s: float) -> str:
    h, m = divmod(int(s) // 60, 60)
    return f"{h} h {m:02d} m" if h else f"{m} m"


def _num(n: float | None, digits: int = 0) -> str:
    return "-" if n is None else f"{n:,.{digits}f}"


def render(rows: list[dict]) -> str:
    e = html.escape
    body = []
    for r in rows:
        b = r["bench"]
        if b:
            notes = [f"{b['result']['env_steps_per_s']} env steps/s", e(b["when"][:10])]
            if datetime.datetime.fromisoformat(b["when"]).timestamp() > r["start"]:
                notes.append("taken after the run")
            if not r["host"]:
                notes.append("machine unknown")
            bench = f"{b['result']['step_time_ms']} ms<small>{' · '.join(notes)}</small>"
        else:
            bench = "-<small>no benchmark on this machine</small>"
        ended = e(r["ended"])
        cls = "bad" if re.search(r"crash|died|killed|NaN|error|exception", r["ended"], re.I) else ""
        resumed = f"<small>{r['sessions']} sessions</small>" if r["sessions"] > 1 else ""
        launcher = f"<small>{e(r['launcher'])}</small>" if r["launcher"] else ""
        body.append(
            "<tr>"
            f"<td><b>{e(r['run'])}</b>{resumed}</td>"
            f"<td>{datetime.datetime.fromtimestamp(r['start']).strftime('%Y-%m-%d %H:%M')}</td>"
            f'<td class="n">{_dur(r["wall"])}</td>'
            f'<td class="{cls}">{ended}{launcher}</td>'
            f'<td class="n">{_num(r["steps"])}</td>'
            f'<td class="n"><b>{_num(r["rate"])}</b><small>agent steps/s</small></td>'
            f'<td class="n">{bench}</td>'
            f"<td>{e(r['config'] or '-')}</td>"
            f'<td class="n">{e(str(r["envs"]))}</td>'
            f"<td>{e(', '.join(r['warm']) or 'cold')}</td>"
            f'<td class="mono">{e(r["commit"] or "-")}</td>'
            f"<td>{e(r['host'] or '-')}<small>{e(r['gpu'] or '')}{' · ' + e(r['device']) if r['device'] else ''}"
            f"</small></td>"
            "</tr>"
        )
    latest = rows[0] if rows else None
    prev = next((r for r in rows[1:] if r["rate"]), None) if rows else None
    delta = ""
    if latest and latest["rate"] and prev:
        pct = 100 * (latest["rate"] / prev["rate"] - 1)
        delta = f'<span class="{"up" if pct >= 0 else "down"}">{pct:+.0f}% vs {e(prev["run"])}</span>'
    summary = (
        f'<div class="sum"><div><span>Latest run</span><b>{e(latest["run"])}</b></div>'
        f"<div><span>Speed</span><b>{_num(latest['rate'])} steps/s</b>{delta}</div>"
        f"<div><span>Wall-clock</span><b>{_dur(latest['wall'])}</b></div>"
        f"<div><span>Runs listed</span><b>{len(rows)}</b></div></div>"
        if latest
        else "<p>No training runs under training/runs/ yet.</p>"
    )
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{e(TITLE)} training runs</title>
<style>
:root{{--bg:#f5f7f6;--card:#fff;--ink:#18211f;--muted:#5d6b67;--line:#d8e0dd;--accent:#0f6b5c;--bad:#b42318;--up:#1f7a43}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0f1514;--card:#161e1c;--ink:#e2eae7;--muted:#91a19c;--line:#27332f;
--accent:#5cc9b0;--bad:#ff8a7a;--up:#5fcf8a;color-scheme:dark}}}}
body{{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 "Segoe UI",system-ui,sans-serif;padding:24px 16px}}
h1{{font-size:20px;margin:0 0 4px}} p{{margin:0;color:var(--muted)}} .wrap{{max-width:1500px;margin:auto;display:grid;gap:16px}}
.sum{{display:flex;flex-wrap:wrap;gap:12px 40px;background:var(--card);border:1px solid var(--line);border-radius:8px;padding:14px 18px}}
.sum span{{display:block;color:var(--muted);font-size:12px}} .sum b{{font-size:20px}} .sum .up,.sum .down{{display:block;font-size:12px}}
.up{{color:var(--up)}} .down{{color:var(--bad)}}
.t{{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:8px}}
table{{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}}
th,td{{padding:8px 12px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top;white-space:nowrap}}
th{{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);position:sticky;top:0;background:var(--card)}}
tr:last-child td{{border-bottom:0}} td.n{{text-align:right}} td small{{display:block;color:var(--muted);font-size:11px}}
td.bad{{color:var(--bad)}} .mono{{font-family:Consolas,monospace}}
</style></head><body><div class="wrap">
<div><h1>{e(TITLE)} training runs</h1><p>Every real training run under training/runs/, newest first. Rebuilt {now} by
benchmark/runs.py. Steps = the trainer's agent steps over every session; Speed = agent steps/s of the run's last
session (agents = worlds, 2 per world in arena self-play); Benchmark = the newest benchmark.ps1 result on the same
machine before the run (ms per step, env steps/s).</p></div>
{summary}
<div class="t"><table><thead><tr><th>Run</th><th>Started</th><th>Wall-clock</th><th>Ended</th><th>Steps</th>
<th>Speed</th><th>Benchmark</th><th>Config</th><th>Envs</th><th>Warm start</th><th>Commit</th><th>Machine</th></tr></thead>
<tbody>{"".join(body)}</tbody></table></div></div></body></html>
"""


def build(runs: Path = RUNS) -> Path:
    runs.mkdir(parents=True, exist_ok=True)
    out = runs / OUT_NAME
    tmp = out.with_suffix(".tmp")
    tmp.write_text(render(collect(runs)), encoding="utf-8")
    os.replace(tmp, out)
    return out


def record(run_id: str, ended: str, launcher: str | None, runs: Path = RUNS) -> None:
    """Write runs/<run>/run_meta.json: this machine, the commit and how the run ended."""
    sys.path.insert(0, str(HERE))
    import core

    m = core.machine()
    d = runs / run_id
    d.mkdir(parents=True, exist_ok=True)
    if ended == "finished":  # train.py exits 0 after a NaN stop too; a crash, resumed to the end, leaves recovered.log
        if _json(d / "meta.json").get("nan"):
            ended = "stopped: NaN in training"
        elif (d / "recovered.log").exists():
            ended = "finished (resumed after a crash)"
    meta = dict(
        host=m["hostname"],
        gpu=m["gpu"],
        cpu=m["cpu"],
        device="CPU" if os.environ.get("CUDA_VISIBLE_DEVICES") == "-1" else None,
        commit=core.git_commit(),
        ended=ended,
        launcher=launcher,
        recorded=datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
    )
    (d / META).write_text(json.dumps(meta, indent=1), encoding="utf-8")


def main() -> None:
    p = argparse.ArgumentParser(description="Rebuild training/runs/runs.html (the training runs grid).")
    p.add_argument("--record", metavar="RUN_ID", help="note this machine and how the run ended first")
    p.add_argument("--ended", default="exited", help="how the run ended, in words")
    p.add_argument("--launcher", help="the script that ran it")
    p.add_argument("--runs-dir", type=Path, default=RUNS, help="the runs/ folder (default training/runs)")
    a = p.parse_args()
    if a.record:
        record(a.record, a.ended, a.launcher, a.runs_dir)
    print(f"Training runs grid: {build(a.runs_dir)}")


if __name__ == "__main__":
    main()
