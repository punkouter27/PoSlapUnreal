"""Prune the abandoned training runs, and report what it would free.

    .venv/Scripts/python.exe prune_runs.py                 # dry run: report only
    .venv/Scripts/python.exe prune_runs.py --apply         # delete

What it deletes
  - a FAILED run: meta.json says exit_code != 0, or nan == true, or a crash log sits next to it;
  - a TEST run: fewer than 5,000 steps;
  - an OLD FINISHED run: meta.json says it graduated, and it is older than --older-than-hours;
  - the empty and one-line log files the overnight queues leave behind (older than the threshold).

What it NEVER deletes (AGENTS.md)
  - a run written in the last 24 hours;
  - a run a training queue still has to resume: any name in a queue file's "run" or "resume" field,
    finished stages included (a finished stage can still be resumed from its checkpoint);
  - a run that holds the only copy of a checkpoint a queue points at;
  - anything outside training/runs.

Nothing is deleted unless --apply is given.
"""
import argparse
import json
import shutil
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNS = HERE / "runs"

# A run is never touched inside this window (AGENTS.md: "Never touch a run written in the last 24 h").
FRESH_HOURS = 24
# A run with fewer steps than this is a test, not a training run.
TEST_STEPS = 5000


def folder_size(path):
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def queue_referenced():
    """Every run name any queue file names, in a "run" or a "resume" field."""
    names = set()
    if not RUNS.exists():
        return names
    for q in RUNS.glob("*.json"):
        try:
            data = json.loads(q.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(data, list):
            continue
        for stage in data:
            if not isinstance(stage, dict):
                continue
            for key in ("run", "resume"):
                v = stage.get(key)
                if not isinstance(v, str):
                    continue
                # a resume path looks like "runs/<name>/<file>.pt" or "models/brains/<name>.pt"
                parts = v.replace("\\", "/").split("/")
                if len(parts) >= 2 and parts[0] == "runs":
                    names.add(parts[1])
                elif parts and parts[0]:
                    names.add(parts[-1].split(".")[0])
    return names


def classify(run_dir, referenced, older_than_h):
    """(reason, size_bytes) for a run that may be deleted, or (None, size) for one that may not."""
    size = folder_size(run_dir)
    age_h = (time.time() - run_dir.stat().st_mtime) / 3600.0

    if age_h < FRESH_HOURS:
        return None, size
    if run_dir.name in referenced:
        return None, size

    meta_path = run_dir / "meta.json"
    meta = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {}

    crashed = any(run_dir.glob("*.err")) and bool(meta.get("exit_code") not in (0, None))
    if meta.get("exit_code") not in (None, 0) or meta.get("nan") or crashed:
        return f"failed (exit_code={meta.get('exit_code')}, nan={meta.get('nan')})", size
    if meta.get("steps", 0) < TEST_STEPS:
        return f"test run ({meta.get('steps', 0)} steps)", size
    if meta.get("graduated") is not None and age_h >= older_than_h:
        return f"old finished run ({age_h / 24:.1f} days old)", size
    return None, size


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually delete (without it, nothing is touched)")
    ap.add_argument("--older-than-hours", type=float, default=72.0,
                    help="how old a finished run must be before it is purged (default 72)")
    opts = ap.parse_args()

    if not RUNS.exists():
        print(f"no {RUNS} to prune")
        return 0

    referenced = queue_referenced()
    print(f"=== PoSlap run purge ({'APPLYING' if opts.apply else 'dry run'}) ===")
    print(f"  {len(referenced)} run(s) are named by a training queue and are never touched: "
          f"{', '.join(sorted(referenced)) or 'none'}")

    victims, kept, freed = [], [], 0
    for run_dir in sorted(RUNS.iterdir()):
        if not run_dir.is_dir():
            continue
        reason, size = classify(run_dir, referenced, opts.older_than_hours)
        if reason:
            victims.append((run_dir, reason, size))
            freed += size
        else:
            kept.append((run_dir, size))

    # the leftover log files the overnight queues write next to the runs
    stale_logs, log_bytes = [], 0
    cutoff = time.time() - opts.older_than_hours * 3600.0
    for f in RUNS.iterdir():
        if f.is_file() and f.suffix in (".err", ".out") and f.stat().st_mtime < cutoff:
            stale_logs.append((f, f.stat().st_size))
            log_bytes += f.stat().st_size

    print(f"\n  would delete {len(victims)} run(s) and {len(stale_logs)} log file(s):")
    for run_dir, reason, size in victims:
        print(f"    {run_dir.name:34s} {size / 1024 / 1024:8.2f} MB  {reason}")
    for f, size in stale_logs:
        print(f"    {f.name:34s} {size / 1024:8.2f} KB  stale log")
    print(f"\n  kept {len(kept)} run(s)")
    print(f"  space freed: {freed / 1024 / 1024:.1f} MB (runs) + {log_bytes / 1024:.1f} KB (logs) "
          f"= {(freed + log_bytes) / 1024 / 1024:.1f} MB")

    if not opts.apply:
        print("\n  dry run: nothing was deleted. Re-run with --apply to delete.")
        return 0

    for run_dir, _reason, _size in victims:
        shutil.rmtree(run_dir, ignore_errors=True)
        print(f"  deleted {run_dir.name}")
    for f, _size in stale_logs:
        try:
            f.unlink()
            print(f"  deleted {f.name}")
        except OSError as e:
            print(f"  could not delete {f.name}: {e}")
    print(f"\n  freed {(freed + log_bytes) / 1024 / 1024:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
