"""Find a working slap for Grandpa by search, in CPU MuJoCo, under the brain's own limits (L2 demonstration).

    .venv/Scripts/python.exe slap_demo.py search            -> models/<char>_slap_demo.json
    .venv/Scripts/python.exe slap_demo.py check             (replays the saved slap, prints hit / speed / stays up)

A slap is two keyframes of joint targets (wind-up, strike) plus two times; targets move at most max_step per
brain step, exactly like the brain's actions. Scored on the first contact with the target: palm-first speed,
staying up 1.5 s after. Cross-entropy search; the target is the arena opponent's head (L2's mocap sphere).
"""
import json, math, sys
from pathlib import Path

import mujoco
import numpy as np

from brain_cpu import Athlete, stand

HERE = Path(__file__).parent
CHAR = "grandpa"
# (joint, windup range deg, strike range deg)
KEYS = [("waist_twist", (-40, 40), (-40, 40)), ("waist_flex", (-10, 40), (-10, 50)), ("waist_side", (-20, 20), (-20, 20)),
        ("shoulder_swing_r", (-110, 30), (-110, 30)), ("shoulder_raise_r", (-80, 60), (-80, 60)),
        ("shoulder_twist_r", (-60, 60), (-60, 60)), ("elbow_r", (0, 110), (0, 110)),
        ("wrist_flex_r", (-50, 50), (-50, 50)), ("hip_flex_r", (-10, 30), (-10, 30)), ("hip_flex_l", (-10, 30), (-10, 30)),
        ("knee_r", (0, 40), (0, 40)), ("knee_l", (0, 40), (0, 40))]
LO = np.array([k[1][0] for k in KEYS] + [k[2][0] for k in KEYS] + [0.2, 0.2])
HI = np.array([k[1][1] for k in KEYS] + [k[2][1] for k in KEYS] + [1.2, 0.8])


def setup():
    fp = json.loads((HERE / "models" / f"{CHAR}_fingerprint.json").read_text())
    m = mujoco.MjModel.from_xml_path(str(HERE / "models" / f"{CHAR}_solo.xml"))
    return m, fp


def run(m, fp, x, record=False, tgt_offset=(0.0, 0.0, 0.0)):
    d = mujoco.MjData(m)
    stand(m, d, fp)
    A = Athlete(m, fp, "")
    J = {n: i for i, n in enumerate(A.order)}
    n = len(KEYS)
    wind, strike, t_wind, t_strike = x[:n], x[n:2 * n], x[2 * n], x[2 * n + 1]
    d.mocap_pos[0] = d.qpos[:3] + np.array([0.8015, 0.0, 0.74]) + np.array(tgt_offset)
    mujoco.mj_forward(m, d)
    hand, tg = m.geom("hand_r").id, int(m.body_geomadr[m.body("target").id])
    feet = {m.geom("foot_l").id, m.geom("foot_r").id}
    floor = m.geom("floor").id
    prev, hit, speed, palm, after, frames = d.geom_xpos[hand].copy(), None, 0.0, False, 0, []
    goal = A.stand.copy()
    for k in range(int((t_wind + t_strike + 1.6) / 0.005)):
        t = k * 0.005
        if k % 4 == 0:
            tgt = A.stand.copy()
            if t < t_wind + t_strike:
                key = wind if t < t_wind else strike
                for (name, _, _), deg in zip(KEYS, key):
                    tgt[J[name]] = math.radians(deg)
            tgt = np.clip(tgt, A.lo, A.hi)
            goal = np.clip(tgt, goal - A.max_step, goal + A.max_step)
            if record:
                frames.append((d.qpos.copy(), d.qvel.copy(), goal.copy()))
        d.ctrl[A.act] = goal
        mujoco.mj_step(m, d)
        p = d.geom_xpos[hand].copy()
        v = np.linalg.norm(p - prev) / 0.005
        prev = p
        for c in d.contact[: d.ncon]:
            g = {c.geom[0], c.geom[1]}
            if floor in g and (g - {floor}).pop() not in feet:
                return {"score": (speed if palm else 0.0) - 20.0, "fell": True, "hit": hit, "speed": speed, "palm": palm, "frames": frames}
            if hit is None and tg in g:
                other = (g - {tg}).pop()
                normal = c.frame[:3] * (-1.0 if c.geom[0] == tg else 1.0)
                palm_dir = -d.geom_xmat[hand].reshape(3, 3)[:, 2]
                hit, speed = t, v
                palm = other == hand and float(normal @ palm_dir) > 0.5
        if hit is not None:
            after += 1
            if after >= 300:   # stayed up 1.5 s
                break
    if hit is None:
        dist = np.linalg.norm(d.geom_xpos[hand] - d.mocap_pos[0])
        return {"score": -5.0 - 10.0 * dist, "fell": False, "hit": None, "speed": 0.0, "palm": False, "frames": frames}
    return {"score": (10.0 + speed if palm else 0.2 * speed), "fell": False, "hit": hit, "speed": speed, "palm": palm, "frames": frames}


def search(iters=60, pop=96, elite=12, seed=0):
    m, fp = setup()
    rng = np.random.default_rng(seed)
    mu, sd = (LO + HI) / 2, (HI - LO) / 3
    best = (-1e9, None, None)
    for it in range(iters):
        xs = np.clip(mu + sd * rng.standard_normal((pop, len(mu))), LO, HI)
        res = [run(m, fp, x) for x in xs]
        sc = np.array([r["score"] for r in res])
        order = np.argsort(-sc)
        if sc[order[0]] > best[0]:
            best = (sc[order[0]], xs[order[0]].copy(), res[order[0]])
        el = xs[order[:elite]]
        mu, sd = el.mean(0), np.maximum(el.std(0), (HI - LO) * 0.02)
        b = best[2]
        print(f"it {it:3d} best score {best[0]:6.2f}  palm {b['palm']}  speed {b['speed']:.2f} m/s  fell {b['fell']}  "
              f"| pop palm-hits {sum(r['palm'] and not r['fell'] for r in res)}/{pop}", flush=True)
    return best, m, fp


def save(best):
    names = [k[0] for k in KEYS]
    x = best[1]
    out = {"joints": names, "windup_deg": x[:len(names)].tolist(), "strike_deg": x[len(names):2 * len(names)].tolist(),
           "t_windup": float(x[-2]), "t_strike": float(x[-1]), "score": float(best[0]),
           "speed_mps": float(best[2]["speed"]), "palm": bool(best[2]["palm"]), "fell": bool(best[2]["fell"])}
    (HERE / "models" / f"{CHAR}_slap_demo.json").write_text(json.dumps(out, indent=1))
    return out


def load_x():
    j = json.loads((HERE / "models" / f"{CHAR}_slap_demo.json").read_text())
    return np.array(j["windup_deg"] + j["strike_deg"] + [j["t_windup"], j["t_strike"]])


if __name__ == "__main__":
    if sys.argv[1] == "search":
        best, m, fp = search()
        print(json.dumps(save(best), indent=1))
    else:
        m, fp = setup()
        x = load_x()
        for off in [(0, 0, 0), (0.03, 0, 0), (-0.03, 0.03, 0), (0, -0.03, 0.03)]:
            r = run(m, fp, x, tgt_offset=off)
            print(f"target offset {off}: palm {r['palm']} speed {r['speed']:.2f} m/s fell {r['fell']} hit at {r['hit']}")
