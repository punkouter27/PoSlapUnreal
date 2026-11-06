"""Watch a brain stand in a live MuJoCo window, with training-style pushes (Blueprint 6), in real time.

    .venv/Scripts/python.exe view_brain.py models/brains/mom_final_v3.onnx --char mom --physics 3

Same loop as brain_cpu.rollout (and Unreal): every 4th physics step obs -> brain -> goals, Hill-capped muscles.
Every --every s a 150-300 N shove for 0.1-0.2 s hits the chest or the head in a random direction (--push 0: none).
A knockdown (any non-sole geom on the floor) resets the athlete 2 s later; the window's corner shows the time
standing, the push count and the falls. Space pauses (MuJoCo viewer); close the window to quit.
"""
import argparse, json, time
from pathlib import Path

import mujoco, mujoco.viewer
import numpy as np

from brain_cpu import Athlete, muscle_step, muscle_vmax, onnx_policy, stand
from constants import lpf_on

HERE = Path(__file__).parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("onnx")
    ap.add_argument("--char", default="mom")
    ap.add_argument("--physics", type=int, default=3)
    ap.add_argument("--every", type=float, default=3.0, help="seconds between pushes")
    ap.add_argument("--push", type=float, default=1.0, help="push strength scale (1 = 150-300 N)")
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    mdir = HERE / "models" / ("" if a.physics == 1 else f"v{a.physics}")
    fp = json.loads((mdir / f"{a.char}_fingerprint.json").read_text())
    m = mujoco.MjModel.from_xml_path(str(mdir / f"{a.char}_solo.xml"))
    d = mujoco.MjData(m)
    policy = onnx_policy(HERE / a.onnx)
    rng = np.random.default_rng(a.seed)
    targets = [m.body(n).id for n in ("chest", "head")]
    soles = {m.geom("foot_l").id, m.geom("foot_r").id}
    floor = {g for g in range(m.ngeom) if m.geom_bodyid[g] == 0}

    def reset():
        stand(m, d, fp)
        ath = Athlete(m, fp)
        ath.lpf = lpf_on(policy.n_in)
        return ath

    ath = reset()
    vmax = muscle_vmax(m, [ath])
    k, push_end, next_push, fell_at = 0, -1, a.every, None
    pushes = falls = 0
    t_up = 0.0
    with mujoco.viewer.launch_passive(m, d) as v:
        v.cam.azimuth, v.cam.elevation, v.cam.distance = 120, -12, 3.2
        wall0, sim0 = time.perf_counter(), 0.0
        while v.is_running():
            t = d.time
            if k % 4 == 0:
                mujoco.mj_kinematics(m, d)
                ath.decide(policy(ath.obs(d)))
            d.ctrl[ath.act] = ath.goal
            d.xfrc_applied[:] = 0
            if a.push > 0 and fell_at is None and t >= next_push:
                body = int(rng.choice(targets))
                ang = rng.uniform(0, 2 * np.pi)
                force = rng.uniform(150, 300) * a.push * np.array([np.cos(ang), np.sin(ang), 0.0])
                push_end, push_body, push_f = t + rng.uniform(0.1, 0.2), body, force
                next_push = t + a.every
                pushes += 1
            if t < push_end:
                d.xfrc_applied[push_body, :3] = push_f
            muscle_step(m, d, vmax)
            k += 1
            if fell_at is None:
                down = any((c.geom1 in floor and c.geom2 not in soles) or (c.geom2 in floor and c.geom1 not in soles)
                           for c in d.contact[:d.ncon])
                if down:
                    fell_at, falls = t, falls + 1
                    print(f"fell after {t - t_up:.1f} s standing ({pushes} pushes so far, {falls} falls)", flush=True)
            elif t - fell_at > 2.0:
                ath, fell_at = reset(), None
                k, push_end, next_push, t_up = 0, -1, a.every, 0.0
                wall0, sim0 = time.perf_counter(), 0.0
            if k % 8 == 0:   # 25 fps
                v.cam.lookat[:] = [d.qpos[0], d.qpos[1], 0.9]
                v.set_texts((mujoco.mjtFontScale.mjFONTSCALE_150, mujoco.mjtGridPos.mjGRID_TOPLEFT,
                             f"{a.char}  {Path(a.onnx).stem}\npushes\nfalls\nstanding",
                             f"\n{pushes}\n{falls}\n{'DOWN' if fell_at is not None else f'{d.time - t_up:.1f} s'}"))
                v.sync()
                lag = (d.time - sim0) - (time.perf_counter() - wall0)   # real time: physics never outruns the clock
                if lag > 0:
                    time.sleep(lag)


if __name__ == "__main__":
    main()
