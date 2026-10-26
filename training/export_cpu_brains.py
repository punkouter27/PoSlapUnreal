"""Brain weights for Android: UE 5.8 has no ONNX runtime there, so USlapController rebuilds each brain for NNE's basic
CPU runtime from these files (normalise, clip, actor and critic ELU MLPs). One file per /Game/Brains asset:

    .venv/Scripts/python.exe export_cpu_brains.py   -> Content/Data/Brains/<asset>.bin

Format (little-endian): int32 tensor count, then per tensor int32 ndims, int32 dims[ndims], float32 data (row-major,
PyTorch layout). Order: mean, std, clip lo, clip hi, then (weight, bias) per linear layer: actor's, then critic's.
"""
import sys
from pathlib import Path

import numpy as np
import onnx
from onnx import numpy_helper

HERE = Path(__file__).parent
ROOT = HERE.parent
OUT = ROOT / "Content/Data/Brains"


def tensors(path):
    g = onnx.load(str(path)).graph
    init = {t.name: numpy_helper.to_array(t) for t in g.initializer}
    const = {n.output[0]: numpy_helper.to_array(n.attribute[0].t) for n in g.node if n.op_type == "Constant"}
    ops = [n for n in g.node if n.op_type != "Constant"]
    # the exporter's graph, exactly: Sub(mean) Div(std) Clip, then actor and critic: (Gemm Elu)* Gemm
    assert [n.op_type for n in ops[:3]] == ["Sub", "Div", "Clip"], [n.op_type for n in ops[:3]]
    clip = ops[2]
    out = [init[ops[0].input[1]], init[ops[1].input[1]], const[clip.input[1]].reshape(1), const[clip.input[2]].reshape(1)]
    heads = {}
    for n in ops[3:]:
        head = n.output[0].strip("/").split("/")[0] if n.output[0].startswith("/") else ("actor" if n.output[0] == "actions" else "critic")
        assert n.op_type in ("Gemm", "Elu"), n.op_type
        if n.op_type == "Gemm":
            a = {x.name: onnx.helper.get_attribute_value(x) for x in n.attribute}
            assert a.get("transB") == 1 and a.get("alpha", 1.0) == 1.0 and a.get("beta", 1.0) == 1.0, a
            heads.setdefault(head, []).append((init[n.input[1]], init[n.input[2]]))
        else:
            assert onnx.helper.get_attribute_value(n.attribute[0]) == 1.0, "ELU alpha"
    assert set(heads) == {"actor", "critic"} and len(heads["actor"]) == len(heads["critic"]), heads.keys()
    for h in ("actor", "critic"):
        for w, b in heads[h]:
            out += [w, b]
    return [np.ascontiguousarray(t, dtype="<f4") for t in out]


def write(ts, path):
    with open(path, "wb") as f:
        f.write(np.int32(len(ts)).tobytes())
        for t in ts:
            f.write(np.array([t.ndim, *t.shape], "<i4").tobytes())
            f.write(t.tobytes())


def forward(ts, obs):
    """What the rebuilt net computes, in numpy (self-check against onnxruntime)."""
    x = np.clip((obs - ts[0]) / ts[1], ts[2][0], ts[3][0])
    layers = list(zip(ts[4::2], ts[5::2]))
    outs = []
    for head in (layers[:len(layers) // 2], layers[len(layers) // 2:]):
        h = x
        for i, (w, b) in enumerate(head):
            h = h @ w.T + b
            if i < len(head) - 1:
                h = np.where(h > 0, h, np.expm1(h))
        outs.append(h)
    return outs


def main():
    import onnxruntime as ort
    OUT.mkdir(parents=True, exist_ok=True)
    names = sorted(p.stem for p in (ROOT / "Content/Brains").glob("*.uasset") if p.stem != "random_brain")   # B6's load test
    for name in names:
        src = next((p for p in (HERE / "models/brains" / f"{name}.onnx", HERE / "models" / f"{name}.onnx") if p.exists()), None)
        if src is None:
            sys.exit(f"{name}: no .onnx in training/models[/brains]")
        ts = tensors(src)
        obs = np.random.default_rng(0).standard_normal((1, ts[0].shape[0])).astype("f4") * 2
        ref = ort.InferenceSession(str(src)).run(None, {"obs": obs})
        got = forward(ts, obs)
        err = max(float((np.abs(r - g) / np.maximum(1.0, np.abs(r))).max()) for r, g in zip(ref, got))   # value runs to 100s
        assert err < 1e-4, f"{name}: numpy forward differs from onnxruntime by {err} (relative)"
        write(ts, OUT / f"{name}.bin")
        print(f"{name}: {src.relative_to(HERE)} -> {len(ts)} tensors, {ts[0].shape[0]} inputs, max diff vs ORT {err:.1e}")


if __name__ == "__main__":
    main()
