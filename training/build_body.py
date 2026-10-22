"""Character card + Blender skeleton -> MuJoCo athlete, solo scene and arena, with the Phase A checks.

    .venv/Scripts/python.exe build_body.py characters/grandpa.json [--blue characters/other.json]

Writes models/<name>_solo.xml, models/<red>_vs_<blue>_arena.xml and models/<name>_fingerprint.json.
Fails loudly on: a bad rig (A8), self-overlap that shrinking can't fix (A10), a model that explodes (A11),
or the wrong MuJoCo version (A12).

Frames: Blender is Z-up, metres, character faces -Y. MuJoCo here is Z-up, metres, athlete faces +X, left = +Y.
    mujoco (x, y, z) = blender (-y, x, z)
Body frames are world-aligned in the rest pose (symmetric T-pose), body origin = bone head, so bone == body.
"""
import argparse, hashlib, json, math, sys
from pathlib import Path

import mujoco
import numpy as np

HERE = Path(__file__).parent
MUJOCO_PIN = "3.10.0"  # URLab's MuJoCo; trainer and Unreal must step identical physics

PARENT = {"pelvis": None, "belly": "pelvis", "chest": "belly", "neck": "chest", "head": "neck",
          "upperarm_l": "chest", "forearm_l": "upperarm_l", "hand_l": "forearm_l",
          "upperarm_r": "chest", "forearm_r": "upperarm_r", "hand_r": "forearm_r",
          "thigh_l": "pelvis", "shin_l": "thigh_l", "foot_l": "shin_l",
          "thigh_r": "pelvis", "shin_r": "thigh_r", "foot_r": "shin_r"}
# % of body mass per segment (Blueprint 2.2)
MASS = {"pelvis": 14.2, "belly": 13.9, "chest": 21.6, "neck": 1.2, "head": 6.9, "upperarm": 2.8,
        "forearm": 1.6, "hand": 0.6, "thigh": 10.0, "shin": 4.65, "foot": 1.45}
# segment: [(joint, axis on the LEFT side / centre, range deg, trained-adult torque Nm)]  (Blueprint 2.3, 30 motors)
# axes in the athlete frame (+X forward, +Y left, +Z up); positive = flex / forward / up / outward
JOINTS = {
    "belly":    [("waist_twist", (0, 0, 1), (-40, 40), 120), ("waist_flex", (0, 1, 0), (-20, 60), 200),
                 ("waist_side", (1, 0, 0), (-30, 30), 160)],
    "neck":     [("neck_turn", (0, 0, 1), (-70, 70), 40), ("neck_nod", (0, 1, 0), (-40, 50), 40),
                 ("neck_tilt", (1, 0, 0), (-40, 40), 40)],
    "upperarm": [("shoulder_swing", (0, 0, 1), (-130, 40), 90), ("shoulder_raise", (1, 0, 0), (-95, 80), 90),
                 ("shoulder_twist", (0, 1, 0), (-80, 80), 60)],
    "forearm":  [("elbow", (0, 0, -1), (0, 140), 70)],
    "hand":     [("wrist_flex", (-1, 0, 0), (-60, 70), 20), ("wrist_dev", (0, 0, 1), (-25, 25), 15)],
    "thigh":    [("hip_flex", (0, -1, 0), (-20, 110), 220), ("hip_abd", (1, 0, 0), (-25, 40), 160),
                 ("hip_rot", (0, 0, 1), (-40, 40), 100)],
    "shin":     [("knee", (0, 1, 0), (0, 140), 220)],
    "foot":     [("ankle_flex", (0, -1, 0), (-45, 20), 180), ("ankle_roll", (1, 0, 0), (-20, 20), 60)],
}
KP_PER_NM = 1 / 0.35   # full torque at 0.35 rad error
KV_RATIO = 0.05        # damping = 5 % of stiffness
ADULT_JOINT_SPEED = 12.0  # rad/s, scaled by card speed; enforced by the env, recorded here
LENGTH_RATIO = {"thigh": (0.21, 0.28), "shin": (0.19, 0.27), "upperarm": (0.13, 0.20), "forearm": (0.10, 0.17)}


