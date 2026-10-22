"""The reference slap (L2 imitation): wind-up, strike, follow-through, found by search in CPU MuJoCo under the brain's
own limits, then imitated by the brain while it learns to strike (envs/slap.py).

    .venv/Scripts/python.exe slap_demo.py search     -> models/<char>_slap_demo.json (keyframes + per-brain-step reference)
    .venv/Scripts/python.exe slap_demo.py check      (replays it on 20 variations: hit, palm, speed, stays up)
    .venv/Scripts/python.exe slap_demo.py video out.gif

A slap is three key poses held as joint targets (wind-up: trunk turned back, arm raised and pulled back, elbow bent;
strike: trunk through, lean in, arm swung across, elbow whipping straight; follow-through), each reached in its own time,
then back to the stand pose. Targets move at most max_step per brain step, exactly like the brain's actions. The target
is the arena opponent's head (L2's mocap sphere); the podium is in the model and a knockdown is Unreal's rule.
Scored on the first contact with the target: palm, speed, staying up 1.5 s after, feet in the box; averaged over
target jitter and start noise so the motion is robust enough to imitate. Search: cross-entropy from an anatomical guess.
"""
import json, math, sys
from multiprocessing import Pool
from pathlib import Path

import mujoco
import numpy as np

from brain_cpu import Athlete, stand

HERE = Path(__file__).parent
CHAR = "grandpa"
OPP_HEAD = np.array([0.8015, 0.0, 0.74])
BOX = (0.20, 0.25)
# joint: (wind-up, strike, follow-through) degrees, the anatomical starting guess (probed: + twist = right shoulder forward,
# + flex = lean forward, raise 0 = arm horizontal, - swing = arm forward across, - shoulder twist = palm toward the face)
GUESS = {
    "waist_twist": (-30, 30, 34), "waist_flex": (0, 15, 12), "waist_side": (0, 5, 5),
    "shoulder_swing_r": (30, -80, -105), "shoulder_raise_r": (25, 25, 15), "shoulder_twist_r": (-40, -50, -45),
    "elbow_r": (70, 15, 30), "wrist_flex_r": (0, 0, 0), "wrist_dev_r": (0, 0, 0),
    "shoulder_swing_l": (0, 20, 20), "shoulder_raise_l": (-70, -60, -60), "elbow_l": (10, 30, 30),
    "hip_flex_l": (5, 15, 15), "hip_flex_r": (5, 15, 15), "knee_l": (10, 15, 15), "knee_r": (10, 15, 15),
    "hip_rot_l": (0, 0, 0), "hip_rot_r": (0, 0, 0),
}
# strike-phase start delay per joint (s): the kinetic chain whips proximal to distal (hips, trunk, shoulder, elbow, wrist)
DELAY = {"hip_rot_l": 0.0, "hip_rot_r": 0.0, "waist_twist": 0.02, "waist_flex": 0.02, "shoulder_swing_r": 0.05,
         "shoulder_raise_r": 0.05, "shoulder_twist_r": 0.07, "elbow_r": 0.09, "wrist_flex_r": 0.11}
KEYS = list(GUESS)
TIMES = (0.45, 0.22, 0.20)          # s to reach each pose
SIG_DEG, SIG_T = 15.0, 0.06


def setup():
    fp = json.loads((HERE / "models" / f"{CHAR}_fingerprint.json").read_text())
    return mujoco.MjModel.from_xml_path(str(HERE / "models" / f"{CHAR}_solo.xml")), fp


def unpack(x):
    n = len(KEYS)
    poses = x[:3 * n].reshape(3, n)
    times = np.clip(x[3 * n:3 * n + 3], 0.08, 0.8)
    delays = np.clip(x[3 * n + 3:], 0.0, 0.2)
    return poses, times, delays


