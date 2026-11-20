"""Shared paths for the CPU test suite (no GPU, no Unreal). Run from the repo root: `python -m pytest`."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TRAINING = ROOT / "training"


@pytest.fixture
def run_script():
    """Run a repo script with this interpreter; returns the CompletedProcess (text output captured)."""

    def run(*args, cwd=ROOT, env=None):
        return subprocess.run([sys.executable, *map(str, args)], cwd=cwd, env=env, capture_output=True, text=True)

    return run