def b2m(p):
    return np.array([-p[1], p[0], p[2]], float)


def mirror(v):  # left <-> right in the athlete frame
    return np.array([v[0], -v[1], v[2]])


def seg(name):
    return name.rsplit("_", 1)[0] if name.endswith(("_l", "_r")) else name


def fmt(v):
    """Numbers as the exact value of their float32: URLab stores floats, so this round-trips bit for bit."""
    return " ".join(repr(float(np.float32(x))) for x in np.atleast_1d(v))


def quat_z_to(d):
    """Quaternion turning +Z onto direction d (capsule axis)."""
    d = np.asarray(d, float) / np.linalg.norm(d)
    ax = np.cross([0, 0, 1], d)
    s = np.linalg.norm(ax)
    if s < 1e-9:
        return np.array([1.0, 0, 0, 0]) if d[2] > 0 else np.array([0.0, 1, 0, 0])
    a = math.atan2(s, d[2])
    return np.array([math.cos(a / 2), *(ax / s * math.sin(a / 2))])


def quat_axis(axis, deg):
    t = math.radians(deg) / 2
    return np.array([math.cos(t), *(np.array(axis, float) * math.sin(t))])


# ---------------------------------------------------------------- A8: rig validation
def validate(sk, card):
    B, H, err = sk["bones"], card["height_m"], []
    missing = set(PARENT) - set(B)
    if missing:
        sys.exit(f"RIG FAIL: missing bones {sorted(missing)}")
    for n, p in PARENT.items():
        if B[n]["parent"] != p:
            err.append(f"{n} parent is {B[n]['parent']}, expected {p}")
    top = max(max(b["head"][2], b["tail"][2]) for b in B.values())
    low = min(min(b["head"][2], b["tail"][2]) for b in B.values())
    if abs(top - H) > 0.05 * H:
        err.append(f"top of head at {top:.3f} m, card height {H} m (scale or Z-up wrong)")
    if not -0.01 <= low <= 0.06:
        err.append(f"lowest bone point at z={low:.3f} m, feet should be on Z=0")
    for s in "lr":
        f = B["foot_" + s]
        if f["tail"][1] >= f["head"][1]:
            err.append(f"foot_{s} points +Y; character must face -Y in Blender")
    for n in PARENT:
        if n.endswith("_l"):
            r = n[:-2] + "_r"
            for k in ("head",) if n.startswith("hand") else ("head", "tail"):  # fingertips depend on curl
                d = np.linalg.norm(np.array(B[n][k]) - np.array(B[r][k]) * [-1, 1, 1])
                if d > 0.03:
                    err.append(f"{n}/{r} {k} asymmetric by {d * 100:.1f} cm")
    for s, (lo, hi) in LENGTH_RATIO.items():
        L = np.linalg.norm(np.array(B[s + "_l"]["tail"]) - B[s + "_l"]["head"]) / H
        if not lo <= L <= hi:
            err.append(f"{s} length {L:.3f} x height outside human range {lo}-{hi}")
    print(f"A8 rig: {len(B)} bones, top {top:.3f} m, feet {low:.3f} m, faces -Y, "
          f"{'OK' if not err else 'FAIL'}")
    if err:
        sys.exit("RIG FAIL:\n  " + "\n  ".join(err))


# ---------------------------------------------------------------- geometry from rig + mesh stats
def symmetric_skeleton(sk):
    """Bone heads/tails in the athlete frame, centre bones on y=0, left/right averaged."""
    B, out = sk["bones"], {}
    for n in PARENT:
        h, t = b2m(B[n]["head"]), b2m(B[n]["tail"])
        if n.endswith("_l"):
            r = n[:-2] + "_r"
            h = (h + mirror(b2m(B[r]["head"]))) / 2
            t = (t + mirror(b2m(B[r]["tail"]))) / 2
            out[r] = (mirror(h), mirror(t))
        elif not n.endswith("_r"):
            h[1] = t[1] = 0.0
        if not n.endswith("_r"):
            out[n] = (h, t)
    return out


