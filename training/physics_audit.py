"""Physics audit (DOCS/20261104/physics3.md): what the strike does to the physics, per physics version, on the CPU.

    .venv/Scripts/python.exe physics_audit.py strikes --physics 3 --pairing gk --matches 6     (tunnelling, first-contact depth,
                                                                                               momentum, head kinematics)
    .venv/Scripts/python.exe physics_audit.py cost --pairing gk                                (mj_step cost v1/v2/v3, CCD flags,
                                                                                               solver options; open-loop replay)
Matches are match.py's own (jittered exam starts), the brains as Scripts/collision_review.py --brains v2.
"""
import argparse, json, math, os, sys, time
from multiprocessing import Pool
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
BRAINS = HERE / "models" / "brains"
PAIR = {"gg": ("grandpa_L4_v2", "grandpa_L4_v2", "grandpa", "grandpa"),
        "gk": ("grandpa_final_vs_kim_v2", "kim_final_vs_grandpa_v2", "grandpa", "kim"),
        "kk": ("kim_L4_v2_candidate", "kim_L4_v2_candidate", "kim", "kim")}
HAND_HALF = 0.015   # the hand box's half thickness (build_body: size 0.042 x L/2 x 0.015)


def seg_dist(p, a, b):
    """Distance from point p to segment ab."""
    ab = b - a
    t = np.clip(np.dot(p - a, ab) / max(np.dot(ab, ab), 1e-12), 0, 1)
    return np.linalg.norm(p - (a + t * ab))


def seg_seg(p0, p1, a, b, n=12):
    """Min distance between segment p0p1 (the hand's sweep in one step) and segment ab (sampled)."""
    return min(seg_dist(p0 + (p1 - p0) * s, a, b) for s in np.linspace(0, 1, n))


def one_strikes(args):
    phys, pairing, i = args
    os.environ["SLAP_PHYSICS"] = str(phys)
    import mujoco
    import match
    from brain_cpu import muscle_step as real
    rb, bb, rc, bc = PAIR[pairing]
    m, _, pre = match.load(None, rc, bc)
    gid = lambda n: mujoco.mj_name2id(m, mujoco.mjtObj.mjOBJ_GEOM, n)
    face = {p: [g for g in (gid(p + "head"), gid(p + "jaw_l"), gid(p + "jaw_r")) if g >= 0] for p in pre}
    hands = {p: [gid(p + "hand_l"), gid(p + "hand_r")] for p in pre}
    pelvis = {p: m.body(p + "pelvis").id for p in pre}
    world = {g for g in range(m.ngeom) if m.geom_bodyid[g] == 0}
    owner = {g: next((p for p in pre if m.body_rootid[m.geom_bodyid[g]] == pelvis[p]), None) for g in range(m.ngeom)}
    out = dict(first_pen=[], approach=[], tunnel=0, sweeps=0, kicks=[], resid=[], steps=0, t_step=0.0)
    st = dict(prev=None, touching=set(), P=None, win=None)
    f6 = np.zeros(6)

    d2 = mujoco.MjData(m)   # scratch: after mj_step d's kinematics are the step's start (stale); match.py reads them as they are

    def momentum(d):
        d2.qpos[:], d2.qvel[:] = d.qpos, d.qvel
        mujoco.mj_kinematics(m, d2); mujoco.mj_comPos(m, d2); mujoco.mj_comVel(m, d2); mujoco.mj_subtreeVel(m, d2)
        return sum(m.body_subtreemass[pelvis[p]] * d2.subtree_linvel[pelvis[p]] for p in pre)

    def step(m_, d, vmax):
        hp = {p: d.geom_xpos[hands[p]].copy() for p in pre}
        P0 = momentum(d)
        kick = d.xfrc_applied[:, :3].sum(0).copy()   # the slap impulse's force this step (world)
        t = time.perf_counter()
        real(m_, d, vmax)
        out["t_step"] += time.perf_counter() - t
        out["steps"] += 1
        # the external force on the two athletes this step: gravity, the floor/podium contacts, xfrc (the kick)
        F = kick + m.opt.gravity * sum(m.body_subtreemass[pelvis[p]] for p in pre)
        now = set()
        for ci in range(d.ncon):
            c = d.contact[ci]
            g1, g2 = int(c.geom[0]), int(c.geom[1])
            if g1 in world or g2 in world:
                mujoco.mj_contactForce(m, d, ci, f6)
                fw = np.asarray(c.frame).reshape(3, 3).T @ f6[:3]   # on geom2, from geom1 (normal 1 -> 2)
                F = F + (fw if g1 in world else -fw)
            for s, o in (pre, pre[::-1]):
                if (g1 in hands[s] and g2 in face[o]) or (g2 in hands[s] and g1 in face[o]):
                    key = (min(g1, g2), max(g1, g2))
                    now.add(key)
                    if key not in st["touching"]:
                        out["first_pen"].append(max(0.0, -float(c.dist)))
                        h = g1 if g1 in hands[s] else g2
                        out["approach"].append(float(np.linalg.norm(d.geom_xpos[h] - hp[s][hands[s].index(h)]) / m.opt.timestep))
        # a sweep through a face shape with no contact in this step (tunnelling)
        for s, o in (pre, pre[::-1]):
            for k, h in enumerate(hands[s]):
                p0, p1 = hp[s][k], d.geom_xpos[h]
                if np.linalg.norm(p1 - p0) < 0.02:
                    continue
                for g in face[o]:
                    r = m.geom_size[g][0] + HAND_HALF
                    if m.geom_type[g] == mujoco.mjtGeom.mjGEOM_SPHERE:
                        a = b = d.geom_xpos[g]
                    else:
                        ax = d.geom_xmat[g].reshape(3, 3)[:, 2] * m.geom_size[g][1]
                        a, b = d.geom_xpos[g] - ax, d.geom_xpos[g] + ax
                    if seg_dist(p0, a, b) > r and seg_dist(p1, a, b) > r and seg_seg(p0, p1, a, b) < r - 0.005:
                        out["sweeps"] += 1
                        if not any(g in key and h in key for key in now):
                            out["tunnel"] += 1
        st["touching"] = now
        P1 = momentum(d)
        res = (P1 - P0) - F * m.opt.timestep
        if np.abs(kick).max() > 0:
            out["kicks"].append(dict(J=float(np.linalg.norm(kick) * m.opt.timestep), resid=float(np.linalg.norm(res))))
        out["resid"].append(float(np.linalg.norm(res)))

    match.muscle_step = step
    res, turns, log = match.play(str(BRAINS / f"{rb}.onnx"), i % 2, blue=str(BRAINS / f"{bb}.onnx"), character=rc,
                                 blue_character=bc, seed=i, jitter=match.EXAM_JITTER, log=True)
    ev = [json.loads(l) for l in Path(log).read_text().splitlines() if l.strip()]
    Path(log).unlink()
    out["head"] = [(e["peak_g"], e["peak_rad_s2"]) for e in ev if e["type"] == "head"]
    out["snap"] = [(e["speed"], e["head_peak"]) for e in ev if e["type"] == "strike_result"]
    out["resid"] = float(np.percentile(out["resid"], 99)) if out["resid"] else 0.0
    return out


