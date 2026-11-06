"""Write a queue's HTML training report when its driver ends (and an interim one at a deadline).

    .venv/Scripts/python.exe report_watch.py retrain_v4_queue DOCS/20261106/training_8h_report.html --deadline "2026-11-06 08:05"

Polls runs/<queue>.lock every minute: the report is written once the PID in it has exited (overnight.py removes the lock
at its end; a killed driver leaves it behind with a dead PID). At the deadline, if the driver is still going, an interim
report goes to the same path with "_interim" before .html, and the watch carries on. The output path is relative to the
repo root (training/..). Each extra --copy-to <dir> gets a copy of the final report.
"""
import argparse, json, os, shutil, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent


def alive(pid):
    if pid <= 0:
        return False
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
    return str(pid) in out


def driver_running(lock):
    if not lock.exists():
        return False
    try:
        return alive(int(json.loads(lock.read_text()).get("pid", 0)))
    except (ValueError, OSError):
        return False


def report(queue, out):
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, str(HERE / "night_report.py"), queue, str(out)], cwd=HERE)
    print(time.strftime("%H:%M:%S"), "report", out, flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("queue")
    ap.add_argument("out")
    ap.add_argument("--deadline", default=None, help="YYYY-MM-DD HH:MM: interim report if the driver still runs then")
    ap.add_argument("--copy-to", action="append", default=[])
    a = ap.parse_args()
    lock = HERE / "runs" / f"{a.queue}.lock"
    out = (ROOT / a.out).resolve()
    deadline = time.mktime(time.strptime(a.deadline, "%Y-%m-%d %H:%M")) if a.deadline else None
    time.sleep(120)                                   # let a driver that is starting write its lock
    while driver_running(lock):
        if deadline and time.time() >= deadline:
            report(a.queue, out.with_name(out.stem + "_interim.html"))
            deadline = None
        time.sleep(60)
    report(a.queue, out)
    for d in a.copy_to:
        Path(d).mkdir(parents=True, exist_ok=True)
        shutil.copy2(out, Path(d) / out.name)


if __name__ == "__main__":
    main()
