"""Collision review: real matches (training/match.py's own loop, CPU MuJoCo), every contact measured per pair class.

    training/.venv/Scripts/python.exe Scripts/collision_review.py --physics 1 --pairing gk --matches 6 [--workers 6]

Reads the models, never changes them (match.muscle_step is wrapped to look at d.contact after each physics step).
Pair classes: palm_head (a hand box vs the other athlete's head), other_head (any other part vs that head),
body_body (the two athletes' other parts: bumps), self (one athlete's own non-adjacent parts), sole_floor,
body_floor (non-sole on the floor), hand_podium, body_podium (non-hand, non-sole on the podium), sole_podium.

Per class: penetration (median / p90 / max over contact-steps, mm), contact duration (consecutive steps per pair,
ms), relative speed along the normal at first touch and just after separation (the restitution e = out / in), slip
speed while in contact and the friction use |f_t| / (mu f_n). Per match: the largest kinetic-energy jump in one step
outside the slap's impulse, the largest joint speed. The palm-head contact steps also keep the bodies' poses
(Scripts/collision_mesh_clip.py turns them into the drawn mesh's clipping). Output: JSON on stdout and
Saved/collision_review/<physics>_<pairing>.json (+ .npz with the poses).
"""
import argparse
import json
import os
import sys
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRAIN = ROOT / "training"
BRAINS = TRAIN / "models" / "brains"
PAIRINGS = {   # red brain, blue brain, red character, blue character (the shipped brains, as the site plays them)
    "gg": ("grandpa_final", "grandpa_final", "grandpa", "grandpa"),
    "gk": ("grandpa_final_vs_kim", "kim_final_vs_grandpa", "grandpa", "kim"),
    "kk": ("kim_final", "kim_final", "kim", "kim"),
}
CLASSES = ("palm_head", "other_head", "body_body", "self", "sole_floor", "body_floor", "hand_podium", "body_podium",
           "sole_podium")


