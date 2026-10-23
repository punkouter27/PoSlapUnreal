"""Unattended training queue: ladder stages back to back inside a time budget.

    .venv/Scripts/python.exe overnight.py --hours 10
    .venv/Scripts/python.exe overnight.py --hours 16 --char kim --queue kim_queue.json   (a second athlete's own queue)

Stages come from runs/overnight_queue.json, re-read before every stage (a stage can be appended while one runs):
    [{"level": "L2", "minutes": 360, "keep_going": false, "resume": "<optional .pt>"}, ...]
Each stage resumes from the previous stage's brain (its best.pt, else latest.pt) unless it names one. A stage that
crashes is retried once from its own latest.pt. A stage that ends without graduating stops the queue (the ladder;
"no_gate": true on a stage lets the queue go on after it). Milestones go to runs/overnight.jsonl; every stage keeps train.py's
TensorBoard, telemetry, checkpoints, videos and report. At the end: models/brains/<char>_final.pt + .onnx and
runs/overnight_summary.html.
"""
import argparse, json, subprocess, sys, time
from pathlib import Path

import torch

from brain_cpu import OBS
from ppo import PPO, PPOConfig, export_onnx

HERE = Path(__file__).parent
RUNS = HERE / "runs"
QUEUE, LOG = RUNS / "overnight_queue.json", RUNS / "overnight.jsonl"


def log(**k):
    k = {"t": time.strftime("%Y-%m-%d %H:%M:%S"), **k}
    with LOG.open("a") as f:
        f.write(json.dumps(k) + "\n")
    print(json.dumps(k), flush=True)


def brain(run: Path):
    for name in ("best.pt", "latest.pt"):
        if (run / name).exists():
            return run / name
    return None


def train(stage, run, minutes, resume, char):
    cmd = [sys.executable, "-u", "train.py", "--char", char, "--level", stage["level"], "--minutes", f"{minutes:.1f}",
           "--envs", str(stage.get("envs", 8192)), "--run", run.name]
    if resume:
        cmd += ["--resume", str(resume)]
    cmd += stage.get("args", [])
    if stage.get("keep_going"):
        cmd.append("--keep-going")
    with open(RUNS / f"{run.name}.log", "a") as out:
        code = subprocess.run(cmd, cwd=HERE, stdout=out, stderr=subprocess.STDOUT).returncode
    if code and run.is_dir():   # housekeeping's "failed run" marker (AGENTS.md); a native crash leaves no traceback
        with open(run / "crash.log", "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} exit code {code}\n")
    return code


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=10.0)
    ap.add_argument("--char", default="grandpa")
    ap.add_argument("--queue", default=None, help="queue file in runs/ (default overnight_queue.json); its log and summary are named after it")
    a = ap.parse_args()
    global QUEUE, LOG
    summary = RUNS / "overnight_summary.html"
    if a.queue:
        QUEUE = RUNS / a.queue
        LOG, summary = QUEUE.with_suffix(".jsonl"), QUEUE.with_suffix(".html")
    deadline = time.time() + a.hours * 3600
    log(event="start", hours=a.hours)
    carry, done = None, []
    while time.time() < deadline - 120:
        queue = json.loads(QUEUE.read_text())
        todo = [s for s in queue if not s.get("done")]
        if not todo:
            break
        stage = todo[0]
        minutes = min(stage["minutes"], (deadline - time.time()) / 60 - 1)
        run = RUNS / f"{a.char}_{stage['level']}_night{len(done) + 1}_{time.strftime('%H%M')}"
        resume = stage.get("resume") or carry
        log(event="stage", level=stage["level"], run=run.name, minutes=round(minutes, 1), resume=str(resume))
        end = time.time() + minutes * 60
        code = train(stage, run, minutes, resume, a.char)
        for _ in range(5):   # retries from where it got to (a crash, or a trainer restarted to pick up a fix)
            if code == 0 or not (run / "latest.pt").exists() or time.time() > end - 300:
                break
            log(event="crash", run=run.name, code=code)
            code = train(stage, run, (end - time.time()) / 60, run / "latest.pt", a.char)
        meta = json.loads((run / "meta.json").read_text()) if (run / "meta.json").exists() else {}
        carry = brain(run) or carry
        stage.update(done=True, run=run.name, code=code, graduated=meta.get("graduated"), best_exam=meta.get("best_exam"))
        QUEUE.write_text(json.dumps(queue, indent=1))
        done.append(stage)
        log(event="milestone", level=stage["level"], run=run.name, code=code, graduated=meta.get("graduated"),
            best_exam=meta.get("best_exam"), episode_return=meta.get("episode_return"), steps=meta.get("steps"))
        if not meta.get("graduated") and not stage.get("no_gate"):   # the ladder: no level before the last one graduates
            log(event="stopped", level=stage["level"], run=run.name, reason="not graduated; later stages wait")
            break

    if carry:   # final weights: the last stage's best brain
        ppo = PPO(OBS, 30, 8, "cuda", PPOConfig())
        extra = ppo.load(str(carry), widen=True)
        final = HERE / "models" / "brains" / f"{a.char}_final"
        ppo.save(str(final.with_suffix(".pt")), extra)   # with its curricula and rate: a resume from it carries on
        export_onnx(ppo, str(final.with_suffix(".onnx")), OBS, fixed_batch=True)
        log(event="final", from_=str(carry), pt=str(final.with_suffix(".pt")), onnx=str(final.with_suffix(".onnx")))

    rows = "".join(f"<tr><td>{s['level']}</td><td>{s.get('run')}</td><td>{s.get('graduated') or 'no'}</td>"
                   f"<td>{s.get('best_exam')}</td><td><a href='{s.get('run')}/report.html'>report</a></td></tr>" for s in done)
    summary.write_text(
        f"<!doctype html><meta charset='utf-8'><title>Overnight training</title><style>body{{font:15px system-ui;margin:24px}}"
        f"td,th{{padding:4px 10px;border-bottom:1px solid #ccc}}</style><h1>Overnight training</h1><table><tr><th>Level</th>"
        f"<th>Run</th><th>Graduated</th><th>Best exam</th><th></th></tr>{rows}</table>", encoding="utf-8")
    log(event="end")


if __name__ == "__main__":
    main()