def torso_band(sk, z0, z1):
    S = [s for s in sk["torso_slices"] if z0 - 1e-6 <= s["z"] <= z1 + 1e-6]
    width = float(np.median([s["x"][1] - s["x"][0] for s in S]))
    depth = float(np.median([s["y"][1] - s["y"][0] for s in S]))
    fwd = float(np.median([-(s["y"][0] + s["y"][1]) / 2 for s in S]))  # blender -y = forward
    return width, depth, fwd


def athlete_geometry(sk, card):
    """Per body: origin (athlete frame), geoms (in body frame), mass. Shapes sit inside the mesh."""
    J, M, st = symmetric_skeleton(sk), card["mass_kg"], {n: b["mesh"] for n, b in sk["bones"].items()}
    geo = {}

    def add(n, *geoms):
        geo[n] = {"origin": J[n][0], "geoms": list(geoms), "mass": M * MASS[seg(n)] / 100}

    for n, band, zc in (("pelvis", (0.85, 0.95), None), ("belly", (1.0, 1.1), None), ("chest", (1.15, 1.3), "mid")):
        w, d, fwd = torso_band(sk, *band)
        r = 0.46 * d
        half = max(0.01, 0.46 * w - r)
        z = (J[n][0][2] + J[n][1][2]) / 2 if zc else (band[0] + band[1]) / 2
        c = np.array([fwd, 0.0, z]) - J[n][0]
        add(n, ("capsule", n, c - [0, half, 0], c + [0, half, 0], r))
    h, t = J["neck"]
    add("neck", ("capsule", "neck", np.zeros(3), t - h, 0.027 * card["height_m"]))
    h, t = J["head"]
    hy = st["head"]["y"]
    c = (h + t) / 2 + [-(hy[0] + hy[1]) / 2, 0, 0] - h
    add("head", ("sphere", "head", c, None, 0.92 * st["head"]["r_med"]))
    for s, sg in (("l", 1), ("r", -1)):
        h, t = J["upperarm_" + s]
        add("upperarm_" + s, ("capsule", "upperarm_" + s, np.zeros(3), t - h,
                              0.9 * min(st["upperarm_l"]["r_med"], st["upperarm_r"]["r_med"])))
        h, t = J["forearm_" + s]
        add("forearm_" + s, ("capsule", "forearm_" + s, np.zeros(3), t - h,
                             0.9 * min(st["forearm_l"]["r_med"], st["forearm_r"]["r_med"])))
        h, t = J["hand_" + s]
        u = (t - h) / np.linalg.norm(t - h)
        L = 0.108 * card["height_m"]  # anthropometric hand length; the scan's left hand is a fist
        tilt = math.degrees(math.atan2(u[2], abs(u[1])))  # arm points slightly down in the rest pose
        add("hand_" + s, ("box", "hand_" + s, u * L / 2, (0.042, L / 2, 0.015), (sg * tilt, 0, 0)))
        h, t = J["thigh_" + s]
        u = (t - h) / np.linalg.norm(t - h)
        add("thigh_" + s, ("capsule", "thigh_" + s, u * 0.12, t - h - u * 0.02, 0.85 * st["thigh_l"]["r_med"]))
        h, t = J["shin_" + s]
        u = (t - h) / np.linalg.norm(t - h)
        add("shin_" + s, ("capsule", "shin_" + s, u * 0.02, t - h - u * 0.05,
                          0.85 * min(st["shin_l"]["r_med"], st["shin_r"]["r_med"])))
        h, t = J["foot_" + s]
        d = (t - h)[:2] / np.linalg.norm((t - h)[:2])
        fy = st["foot_l"]["y"]  # heel/toe along blender y; left foot measured, mirrored for right
        heel, toe = fy[1] - sk["bones"]["foot_l"]["head"][1], sk["bones"]["foot_l"]["head"][1] - fy[0]
        half = (heel + toe) / 2
        c = np.array([*(d * (toe - half)), -h[2] + 0.03])  # box bottom on the floor in the rest pose
        add("foot_" + s, ("box", "foot_" + s, c, (half, 0.05, 0.03), (0, 0, math.degrees(math.atan2(d[1], d[0])))))
    return geo