def run(m, fp, x, seed=0, record=False, jitter=0.03):
    """One slap. Returns score and (with record) the per-brain-step goals and joint angles."""
    rng = np.random.default_rng(seed)
    d = mujoco.MjData(m)
    stand(m, d, fp)
    A = Athlete(m, fp, "")
    J = {n: i for i, n in enumerate(A.order)}
    if seed:
        d.qpos[A.qa] += rng.uniform(-0.05, 0.05, len(A.qa))
    d.mocap_pos[0] = d.qpos[:3] + OPP_HEAD + (rng.uniform(-jitter, jitter, 3) if seed else 0)
    mujoco.mj_forward(m, d)
    poses, times, delays = unpack(x)
    ends = np.cumsum(times)
    late = {k: dl for k, dl in zip(DELAY, delays)}
    hand, tg = m.geom("hand_r").id, int(m.body_geomadr[m.body("target").id])
    feet = {m.geom("foot_l").id, m.geom("foot_r").id}
    surf = {m.geom("floor").id, m.geom("podium").id}
    mine = {g for g in range(m.ngeom) if m.body_rootid[m.geom_bodyid[g]] == A.pelvis}
    feet0 = np.array([d.geom_xpos[f][:2] for f in sorted(feet)])
    prev, hit, speed, palm, after, dmin = d.geom_xpos[hand].copy(), None, 0.0, False, 0, 9.0
    goal = A.stand.copy()
    ref_goal, ref_q = [], []
    total = ends[-1] + 0.6
    k = 0
    while True:
        t = k * 0.005
        if k % 4 == 0:
            tgt = A.stand.copy()
            for j, name in enumerate(KEYS):   # each joint enters the strike late by its delay (the whip)
                ph = int(np.searchsorted(ends + np.array([0, late.get(name, 0.0), 0]), t, side="right"))
                if ph < 3:
                    tgt[J[name]] = math.radians(poses[ph][j])
            tgt = np.clip(tgt, A.lo, A.hi)
            goal = np.clip(tgt, goal - A.max_step, goal + A.max_step)
            if record:
                ref_goal.append(goal.copy()); ref_q.append(d.qpos[A.qa].copy())
        d.ctrl[A.act] = goal
        mujoco.mj_step(m, d)
        k += 1
        p = d.geom_xpos[hand].copy()
        v = float(np.linalg.norm(p - prev)) / 0.005
        prev = p
        dmin = min(dmin, float(np.linalg.norm(p - d.mocap_pos[0])) - 0.09)
        down = False
        for c in d.contact[:d.ncon]:
            g = {c.geom[0], c.geom[1]}
            if g & surf and (g - surf) and (x_ := (g - surf).pop()) in mine and x_ not in feet:
                down = True
            if hit is None and tg in g:
                other = c.geom[1] if c.geom[0] == tg else c.geom[0]
                normal = c.frame[:3] * (-1.0 if c.geom[0] == tg else 1.0)
                hit, speed = (other == hand), v
                palm = other == hand and float(normal @ -d.geom_xmat[hand].reshape(3, 3)[:, 2]) > 0.5
                if not hit:
                    return dict(score=-10.0 - dmin * 10, fell=False, hit=False, palm=False, speed=0.0, body_touch=True), ref_goal, ref_q
        off = np.abs(np.array([d.geom_xpos[f][:2] for f in sorted(feet)]) - feet0)
        out = (off[:, 0] > BOX[0]).any() or (off[:, 1] > BOX[1]).any()
        if down or out:   # partial credit for getting closer and lasting longer, so the search can climb out of falling
            s = (20 + 3 * speed if palm else 0.0) - 40.0 - 10.0 * dmin + 5.0 * min(t, ends[1])
            why = "out of the box" if out else next(m.geom(g).name for c in d.contact[:d.ncon] for g in (c.geom[0], c.geom[1])
                                                     if g in mine and g not in feet and ({c.geom[0], c.geom[1]} & surf))
            return dict(score=s, fell=True, hit=hit is not None, palm=palm, speed=speed, t=round(t, 2), why=why, dmin=round(dmin, 2)), ref_goal, ref_q
        if hit is not None:
            after += 1
            if after >= 300:   # up 1.5 s after
                break
        elif t > total:
            return dict(score=-10.0 * dmin - 5.0, fell=False, hit=False, palm=False, speed=0.0), ref_goal, ref_q
    clean = palm and speed >= 8.0
    s = (20 + 5 * speed + (40 if clean else 0)) if palm else 2 + speed
    return dict(score=s, fell=False, hit=True, palm=palm, speed=speed), ref_goal, ref_q


def _eval(args):
    x, seeds = args
    m, fp = setup()
    return float(np.mean([run(m, fp, x, s)[0]["score"] for s in seeds]))