def one(args):
    phys, pairing, i = args
    os.environ["SLAP_PHYSICS"] = str(phys)
    sys.path.insert(0, str(TRAIN))
    os.chdir(TRAIN)
    import numpy as np
    import mujoco
    import match
    from brain_cpu import muscle_step as real

    rb, bb, rc, bc = PAIRINGS[pairing]
    st = {c: dict(pen=[], dur=[], vin=[], vout=[], slip=[], fuse=[]) for c in CLASSES}
    live = {}        # (g1, g2) -> [class, steps, max pen, normal, vn at touch]
    ended = []       # pairs whose contact ended last step: measure the separation speed now
    poses = []       # palm-head contact steps: striker hand bodies + defender head body
    meta = dict(ke_jump=0.0, qvel_max=0.0, steps=0, kick_steps=0, deep=[], parts={})   # parts: class:a-b -> [n, max pen]
    cache = {}

    def info(m, g):
        if g in cache:
            return cache[g]
        n = m.geom(g).name
        side = "red" if "red_" in n else "blue" if "blue_" in n else None
        part = n.split(f"{side}_", 1)[1] if side else n
        cache[g] = (side, part, m.geom_bodyid[g])
        return cache[g]

    def klass(m, g1, g2):
        (s1, p1, _), (s2, p2, _) = info(m, g1), info(m, g2)
        if s1 is None or s2 is None:
            env, (s, p) = (p1, (s2, p2)) if s1 is None else (p2, (s1, p1))
            if s is None:
                return None
            sole = p.startswith("foot_")
            hand = p.startswith("hand_")
            if env == "floor":
                return "sole_floor" if sole else "body_floor"
            return "sole_podium" if sole else "hand_podium" if hand else "body_podium"
        if s1 == s2:
            return "self"
        if p1 == "head" or p2 == "head":
            other = p2 if p1 == "head" else p1
            return "palm_head" if other.startswith("hand_") else "other_head"
        return "body_body"

    res6 = np.zeros(6)

    def pvel(m, d, g, p):   # world velocity of geom g's body at point p
        mujoco.mj_objectVelocity(m, d, mujoco.mjtObj.mjOBJ_GEOM, g, res6, 0)
        return res6[3:] + np.cross(res6[:3], p - d.geom_xpos[g])

    def relv(m, d, g1, g2, p):
        return pvel(m, d, g1, p) - pvel(m, d, g2, p)

    f6 = np.zeros(6)
    state = dict(ke=None)

    def step(m, d, vmax):
        real(m, d, vmax)
        meta["steps"] += 1
        kick = bool(np.abs(d.xfrc_applied).max() > 0)
        meta["kick_steps"] += kick
        mujoco.mj_energyVel(m, d)
        ke = float(d.energy[1])
        if state["ke"] is not None and not kick:
            meta["ke_jump"] = max(meta["ke_jump"], ke - state["ke"])
        state["ke"] = ke
        meta["qvel_max"] = max(meta["qvel_max"], float(np.abs(d.qvel[6:]).max()) if m.nv > 6 else 0.0)
        for key, c, n, *_ in ended:
            g1, g2 = key
            p = 0.5 * (d.geom_xpos[g1] + d.geom_xpos[g2])
            st[c]["vout"].append(float(relv(m, d, g1, g2, p) @ n))
        ended.clear()
        seen = set()
        palm_now = False
        for ci in range(d.ncon):
            con = d.contact[ci]
            g1, g2 = int(con.geom[0]), int(con.geom[1])
            c = klass(m, g1, g2)
            if c is None:
                continue
            pen = max(0.0, -float(con.dist))
            if pen <= 0.0 and c not in ("palm_head",):
                continue   # inside the margin, not touching
            if pen <= 0.0:
                continue
            key = (g1, g2)
            seen.add(key)
            S = st[c]
            S["pen"].append(pen)
            n = np.array(con.frame[:3])
            v = relv(m, d, g1, g2, np.array(con.pos))
            vn = float(v @ n)
            if key not in live:
                live[key] = [c, 0, 0.0, n, vn]
                S["vin"].append(vn)
            L = live[key]
            L[1] += 1
            L[2] = max(L[2], pen)
            L[3] = n
            if c in ("sole_floor", "sole_podium", "hand_podium", "body_floor", "body_body"):
                S["slip"].append(float(np.linalg.norm(v - vn * n)))
                mujoco.mj_contactForce(m, d, ci, f6)
                if f6[0] > 1e-6:
                    S["fuse"].append(float(np.hypot(f6[1], f6[2]) / (con.friction[0] * f6[0])))
            if c == "palm_head":
                palm_now = True
            if c in ("body_body", "other_head", "self", "body_floor", "body_podium"):
                pk = f"{c}:" + "-".join(sorted((info(m, g1)[1], info(m, g2)[1])))
                e = meta["parts"].setdefault(pk, [0, 0.0])
                e[0] += 1
                e[1] = max(e[1], pen)
            if pen > 0.05:
                meta["deep"].append((c, info(m, g1)[1], info(m, g2)[1], round(pen * 1000, 1)))
        for key in [k for k in live if k not in seen]:
            c, steps_, pmax, n, _ = live.pop(key)
            st[c]["dur"].append(steps_ * m.opt.timestep)
            ended.append((key, c, n))
        if palm_now:
            rec = []
            for side in ("red", "blue"):
                for part in ("hand_l", "hand_r", "head"):
                    b = m.geom_bodyid[m.geom(f"{pre[side]}{part}").id]
                    rec.append(np.concatenate([d.xpos[b], d.xmat[b]]))
            poses.append(np.array(rec))

    _, _, prefixes = match.load(None, rc, bc)
    pre = {"red": prefixes[0], "blue": prefixes[1]}
    match.muscle_step = step
    res, turns, _ = match.play(str(BRAINS / f"{rb}.onnx"), i % 2, blue=str(BRAINS / f"{bb}.onnx"), character=rc,
                               blue_character=bc, seed=i, jitter=match.EXAM_JITTER, log=False)
    strikes = sum(1 for t in turns if t.get("touch"))
    return dict(st=st, meta=meta, poses=np.array(poses) if poses else np.zeros((0, 6, 12)), strikes=strikes,
                turns=len(turns), how=res.get("how") if isinstance(res, dict) else str(res))