# ---------------------------------------------------------------- MJCF text
def geom_xml(g, mass, pre, scale):
    kind, name, a, b, r = g
    k = scale.get(name, 1.0)
    full = pre + name
    # explicit pos/quat/size (no fromto/euler): nothing left for URLab to recompute in single precision
    if kind == "capsule":
        a, b = np.asarray(a, float), np.asarray(b, float)
        return (f'<geom name="{full}" type="capsule" pos="{fmt((a + b) / 2)}" quat="{fmt(quat_z_to(b - a))}" '
                f'size="{fmt([r * k, np.linalg.norm(b - a) / 2])}" mass="{fmt(mass)}"/>')
    if kind == "sphere":
        return f'<geom name="{full}" type="sphere" pos="{fmt(a)}" size="{fmt(r * k)}" mass="{fmt(mass)}"/>'
    ex, _, ez = r  # boxes turn about one axis only (hand: x, foot: z)
    q = quat_axis((1, 0, 0), ex) if ex else quat_axis((0, 0, 1), ez)
    return (f'<geom name="{full}" type="box" pos="{fmt(a)}" quat="{fmt(q)}" size="{fmt(np.array(b) * k)}" '
            f'mass="{fmt(mass)}"/>')


def athlete_xml(geo, card, pre, pos, quat, scale, rgba):
    S = card["strength"]
    flex = card["flexibility"]
    kids = {n: [c for c, p in PARENT.items() if p == n] for n in PARENT}
    acts = []

    def body(n, depth):
        g = geo[n]
        rel = g["origin"] - (geo[PARENT[n]]["origin"] if PARENT[n] else 0)
        ind = "  " * depth
        out = [f'{ind}<body name="{pre}{n}" pos="{fmt(pos if not PARENT[n] else rel)}"'
               + (f' quat="{fmt(quat)}"' if not PARENT[n] else "") + ">"]
        if not PARENT[n]:
            out.append(f'{ind}  <joint name="{pre}root" type="free" damping="0" armature="0"/>')  # explicit: URLab applies <default> to free joints, MuJoCo's <freejoint> doesn't
        for jn, ax, (lo, hi), tq in JOINTS.get(seg(n), []):
            side = n[-2:] if n.endswith(("_l", "_r")) else ""
            axis = np.array(ax) * ([-1, 1, -1] if side == "_r" else 1)  # rotation axes mirror as (-x, y, -z)
            j = f"{pre}{jn}{side}"
            rng = fmt([math.radians(lo * flex), math.radians(hi * flex)])
            out.append(f'{ind}  <joint name="{j}" axis="{fmt(axis)}" range="{rng}"/>')
            st = S * card.get("toughness", 1.0) if seg(n) == "neck" else S   # toughness: neck bracing (KO resistance)
            kp = tq * st * KP_PER_NM
            acts.append(f'<position name="{j}" joint="{j}" kp="{fmt(kp)}" kv="{fmt(kp * KV_RATIO)}" '
                        f'ctrlrange="{rng}" forcerange="{fmt([-tq * st, tq * st])}"/>')
        for gg in g["geoms"]:
            out.append(f"{ind}  " + geom_xml(gg, g["mass"] / len(g["geoms"]), pre, scale).replace("/>", f' rgba="{rgba}"/>'))
        if n == "head":
            c = g["geoms"][0][2]
            out.append(f'{ind}  <site name="{pre}face_zone" type="box" pos="{fmt(c + [0.04, 0, -0.02])}" '
                       f'size="{fmt([0.06, 0.09, 0.06])}" rgba="1 1 0 0.2"/>')
        for c in kids[n]:
            out += body(c, depth + 1)
        out.append(f"{ind}</body>")
        return out

    return "\n".join(body("pelvis", 2)), acts


