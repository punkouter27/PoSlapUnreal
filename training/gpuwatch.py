"""The GPU's temperature and clocks (nvidia-smi), for the telemetry and for the crash guard (tasks J10, CUDA 719).

The training laptop's RTX 5070 Ti idles at ~81 C and runs at 87-89 C under a training load, with its SM clock
throttled to 850-1000 MHz and stretches at 180 MHz (2026-11-03 repro, DOCS/20261103/training_realism.md). Every
CUDA 719 of 2026-10-30 came from such a run. The crash guard (train.py --supervise, overnight.py's retries)
waits for the GPU to cool before it resumes a crashed run from its last checkpoint.
"""
from __future__ import annotations

import subprocess
import time


def gpu_status():
    """{'temp_c', 'sm_mhz', 'power_w', 'util'} of GPU 0, or {} when nvidia-smi is not there."""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=temperature.gpu,clocks.sm,power.draw,utilization.gpu",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10).stdout
        t, c, p, u = [x.strip() for x in out.splitlines()[0].split(",")]
        return {"temp_c": float(t), "sm_mhz": float(c), "power_w": float(p), "util": float(u)}
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return {}


def cooldown(below_c=75.0, max_s=300.0, log=print):
    """Wait (at most max_s) until the GPU is under below_c: a crashed run resumes on a cooler card."""
    t0 = time.time()
    while time.time() - t0 < max_s:
        s = gpu_status()
        if not s or s["temp_c"] < below_c:
            break
        time.sleep(15)
    s = gpu_status()
    log(f"cooldown: {time.time() - t0:.0f} s, GPU now {s.get('temp_c', '?')} C")
    return s
