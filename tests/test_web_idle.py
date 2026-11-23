"""The browser's demo mode (attract) clock: 30 s without input, frames over 0.25 s ignored.

Runs Web/idle.test.mjs with Node's own test runner (no npm packages needed), so the same checks run in pytest locally
and in CI (deploy.yml sets up Node before pytest).
"""

import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_demo_idle_clock_ignores_long_frames():
    node = shutil.which("node")
    assert node, "Node.js 20+ runs the web checks (AGENTS.md, part 3)"
    r = subprocess.run(
        [node, "--test", "--test-reporter=tap", str(ROOT / "Web" / "idle.test.mjs")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "# fail 0" in r.stdout, r.stdout
    assert "# pass 6" in r.stdout, r.stdout