def strikes(a):
    with Pool(min(a.workers, a.matches)) as p:
        outs = p.map(one_strikes, [(a.physics, a.pairing, i) for i in range(a.matches)])
    cat = lambda k: np.concatenate([np.asarray(o[k], float) for o in outs if len(o[k])]) if any(len(o[k]) for o in outs) else np.zeros(0)
    fp, ap = cat("first_pen") * 1000, cat("approach")
    q = lambda x, p: round(float(np.percentile(x, p)), 2) if len(x) else None
    kicks = [k for o in outs for k in o["kicks"]]
    head = np.array([h for o in outs for h in o["head"]]) if any(o["head"] for o in outs) else np.zeros((0, 2))
    snap = np.array([s for o in outs for s in o["snap"]]) if any(o["snap"] for o in outs) else np.zeros((0, 2))
    steps = sum(o["steps"] for o in outs)
    r = dict(physics=a.physics, pairing=a.pairing, matches=a.matches, steps=steps,
             us_per_step=round(1e6 * sum(o["t_step"] for o in outs) / max(1, steps), 1),
             first_contact_pen_mm=dict(n=len(fp), median=q(fp, 50), p90=q(fp, 90), max=q(fp, 100), over_15mm_pct=round(100 * float((fp > 15).mean()), 1) if len(fp) else None),
             approach_mps=dict(median=q(ap, 50), max=q(ap, 100)),
             sweeps_through_face=sum(o["sweeps"] for o in outs), tunnelled=sum(o["tunnel"] for o in outs),
             momentum_resid_Ns_p99_per_step=round(max(o["resid"] for o in outs), 4),
             kick_J_Ns=dict(n=len(kicks), median=q([k["J"] for k in kicks], 50), max=q([k["J"] for k in kicks], 100)),
             head_g=dict(n=len(head), median=q(head[:, 0], 50), p90=q(head[:, 0], 90)),
             head_rad_s2=dict(median=q(head[:, 1], 50), p90=q(head[:, 1], 90)),
             strike_speed=dict(median=q(snap[:, 0], 50)), head_peak_mps=dict(median=q(snap[:, 1], 50), p90=q(snap[:, 1], 90)))
    print(json.dumps(r))
    (HERE / "runs" / "exam_v3" / f"audit_p{a.physics}_{a.pairing}.json").write_text(json.dumps(r, indent=1))


