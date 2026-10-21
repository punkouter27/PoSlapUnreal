"""Matching check (Blueprint 8): the same 5 s control stream in MuJoCo (Python) and in Unreal/URLab.

    python replay_check.py record models/grandpa_vs_grandpa_arena.xml     -> replays/<model>_5s.json
    python replay_check.py compare replays/<model>_5s.json <ue_dump.json> [--mjb Saved/URLab/scene_compiled.mjb]

Both sides: mj_resetData, stand pose, mj_forward, then per physics step: ctrl <- stream, record qpos, mj_step.
Unreal writes the same JSON shape ({"names": [...], "qpos": [[...], ...]}) from UMjReplayController.
With --mjb, the stream is also replayed on URLab's own compiled model, to tell a model-conversion
difference (XML vs URLab components) from a stepping difference.
"""
import argparse, json, math, sys
from pathlib import Path

import mujoco
import numpy as np

HERE = Path(__file__).parent
SECONDS, CONTROL_EVERY = 5.0, 4  # 50 Hz brain over 200 Hz physics


def stand(m, d, fp):
    mujoco.mj_resetData(m, d)
    for i in range(m.njnt):
        name = m.joint(i).name
        for j, deg in fp["stand_pose_deg"].items():
            if name.endswith(j):
                d.qpos[m.jnt_qposadr[i]] = math.radians(deg)
    mujoco.mj_forward(m, d)


def run(m, d, fp, ctrl_names, ctrl):
    """Replay ctrl (per control step, by actuator name) and return qpos per physics step (before each step)."""
    idx = [next(i for i in range(m.nu) if m.actuator(i).name.endswith(n)) for n in ctrl_names]
    stand(m, d, fp)
    out = []
    for k in range(len(ctrl) * CONTROL_EVERY):
        d.ctrl[idx] = ctrl[k // CONTROL_EVERY]
        out.append(d.qpos.copy())
        mujoco.mj_step(m, d)
    return np.array(out)


def joint_names(m):  # one name per qpos slot, so traces from differently-ordered models line up
    names = []
    for i in range(m.njnt):
        n = {0: 7, 1: 4}.get(int(m.jnt_type[i]), 1)
        names += [f"{m.joint(i).name}[{k}]" if n > 1 else m.joint(i).name for k in range(n)]
    return names


def record(xml):
    m = mujoco.MjModel.from_xml_path(xml)
    d = mujoco.MjData(m)
    fp = json.loads((HERE / "models" / "grandpa_fingerprint.json").read_text())
    stand(m, d, fp)
    rng = np.random.default_rng(0)
    names = [m.actuator(i).name for i in range(m.nu)]
    base = d.qpos[[m.jnt_qposadr[m.actuator_trnid[i, 0]] for i in range(m.nu)]]
    lo, hi = m.actuator_ctrlrange.T
    f, ph = rng.uniform(0.3, 1.5, m.nu), rng.uniform(0, 2 * math.pi, m.nu)
    t = np.arange(int(SECONDS * 50))[:, None] / 50
    ctrl = np.clip(base + 0.25 * np.sin(2 * math.pi * f * t + ph), lo, hi)  # smooth, deterministic
    q = run(m, d, fp, names, ctrl)
    out = HERE / "replays" / f"{Path(xml).stem}_5s.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"model": Path(xml).name, "mujoco": mujoco.__version__, "timestep": m.opt.timestep,
                               "control_every": CONTROL_EVERY, "actuators": names, "ctrl": ctrl.tolist(),
                               "names": joint_names(m), "qpos": q.tolist()}))
    print(f"recorded {out}: {len(ctrl)} control steps, {len(q)} physics steps, final pelvis z {q[-1][2]:.3f}")


def body_error(xml, ref_names, ref_q, names, q):
    """Max body-position difference (m) per step between two qpos traces, via forward kinematics."""
    m = mujoco.MjModel.from_xml_path(xml)
    d = mujoco.MjData(m)
    mine = joint_names(m)
    def to_model(nm, row):
        qp = np.zeros(m.nq)
        for n, v in zip(nm, row):
            hit = [i for i, x in enumerate(mine) if x == n or n.endswith("_" + x) or x.endswith("_" + n)]
            if hit:
                qp[hit[0]] = v
        return qp
    err = []
    for a, b in zip(ref_q, q):
        d.qpos[:] = to_model(ref_names, a); mujoco.mj_kinematics(m, d); xa = d.xpos.copy()
        d.qpos[:] = to_model(names, b); mujoco.mj_kinematics(m, d)
        err.append(np.abs(d.xpos[1:] - xa[1:]).max())
    return np.array(err)


def compare(ref_path, ue_path, mjb):
    ref = json.loads(Path(ref_path).read_text())
    xml = str(HERE / "models" / ref["model"])
    fp = json.loads((HERE / "models" / "grandpa_fingerprint.json").read_text())
    ok = True
    if mjb:
        m = mujoco.MjModel.from_binary_path(mjb)
        o = fp["option"]  # what USlapController::Bind sets at runtime (URLab drops the child spec's <option>)
        m.opt.timestep, m.opt.integrator, m.opt.solver, m.opt.iterations = o["timestep"], o["integrator"], o["solver"], o["iterations"]
        m.opt.gravity[:] = o["gravity"]
        q = run(m, mujoco.MjData(m), fp, ref["actuators"], np.array(ref["ctrl"]))
        e = body_error(xml, ref["names"], ref["qpos"], joint_names(m), q)
        print(f"XML vs URLab-compiled model (both stepped in Python): max {e.max() * 1000:.3f} mm, "
              f"first >1 mm at step {int(np.argmax(e > 1e-3)) if (e > 1e-3).any() else '-'}")
    ue = json.loads(Path(ue_path).read_text())
    n = min(len(ref["qpos"]), len(ue["qpos"]))
    e = body_error(xml, ref["names"], ref["qpos"][:n], ue["names"], ue["qpos"][:n])
    ok = e.max() <= 1e-3 and n == len(ref["qpos"])
    print(f"Python vs Unreal: {n} steps, max body error {e.max() * 1000:.3f} mm, at 1 s {e[min(n - 1, 199)] * 1000:.3f} mm, "
          f"first >1 mm at step {int(np.argmax(e > 1e-3)) if (e > 1e-3).any() else '-'}  ->  {'PASS' if ok else 'FAIL'}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["record", "compare"])
    ap.add_argument("a")
    ap.add_argument("b", nargs="?")
    ap.add_argument("--mjb")
    x = ap.parse_args()
    record(x.a) if x.cmd == "record" else compare(x.a, x.b, x.mjb)