HEADER = f"""<mujoco model="{{model}}">
  <compiler angle="radian" autolimits="true"/>
  <option timestep="0.005" integrator="implicitfast"/>
  <default>
    <joint armature="{fmt(0.02)}" damping="0.5"/>
    <geom condim="3" friction="{fmt([0.8, 0.02, 0.01])}"/>
    <position ctrllimited="true" forcelimited="true"/>
  </default>
  <worldbody>
    <light pos="0 0 4" dir="0 0 -1" diffuse="0.8 0.8 0.8"/>
    <geom name="floor" type="plane" size="10 10 0.1" friction="{fmt([0.9, 0.02, 0.01])}" priority="1" rgba="0.25 0.25 0.28 1"/>
"""


def scene_xml(model, parts, extra_world=""):
    bodies = "\n".join(p[0] for p in parts)
    acts = "\n    ".join(a for p in parts for a in p[1])
    return (HEADER.format(model=model) + extra_world + bodies + "\n  </worldbody>\n  <actuator>\n    " + acts
            + "\n  </actuator>\n</mujoco>\n")


# ---------------------------------------------------------------- poses and checks
def set_pose(m, d, pose, pre=""):
    for j, deg in pose.items():
        d.qpos[m.joint(pre + j).qposadr[0]] = math.radians(deg)


def stand_pose(raise_deg):
    p = {}
    for s in ("_l", "_r"):
        p["shoulder_raise" + s] = raise_deg
        p["elbow" + s] = 10
    return p


def self_overlaps(m, d):
    """Pairs of geoms in the same athlete that overlap (> 0.1 mm)."""
    mujoco.mj_forward(m, d)
    bad = set()
    for c in d.contact[: d.ncon]:
        b1, b2 = m.geom_bodyid[c.geom1], m.geom_bodyid[c.geom2]
        if b1 == 0 or b2 == 0 or c.dist > -1e-4:
            continue
        if m.body_rootid[b1] == m.body_rootid[b2]:
            bad.add(tuple(sorted((m.geom(c.geom1).name, m.geom(c.geom2).name))))
    return bad


def swing_poses(base):
    for s in ("_l", "_r"):
        for sw in range(40, -101, -5):
            p = dict(base)
            p.update({"shoulder_raise" + s: 0, "elbow" + s: 20, "shoulder_swing" + s: sw})
            yield f"swing{s} {sw}", p


def fit(geo, card):
    """A10: shrink overlapping shapes until reference pose, stand and swing arcs are overlap-free."""
    scale = {}
    for it in range(25):
        xml = scene_xml("fit", [athlete_xml(geo, card, "", geo["pelvis"]["origin"] + [0, 0, 0.001],
                                             (1, 0, 0, 0), scale, "0.8 0.6 0.5 1")])
        m = mujoco.MjModel.from_xml_string(xml)
        d = mujoco.MjData(m)
        bad = self_overlaps(m, d)                      # reference pose = qpos0
        where = "reference pose"
        stand = None
        if not bad:
            for r in range(-80, -54, 5):                # arms down, abducted just enough to clear the belly
                mujoco.mj_resetData(m, d); set_pose(m, d, stand_pose(r))
                if not self_overlaps(m, d):
                    stand = r
                    break
            if stand is None:
                mujoco.mj_resetData(m, d); set_pose(m, d, stand_pose(-70))
                bad, where = self_overlaps(m, d), "stand"
        if not bad:
            for label, p in swing_poses(stand_pose(stand)):
                mujoco.mj_resetData(m, d); set_pose(m, d, p)
                bad = self_overlaps(m, d)
                if bad:
                    where = label
                    break
        if not bad:
            shrunk = {k: round(v, 3) for k, v in scale.items()}
            print(f"A10 self-collision: clean in reference, stand (arms {stand} deg), swing arcs; "
                  f"shrunk {shrunk or 'nothing'}")
            return scale, stand
        for pair in bad:
            for g in pair:
                scale[g] = scale.get(g, 1.0) * 0.95
        print(f"    overlap at {where}: {sorted(bad)} -> shrink 5 %")
    sys.exit("A10 FAIL: overlaps remain after 25 shrink rounds")