def search(iters=150, pop=96, elite=12):
    x0 = np.array([GUESS[k][i] for i in range(3) for k in KEYS] + list(TIMES) + list(DELAY.values()), float)
    prev = HERE / "models" / f"{CHAR}_slap_demo.json"
    if prev.exists():   # continue from the last search: its poses and times, new joints at the guess
        d = json.loads(prev.read_text())
        old = dict(zip(d["joints"], zip(*d["poses_deg"])))
        x0 = np.array([old.get(k, GUESS[k])[i] for i in range(3) for k in KEYS] + d["times_s"] + d.get("delays_s", list(DELAY.values())), float)
    sig = np.array([SIG_DEG] * (3 * len(KEYS)) + [SIG_T] * 3 + [0.03] * len(DELAY))
    mu, best = x0.copy(), (-1e9, x0)
    rng = np.random.default_rng(0)
    with Pool(16) as P:
        for it in range(iters):
            xs = mu + rng.standard_normal((pop, len(mu))) * sig
            xs[0] = best[1]
            seeds = [1 + 3 * it, 2 + 3 * it, 3 + 3 * it]   # fresh variations every generation: robust, not lucky
            sc = np.array(P.map(_eval, [(x, seeds) for x in xs]))
            idx = np.argsort(sc)[::-1][:elite]
            mu = xs[idx].mean(0)
            sig = np.maximum(xs[idx].std(0), np.r_[[2.0] * (3 * len(KEYS)), [0.01] * 3, [0.005] * len(DELAY)])
            if sc[idx[0]] > best[0]:
                best = (float(sc[idx[0]]), xs[idx[0]].copy())
            if it % 5 == 0 or it == iters - 1:
                m, fp = setup()
                r = run(m, fp, best[1])[0]
                print(f"it {it:3d} best {best[0]:7.2f} | nominal: hit {r['hit']} palm {r['palm']} {r['speed']:.1f} m/s fell {r['fell']}", flush=True)
    return best[1]


def save(x):
    m, fp = setup()
    r, goals, qs = run(m, fp, x, seed=0, record=True)
    poses, times, delays = unpack(x)
    out = {"joints": KEYS, "poses_deg": poses.round(2).tolist(), "times_s": times.round(4).tolist(),
           "delays_s": delays.round(4).tolist(), "delay_joints": list(DELAY), "result": r,
           "joint_order": fp["joint_order"], "ref_goal": np.round(goals, 5).tolist(), "ref_q": np.round(qs, 5).tolist(),
           "note": "per brain step (50 Hz) from the stand pose; ref_q = the joint angles the slap actually produced"}
    p = HERE / "models" / f"{CHAR}_slap_demo.json"
    p.write_text(json.dumps(out))
    print("saved", p, r)


def check(n=20):
    x = demo_x()
    m, fp = setup()
    rs = [run(m, fp, x, s)[0] for s in range(1, n + 1)]
    ok = [r for r in rs if r["palm"] and r["speed"] >= 8 and not r["fell"]]
    print(f"{len(ok)}/{n} clean (palm, >= 8 m/s, up 1.5 s); falls {sum(r['fell'] for r in rs)}; "
          f"speeds {np.round([r['speed'] for r in rs], 1).tolist()}")


def demo_x():
    d = json.loads((HERE / "models" / f"{CHAR}_slap_demo.json").read_text())
    return np.array([v for row in d["poses_deg"] for v in row] + d["times_s"] + d["delays_s"], float)


def video(out):
    from PIL import Image
    m, fp = setup()
    x = demo_x()
    r_ = mujoco.Renderer(m, 480, 360)
    cam = mujoco.MjvCamera(); cam.azimuth, cam.elevation, cam.distance = 90, -8, 2.6
    frames = []
    d0 = json.loads((HERE / "models" / f"{CHAR}_slap_demo.json").read_text())
    d = mujoco.MjData(m); stand(m, d, fp)
    A = Athlete(m, fp, "")
    d.mocap_pos[0] = d.qpos[:3] + OPP_HEAD; mujoco.mj_forward(m, d)
    for k in range(len(d0["ref_goal"]) * 4):
        if k % 4 == 0:
            d.ctrl[A.act] = d0["ref_goal"][k // 4]
        mujoco.mj_step(m, d)
        if k % 8 == 0:
            cam.lookat[:] = [d.qpos[0] + 0.3, d.qpos[1], 1.2]
            r_.update_scene(d, cam); frames.append(Image.fromarray(r_.render()))
    frames[0].save(out, save_all=True, append_images=frames[1:], duration=40, loop=0)
    print("video", out, len(frames), "frames")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "search"
    if cmd == "search":
        save(search())
    elif cmd == "check":
        check()
    else:
        video(sys.argv[2])