def record_ctrl(phys, pairing, seed=0):
    """One match's per-step ctrl stream and its start state (open loop replays it on any model of the same layout)."""
    os.environ["SLAP_PHYSICS"] = str(phys)
    import importlib, constants, match
    importlib.reload(constants); importlib.reload(match)
    from brain_cpu import muscle_step as real
    rb, bb, rc, bc = PAIR[pairing]
    rec = dict(ctrl=[], qpos0=None, qvel0=None, act0=None)

    def step(m, d, vmax):
        if rec["qpos0"] is None:
            rec.update(qpos0=d.qpos.copy(), qvel0=d.qvel.copy(), act0=d.act.copy(), vmax=vmax)
        if len(rec["ctrl"]) < 1000:   # 5 s
            rec["ctrl"].append((d.ctrl.copy(), d.xfrc_applied.copy()))
        real(m, d, vmax)
    match.muscle_step = step
    match.play(str(BRAINS / f"{rb}.onnx"), 0, blue=str(BRAINS / f"{bb}.onnx"), character=rc, blue_character=bc, seed=seed,
               jitter=match.EXAM_JITTER, log=False)
    return rec


def replay(m, rec, n=1000):
    import mujoco
    from brain_cpu import muscle_step as real
    d = mujoco.MjData(m)
    d.qpos[:], d.qvel[:], d.act[:] = rec["qpos0"], rec["qvel0"], rec["act0"]
    mujoco.mj_forward(m, d)
    t = time.perf_counter()
    xs = []
    for k in range(min(n, len(rec["ctrl"]))):
        d.ctrl[:], d.xfrc_applied[:] = rec["ctrl"][k]
        real(m, d, rec["vmax"])
        xs.append(d.xpos.copy())
    return (time.perf_counter() - t) / max(1, k + 1), np.array(xs)


def cost(a):
    import mujoco
    import constants
    rb, bb, rc, bc = PAIR[a.pairing]
    rows = []
    for phys in (1, 2, 3):
        rec = record_ctrl(phys, a.pairing)
        path = constants.models_dir(phys) / f"{rc}_vs_{bc}_arena.xml"
        base = mujoco.MjModel.from_xml_path(str(path))
        variants = {"as built": lambda m: None}
        if phys == 3:
            variants.update({
                "nativeccd off": lambda m: setattr(m.opt, "disableflags", m.opt.disableflags | mujoco.mjtDisableBit.mjDSBL_NATIVECCD),
                "multiccd off": lambda m: setattr(m.opt, "disableflags", m.opt.disableflags | mujoco.mjtDisableBit.mjDSBL_MULTICCD),
                "island off": lambda m: setattr(m.opt, "disableflags", m.opt.disableflags | mujoco.mjtDisableBit.mjDSBL_ISLAND),
                "elliptic cones": lambda m: setattr(m.opt, "cone", mujoco.mjtCone.mjCONE_ELLIPTIC),
                "tolerance 1e-6": lambda m: setattr(m.opt, "tolerance", 1e-6),
                "iterations 10": lambda m: setattr(m.opt, "iterations", 10),
                "ls_iterations 25": lambda m: setattr(m.opt, "ls_iterations", 25),
                "noslip 2": lambda m: setattr(m.opt, "noslip_iterations", 2),
            })
        ref = None
        for name, fn in variants.items():
            m = mujoco.MjModel.from_xml_path(str(path))
            fn(m)
            best = 9.0
            for _ in range(3):
                dt, xs = replay(m, rec)
                best = min(best, dt)
            if ref is None:
                ref = xs
            dev = float(np.abs(xs - ref).max()) * 1000
            rows.append(dict(physics=phys, variant=name, us_per_step=round(best * 1e6, 1), max_body_dev_mm_5s=round(dev, 3),
                             npair=m.npair, ngeom=m.ngeom))
            print(json.dumps(rows[-1]), flush=True)
    (HERE / "runs" / "exam_v3" / f"audit_cost_{a.pairing}.json").write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("strikes", "cost"))
    ap.add_argument("--physics", type=int, default=3)
    ap.add_argument("--pairing", default="gk", choices=list(PAIR))
    ap.add_argument("--matches", type=int, default=6)
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    (HERE / "runs" / "exam_v3").mkdir(parents=True, exist_ok=True)
    strikes(a) if a.cmd == "strikes" else cost(a)