def balance_and_drop(xml, stand):
    m = mujoco.MjModel.from_xml_string(xml)
    d = mujoco.MjData(m)
    set_pose(m, d, stand_pose(stand))
    mujoco.mj_forward(m, d)
    com = d.subtree_com[m.body("pelvis").id]
    feet = (d.geom_xpos[m.geom("foot_l").id] + d.geom_xpos[m.geom("foot_r").id]) / 2
    off = com[:2] - feet[:2]
    print(f"A11 balance: COM {off[0] * 100:+.1f} cm fore/aft, {off[1] * 100:+.1f} cm sideways of mid-feet "
          f"({'OK' if abs(off[0]) < 0.04 and abs(off[1]) < 0.02 else 'OFF-CENTRE'})")
    for j in range(m.nu):
        d.ctrl[j] = d.qpos[m.jnt_qposadr[m.actuator_trnid[j, 0]]]
    up, peak = 0.0, 0.0
    for i in range(int(3.0 / m.opt.timestep)):
        mujoco.mj_step(m, d)
        if not np.all(np.isfinite(d.qpos)):
            sys.exit(f"A11 FAIL: simulation blew up at t={d.time:.3f}s")
        peak = max(peak, float(np.abs(d.qvel).max()))
        if d.qpos[2] > 0.7 * m.body("pelvis").pos[2]:
            up = d.time
    print(f"A11 drop test (PD holding stance, 3 s): no blow-up, peak joint speed {peak:.1f}, "
          f"upright for {up:.2f} s, pelvis z {d.qpos[2]:.3f} m")
    return m, off, up


