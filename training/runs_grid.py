"""The training runs grid hook: when a real run ends (finished, stopped, crashed), note this machine and how it ended in
runs/<run>/run_meta.json and rebuild runs/runs.html (benchmark/runs.py). Never fatal, never changes training.

The outermost launcher records: train.py run by hand (with or without --supervise), or overnight.py / night_driver.py
per stage. OWNER in the environment tells a child train.py that its launcher records it, so a run gets one record.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

OWNER = "SLAP_RUNS_GRID_OWNER"
RUNS_PY = Path(__file__).resolve().parents[1] / "benchmark" / "runs.py"


def newest_run(runs: Path, since: float) -> str | None:
    """The run this process trained when it did not name one: the newest config snapshot written since it started."""
    try:
        snaps = [p for p in runs.glob("*/config_snapshot.json") if p.stat().st_mtime >= since - 5]
        return max(snaps, key=lambda p: p.stat().st_mtime).parent.name if snaps else None
    except OSError:
        return None


def record(runs: Path, run: str | None, ended: str, launcher: str) -> None:
    if not run or run == "_benchmark" or not (runs / run).is_dir():
        return
    try:
        out = subprocess.run(
            [
                sys.executable,
                str(RUNS_PY),
                "--record",
                run,
                "--ended",
                ended,
                "--launcher",
                launcher,
                "--runs-dir",
                str(runs.resolve()),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        print((out.stdout or out.stderr).strip() or f"runs grid exited {out.returncode}", flush=True)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"{time.strftime('%H:%M:%S')} runs grid not rebuilt: {exc}", flush=True)