def summary(outs, dt=0.005):
    import numpy as np
    out = {}
    for c in CLASSES:
        P = np.concatenate([o["st"][c]["pen"] for o in outs]) * 1000 if any(o["st"][c]["pen"] for o in outs) else np.zeros(0)
        D = np.concatenate([o["st"][c]["dur"] for o in outs]) * 1000 if any(o["st"][c]["dur"] for o in outs) else np.zeros(0)
        vin = np.concatenate([o["st"][c]["vin"] for o in outs]) if any(o["st"][c]["vin"] for o in outs) else np.zeros(0)
        vout = np.concatenate([o["st"][c]["vout"] for o in outs]) if any(o["st"][c]["vout"] for o in outs) else np.zeros(0)
        slip = np.concatenate([o["st"][c]["slip"] for o in outs]) if any(o["st"][c]["slip"] for o in outs) else np.zeros(0)
        fuse = np.concatenate([o["st"][c]["fuse"] for o in outs]) if any(o["st"][c]["fuse"] for o in outs) else np.zeros(0)
        q = lambda a, p: round(float(np.percentile(a, p)), 2) if len(a) else None
        row = dict(contact_steps=int(len(P)), contacts=int(len(D)),
                   pen_mm=dict(median=q(P, 50), p90=q(P, 90), max=q(P, 100)),
                   dur_ms=dict(median=q(D, 50), p90=q(D, 90), max=q(D, 100)))
        # restitution on the contacts that really closed in (approach faster than 0.3 m/s)
        k = min(len(vin), len(vout))
        if k:
            # the frame's normal points from geom1 to geom2 and v = v1 - v2: closing in is v.n > 0, parting v.n < 0
            a, b = vin[:k], -vout[:k]
            sel = a > 0.3
            if sel.any():
                e = b[sel] / a[sel]
                row["approach_mps"] = dict(median=q(a[sel], 50), max=q(a[sel], 100))
                row["restitution"] = dict(median=q(e, 50), p90=q(e, 90), n=int(sel.sum()))
        if len(slip):
            row["slip_mps"] = dict(median=q(slip, 50), p90=q(slip, 90), max=q(slip, 100))
        if len(fuse):
            row["friction_use"] = dict(median=q(fuse, 50), p90=q(fuse, 90), saturated_pct=round(100 * float((fuse > 0.98).mean()), 1))
        out[c] = row
    out["_match"] = dict(matches=len(outs), turns=sum(o["turns"] for o in outs), strikes=sum(o["strikes"] for o in outs),
                         ke_jump_J_max=round(max(o["meta"]["ke_jump"] for o in outs), 1),
                         qvel_max=round(max(o["meta"]["qvel_max"] for o in outs), 1),
                         deep_over_50mm=[x for o in outs for x in o["meta"]["deep"]][:20],
                         parts=dict(sorted(((k, [sum(o["meta"]["parts"].get(k, [0, 0])[0] for o in outs),
                                                 round(1000 * max(o["meta"]["parts"].get(k, [0, 0])[1] for o in outs), 1)])
                                           for k in {k for o in outs for k in o["meta"]["parts"]}), key=lambda kv: -kv[1][1])[:25]),
                         steps=sum(o["meta"]["steps"] for o in outs))
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--physics", type=int, default=1)
    ap.add_argument("--pairing", default="gk", choices=list(PAIRINGS))
    ap.add_argument("--matches", type=int, default=6)
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    with Pool(min(a.workers, a.matches)) as p:
        outs = p.map(one, [(a.physics, a.pairing, i) for i in range(a.matches)])
    import numpy as np
    s = summary(outs)
    s["_match"].update(physics=a.physics, pairing=a.pairing)
    dst = ROOT / "Saved" / "collision_review"
    dst.mkdir(parents=True, exist_ok=True)
    (dst / f"p{a.physics}_{a.pairing}.json").write_text(json.dumps(s, indent=1))
    np.savez_compressed(dst / f"p{a.physics}_{a.pairing}_poses.npz", poses=np.concatenate([o["poses"] for o in outs]))
    print(json.dumps(s))
