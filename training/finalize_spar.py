"""Save the final model weights for an unattended spar queue (AGENTS.md: "Save final model weights").

Guaranteed export: for each athlete, take the LAST run of the queue and use its best.pt (an exam
passed) or, if the stage never sat an exam, latest.pt - overnight.py skips those, so this is the
safety net that still ships the weights.

    .venv/Scripts/python.exe finalize_spar.py spar3h2_queue
"""
import json
import sys
from pathlib import Path

import torch

from brain_cpu import OBS
from ppo import PPO, PPOConfig, export_onnx

HERE = Path(__file__).parent
RUNS = HERE / "runs"
BRAINS = HERE / "models" / "brains"


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "spar3h2_queue"
    queue = json.loads((RUNS / (name + ".json")).read_text())
    log_path = RUNS / (name + ".jsonl")
    events = []
    if log_path.exists():
        for line in log_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                events.append(json.loads(line))
            except Exception:
                pass

    last_run, opponent, best_run = {}, {}, {}
    for stage in queue:
        if stage.get("run"):
            char = stage.get("char", "grandpa")
            last_run[char] = stage["run"]
            if stage.get("vs"):
                opponent[char] = stage["vs"]
    for e in events:                       # the queue file may lag; the log is authoritative
        if e.get("event") == "stage" and e.get("run"):
            run = e["run"]
            char = next((c for c in last_run if run.startswith(c)), None)
            if char:
                last_run[char] = run

    # the weights to ship: the run with the best exam for that athlete (not merely the last
    # one) - a stage that never sat an exam scores -1 and is skipped.
    def exam_of(run):
        p = RUNS / run / "meta.json"
        if not p.exists():
            return -1.0
        try:
            return float(json.loads(p.read_text()).get("best_exam", -1.0))
        except Exception:
            return -1.0

    for stage in queue:
        run = stage.get("run")
        if not run:
            continue
        char = stage.get("char", "grandpa")
        sc = exam_of(run)
        if sc >= 0 and (char not in best_run or sc > exam_of(best_run[char])):
            best_run[char] = run
    for char, run in last_run.items():      # fall back to the last run when nothing scored
        best_run.setdefault(char, run)

    # a head-to-head (eval_candidates.py) picks the winner against one fixed opponent; that beats
    # "highest exam score", because each stage's exam used a different opponent.
    winner_p = RUNS / (name + "_winner.json")
    if winner_p.exists():
        try:
            for char, run in json.loads(winner_p.read_text()).items():
                if (RUNS / run / "best.pt").exists():
                    best_run[char] = run
                    print("head-to-head winner for %s: %s" % (char, run))
        except Exception as e:
            print("winner file unreadable (%s); using the exam scores" % e)

    print("final weights from:")
    for char, run in best_run.items():
        d = RUNS / run
        src = d / "best.pt"
        note = "best exam of the session"
        if not src.exists():
            src = d / "latest.pt"
            note = "no exam (fallback to latest.pt)"
        if not src.exists():
            print("  %-10s %-42s NO CHECKPOINT" % (char, run))
            continue
        ppo = PPO(OBS, 30, 8, "cuda", PPOConfig())
        extra = ppo.load(str(src), widen=True)
        suffix = "_vs_%s" % opponent[char] if char in opponent else ""
        final = BRAINS / ("%s_final%s" % (char, suffix))
        ppo.save(str(final.with_suffix(".pt")), extra)
        export_onnx(ppo, str(final.with_suffix(".onnx")), OBS, fixed_batch=True)
        meta_p = d / "meta.json"
        meta = json.loads(meta_p.read_text()) if meta_p.exists() else {}
        print("  %-10s %-42s -> %s.pt / .onnx   [%s] best_exam=%s steps=%s"
              % (char, run, final.name, note, meta.get("best_exam"), meta.get("steps")))

    print("\nverify:")
    for char, run in best_run.items():
        suffix = "_vs_%s" % opponent[char] if char in opponent else ""
        onnx = BRAINS / ("%s_final%s.onnx" % (char, suffix))
        pt = BRAINS / ("%s_final%s.pt" % (char, suffix))
        if not onnx.exists():
            print("  %-28s MISSING" % onnx.name)
            continue
        try:
            sess = torch.onnx.load(str(onnx), ) if hasattr(torch.onnx, "load") else None
        except Exception:
            sess = None
        try:
            import onnxruntime as ort
            s = ort.InferenceSession(str(onnx), providers=["CPUExecutionProvider"])
            shapes = {i.name: (i.shape, i.type) for i in s.get_inputs()}
            outs = {o.name: o.shape for o in s.get_outputs()}
            import numpy as np
            res = s.run(None, {shapes and list(shapes)[0]: np.zeros((1, OBS), dtype=np.float32)})
            print("  %-28s in=%s out=%s  ran ok, action shape=%s  pt=%.1f KB"
                  % (onnx.name, {k: v[0] for k, v in shapes.items()}, outs,
                     list(res[0].shape), pt.stat().st_size / 1024))
        except Exception as e:
            print("  %-28s %s" % (onnx.name, str(e)[:120]))


if __name__ == "__main__":
    main()
