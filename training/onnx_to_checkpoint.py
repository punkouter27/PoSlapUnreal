"""Rebuild a resumable training checkpoint (.pt) from an exported brain (.onnx), when the run's own checkpoints are gone.

    .venv/Scripts/python.exe onnx_to_checkpoint.py <brain.onnx> <out.pt> [--std 0.25] [--level L0] [--push 0.84] [--steps N]

An exported brain carries everything the policy is (ppo.ExportPolicy: the input mean and std, every actor and critic
layer) but not what training adds on top: the exploration noise (log_std, set from --std), the optimiser's moments
(dropped: Adam starts fresh, as on a widened resume) and the curriculum position (the --level / --push / --steps given
here go into "extra", which train.py carries on resume). The normaliser's count is set high so a resume keeps the
saved mean and variance instead of re-learning them from the first batches.

2026-11-09: runs/mom_L0_night1_1315 (Mom's only L0 checkpoint in this checkout) was pruned by train.py's startup
cleanup; models/brains/mom_L0_candidate.onnx was exported from it on 2026-11-05 at push 0.84 of full, 196.6 M steps.
"""
import argparse
import math

import numpy as np
import onnx
import torch
from onnx import numpy_helper


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("onnx")
    ap.add_argument("out")
    ap.add_argument("--std", type=float, default=0.25, help="exploration noise to restart with (the runs settle near 0.25)")
    ap.add_argument("--level", default="L0")
    ap.add_argument("--push", type=float, default=None, help="push_scale the level had reached")
    ap.add_argument("--steps", type=float, default=0.0)
    a = ap.parse_args()

    t = {i.name: torch.from_numpy(numpy_helper.to_array(i).copy()) for i in onnx.load(a.onnx).graph.initializer}
    model = {k: v for k, v in t.items() if k.startswith(("actor.", "critic."))}
    act = model["actor.6.weight"].shape[0]
    model["log_std"] = torch.full((act,), math.log(a.std))
    std = t["std"].double()
    rms = {"mean": t["mean"], "var": (std * std - 1e-8).clamp_min(0.0).float(), "count": torch.tensor(1e8)}
    extra = {"level": a.level, "steps": int(a.steps), "rebuilt_from": a.onnx}
    if a.push is not None:
        extra["push_scale"] = a.push
    torch.save({"model": model, "obs_rms": rms, "extra": extra}, a.out)
    print(f"{a.out}: {len(model)} tensors, {rms['mean'].shape[0]} inputs, {act} actions, std {a.std}, extra {extra}")

    # check: the rebuilt policy gives the ONNX graph's own answer
    import onnxruntime as ort
    x = np.random.default_rng(0).normal(size=(1, rms["mean"].shape[0])).astype(np.float32)
    want = ort.InferenceSession(a.onnx).run(None, {"obs": x})[0]
    z = torch.clamp((torch.from_numpy(x) - rms["mean"]) / torch.sqrt(rms["var"] + 1e-8), -10.0, 10.0)
    h = z
    for i in (0, 2, 4):
        h = torch.nn.functional.elu(h @ model[f"actor.{i}.weight"].T + model[f"actor.{i}.bias"])
    got = (h @ model["actor.6.weight"].T + model["actor.6.bias"]).numpy()
    print(f"max diff vs the ONNX graph: {np.abs(got - want).max():.2e}")


if __name__ == "__main__":
    main()
