"""Export an ONNX brain from a run's checkpoint, for a candidate that has not graduated yet.

    .venv/Scripts/python.exe export_candidate.py <run> [<out name>]

Takes the run's best.pt (an exam passed) or latest.pt, and writes models/brains/<name>.onnx - the same graph
the trainer exports on graduation.  It never overwrites a shipped brain unless the name says so.
"""
import sys
from pathlib import Path

from brain_cpu import OBS
from ppo import PPO, PPOConfig, export_onnx

HERE = Path(__file__).parent
RUNS = HERE / "runs"
BRAINS = HERE / "models" / "brains"


def main():
    run = sys.argv[1]
    name = sys.argv[2] if len(sys.argv) > 2 else run
    src = RUNS / run / "best.pt"
    if not src.exists():
        src = RUNS / run / "latest.pt"
    if not src.exists():
        sys.exit(f"no checkpoint in runs/{run}")
    ppo = PPO(OBS, 30, 8, "cuda", PPOConfig())
    extra = ppo.load(str(src), widen=True)
    out = BRAINS / (name + ".onnx")
    export_onnx(ppo, str(out), OBS, fixed_batch=True)
    print(f"{src} -> {out}")
    print("extra:", {k: v for k, v in extra.items() if not hasattr(v, "shape")})


if __name__ == "__main__":
    main()