GAP = 0.85   # pelvis to pelvis (PRD 0.85-1.0 m; shortest so a lean reaches)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("card")
    ap.add_argument("--blue", help="card for the blue athlete (default: same as red)")
    a = ap.parse_args()
    if mujoco.__version__ != MUJOCO_PIN:
        sys.exit(f"A12 FAIL: MuJoCo {mujoco.__version__}, pinned {MUJOCO_PIN} (URLab)")
    out = HERE / "models"
    out.mkdir(exist_ok=True)
    built = {}
    for path in dict.fromkeys([a.card, a.blue or a.card]):
        card = json.loads(Path(path).read_text())
        sk = json.loads((HERE / card["skeleton"]).read_text())
        validate(sk, card)
        geo = athlete_geometry(sk, card)
        scale, stand = fit(geo, card)
        root = geo["pelvis"]["origin"] + [0, 0, 0.001]
        target = '    <body name="target" mocap="true" pos="5 0 1.6"><geom type="sphere" size="0.09" rgba="0.9 0.2 0.2 0.5"/></body>\n'
        pod_h = round(0.59 * card["height_m"], 3)   # the arena's podium where the arena has it (L2's free hand found it)
        target += (f'    <geom name="podium" type="box" pos="{fmt([root[0] + GAP / 2, root[1], pod_h / 2])}" '
                   f'size="{fmt([0.10, 0.30, pod_h / 2])}" rgba="0.15 0.15 0.18 1"/>\n')
        solo = scene_xml(card["name"], [athlete_xml(geo, card, "", root, (1, 0, 0, 0), scale, "0.8 0.6 0.5 1")], target)
        (out / f"{card['name']}_solo.xml").write_text(solo)
        m, off, up = balance_and_drop(solo, stand)
        joints = [m.joint(m.actuator_trnid[i, 0]).name for i in range(m.nu)]
        fp = {"mujoco": mujoco.__version__, "model": f"{card['name']}_solo.xml",
              "sha256": hashlib.sha256(solo.encode()).hexdigest(),
              "option": {"timestep": m.opt.timestep, "integrator": int(m.opt.integrator), "solver": int(m.opt.solver),
                         "iterations": m.opt.iterations, "gravity": m.opt.gravity.tolist()},
              "sizes": {"nq": m.nq, "nv": m.nv, "nu": m.nu, "nbody": m.nbody, "ngeom": m.ngeom},
              "mass_kg": round(float(m.body_subtreemass[m.body("pelvis").id]), 2),
              "body_mass": {m.body(i).name: round(float(m.body_mass[i]), 3) for i in range(1, m.nbody)},
              "control_hz": 50, "physics_hz": 200,
              "joint_order": joints,
              "ctrl_range_rad": m.actuator_ctrlrange.tolist(),
              "torque_limit_nm": m.actuator_forcerange[:, 1].tolist(),
              "max_joint_speed_rad_s": ADULT_JOINT_SPEED * card["speed"],
              "stand_pose_deg": stand_pose(stand), "stand_pose_rad": {k: math.radians(v) for k, v in stand_pose(stand).items()},
              "root_height_m": float(root[2]),
              "geom_scale": scale, "balance_offset_m": off.tolist(), "drop_test_upright_s": up,
              "card": card}
        (out / f"{card['name']}_fingerprint.json").write_text(json.dumps(fp, indent=1))
        built[path] = (card, geo, scale, root)
        print(f"A12 fingerprint: MuJoCo {fp['mujoco']}, {fp['sizes']}, {fp['mass_kg']} kg, sha {fp['sha256'][:12]}")

    # arena: red at -X facing +X, blue at +X facing -X, podium between (Blueprint 5)
    (rc, rg, rs, rr), (bc, bg, bs, br) = built[a.card], built[a.blue or a.card]
    gap = GAP
    pod_h = round(0.59 * min(rc["height_m"], bc["height_m"]), 3)  # below the belly bulge
    world = (f'    <geom name="podium" type="box" pos="0 0 {fmt(pod_h / 2)}" size="{fmt([0.10, 0.30, pod_h / 2])}" rgba="0.15 0.15 0.18 1"/>\n'
             f'    <site name="footbox_red" type="box" pos="{fmt([-gap / 2, 0, 0.001])}" size="{fmt([0.20, 0.25, 0.001])}" rgba="0.8 0.1 0.1 0.4"/>\n'
             f'    <site name="footbox_blue" type="box" pos="{fmt([gap / 2, 0, 0.001])}" size="{fmt([0.20, 0.25, 0.001])}" rgba="0.1 0.2 0.8 0.4"/>\n')
    red = athlete_xml(rg, rc, "red_", [-gap / 2, 0, rr[2]], (1, 0, 0, 0), rs, "0.85 0.3 0.3 1")
    blue = athlete_xml(bg, bc, "blue_", [gap / 2, 0, br[2]], (0, 0, 0, 1), bs, "0.3 0.4 0.85 1")
    arena = scene_xml(f"{rc['name']}_vs_{bc['name']}", [red, blue], world)
    name = f"{rc['name']}_vs_{bc['name']}_arena.xml"
    (out / name).write_text(arena)
    m = mujoco.MjModel.from_xml_string(arena)
    d = mujoco.MjData(m)
    stand = json.loads((out / f"{rc['name']}_fingerprint.json").read_text())["stand_pose_deg"]
    set_pose(m, d, stand, "red_"); set_pose(m, d, stand, "blue_")
    mujoco.mj_forward(m, d)
    touching = {tuple(sorted((m.geom(c.geom1).name, m.geom(c.geom2).name))) for c in d.contact[: d.ncon]
                if not {m.geom(c.geom1).name, m.geom(c.geom2).name} & {"floor"}}
    print(f"arena: {name}, podium top {pod_h} m, gap {gap} m, contacts at stance besides floor: {sorted(touching) or 'none'}")
    if touching:
        sys.exit("ARENA FAIL: athletes touch each other or the podium at stance")


if __name__ == "__main__":
    main()
