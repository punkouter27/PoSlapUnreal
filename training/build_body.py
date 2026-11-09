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

import constants

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
KV_RATIO = 0.05        # damping = 5 % of stiffness: the floor for trunk and legs
ZETA = 0.8             # damping ratio aimed for, from each joint's inertia in the stand pose (2026-10-22 audit: 5 % of
                       # stiffness gave 0.18-0.27 at waist and hips, a 1-2 Hz trunk sway, and 1.5-2.2 at shoulder and ankle)
FREE = {"neck", "upperarm", "forearm", "hand"}   # carry nothing below them: their stand-pose inertia is the real load, so
                                                 # ZETA exactly. Trunk and legs carry the body in stance (more than that
                                                 # inertia shows): damping only raised to ZETA, never lowered
JOINT_SEG = {jn: sg for sg, js in JOINTS.items() for jn, *_ in js}
ARM_ARMATURE = 0.005   # arm joints; the default 0.02 was 49-84 % of wrist/shoulder inertia and slowed the whip
MUSCLE_TC = 0.035      # s: muscle activation time constant (human 30-40 ms); <position timeconst> = filterexact, so
                       # every engine (Warp, URLab, WASM) ramps the motor toward the brain's target instead of snapping
ADULT_JOINT_SPEED = 12.0  # rad/s, scaled by card speed; enforced by the env, recorded here per joint
SEG_SPEED = {"upperarm": 20.0, "forearm": 25.0, "hand": 25.0}   # rad/s: a strike's arm outruns its trunk and legs
                                                                # (throwing peaks: trunk ~15, elbow ~40)
LENGTH_RATIO = {"thigh": (0.20, 0.28), "shin": (0.17, 0.27), "upperarm": (0.13, 0.20), "forearm": (0.09, 0.17)}   # Kim's
# auto-rigged scan sits low on shin (0.182) and forearm (0.096): ranges are a sanity check for scale/axes, not anatomy

# ---------------------------------------------------------------- physics versions
# PHYSICS 1: the shipped physics (every brain in models/brains trained on it; Unreal and the browser run it).
# PHYSICS 2 (2026-11-03, tasks J8 + I16): the collision and muscle realism batch, written to models/v2/ so the shipped
# models stay byte-identical until a retrained brain graduates on v2 (DOCS/20261103/training_realism.md):
#   * the palm-head contact: an explicit pair, stiff (solref at the 2 x timestep floor, solimp 0.99) and with a 15 mm
#     margin - at 200 Hz a 9 m/s palm travels 45 mm a step, so a discrete contact can only be caught early, not late
#     (the default softness let it sink 40-71 mm for ~50 ms, J8);
#   * the welded pairs MuJoCo's parent filter skips (a body without joints is welded to its parent, so belly-upper arm,
#     chest-head, pelvis-chest, belly-neck/head never collided) get explicit pairs; the fit check sees them too;
#   * floor and podium rigid (the same stiff solref/solimp: a stomp sank 25 mm) with torsional friction on every
#     contact with them (condim 4: the soles resist a twist on the spot);
#   * asymmetric torque limits where human strength is (knee, hip, ankle, elbow, trunk) and the rotators trimmed to
#     the literature (TORQUE_V2); the stiffness stays from the stronger direction;
#   * engines read the version from the fingerprint ("physics": 2) and take the strike impulse from the velocity of
#     the part that touched (v1: hand_r's, whatever touched).
# PHYSICS 3 (2026-11-04, DOCS/20261104/physics3.md; DOCS/20261103/collision_review.md's proposals), physics 2 plus:
#   * the FACE: two jaw capsules on the head body (chin to each jaw angle) under the cranium sphere, and the cranium
#     sphere centred on the drawn head (v1/v2 put it at the bone midpoint + the mesh's absolute centre: Kim's sat
#     9.8 cm behind her face, Matt's 4 cm ahead); the referee counts a touch on the face geoms whose contact point
#     lies inside the head's `face_zone` site (cheek and jaw, Blueprint 2.1) - every engine runs the same box test;
#   * stiff, early pairs for each hand against the other's neck and jaw (the slap landing low), as for the head;
#   * every athlete geom stiff (solref/solimp as the head): limb-on-limb bumps sank 2-3 cm with the default softness;
#   * the per-foot friction randomisation made real: the sole boxes carry priority 2 with the floor's friction,
#     condim and stiffness, so a sole's contact takes the SOLE's (randomised) friction; with the floor's priority the
#     floor's value won every floor contact (tasks K11).
PHYSICS = 1
STIFF_SOLREF = (0.01, 1.0)            # the stiffest timeconst MuJoCo allows at a 5 ms step (refsafe: 2 x timestep)
STIFF_SOLIMP = (0.99, 0.999, 0.001)   # impedance ~1 from the first mm: the contact does not give like skin
STRIKE_MARGIN = 0.015                 # m: the palm-head pair engages 15 mm out (the worst sink falls by as much)
# physics 3: the jaw, in units of the cranium sphere's radius r from its centre (head frame: +X forward, +Y left)
JAW_CHIN, JAW_ANGLE, JAW_R = (0.62, 0.0, -1.15), (-0.05, 0.55, -0.75), 0.28   # chin point, jaw angle (y mirrored), radius
FACE_PAD = 0.03          # m: the face zone reaches this far past the skin shapes (a margin contact's point sits up to
                         # 7.5 mm out; a slap's contact point is never deeper than the shapes)
FACE_TOP, FACE_BACK = 0.40, 0.45   # the zone's top (brow / cheekbone) and back (just behind the ear line), x r
FOOT_PRIORITY = 2        # physics 3: above the floor's and podium's 1
TORQUE_V2 = {   # trained adult (Nm): (toward the joint's negative end, toward its positive end); + = flex/forward/up/outward
    "knee": (220, 130),          # extension (quadriceps) vs flexion (hamstrings)
    "hip_flex": (220, 150),      # extension (glutes, hamstrings) vs flexion (iliopsoas)
    "ankle_flex": (180, 50),     # plantarflexion (calf) vs dorsiflexion (tibialis)
    "elbow": (55, 70),           # extension (triceps) vs flexion (biceps, brachialis)
    "waist_flex": (200, 150),    # back extensors vs abdominals
    "waist_twist": (90, 90),     # trunk axial rotation 60-90 Nm (v1 120)
    "hip_rot": (60, 60),         # hip rotators 40-60 Nm (v1 100)
    "ankle_roll": (40, 40),      # inversion / eversion 30-40 Nm (v1 60)
}


def models_dir(physics=None):
    """Where a physics version's models live: models/ (v1, shipped) or models/v<n>/ (constants.models_dir)."""
    return constants.models_dir(PHYSICS if physics is None else physics)


def weld_root(n):
    """MuJoCo welds a body without joints to its parent (pelvis is the free root): chest -> belly, head -> neck."""
    while PARENT[n] and seg(n) not in JOINTS:
        n = PARENT[n]
    return n


def filtered_pairs():
    """Body pairs of one athlete that MuJoCo's parent filter skips but that are NOT a direct parent and child: welded
    bodies take their weld root's parent, so belly-upper arm, chest-head, pelvis-chest (and belly-neck/head) never
    collided (J8). Physics 2 gives each an explicit <pair>."""
    out = []
    names = list(PARENT)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if PARENT[a] == b or PARENT[b] == a:
                continue
            wa, wb = weld_root(a), weld_root(b)
            pa, pb = PARENT[wa] and weld_root(PARENT[wa]), PARENT[wb] and weld_root(PARENT[wb])
            if wa == wb or wa == pb or wb == pa:
                out.append((a, b))
    return out


def pair_xml(g1, g2, margin=None):
    m = f' margin="{fmt(margin)}"' if margin else ""
    return (f'<pair geom1="{g1}" geom2="{g2}" condim="3" solref="{fmt(STIFF_SOLREF)}" solimp="{fmt(STIFF_SOLIMP)}"{m}/>')


def athlete_contacts(pre):
    """Physics 2: the explicit self-collision pairs of one athlete (each body here has one geom, named as the body)."""
    if PHYSICS < 2:
        return []
    geoms = lambda b: [b, "jaw_l", "jaw_r"] if b == "head" and PHYSICS >= 3 else [b]   # the head body's geoms
    return [pair_xml(pre + ga, pre + gb) for a, b in filtered_pairs() for ga in geoms(a) for gb in geoms(b)]


def strike_contacts(striker, head):
    """Physics 2: each hand against a head (the opponent's, or the solo scene's target), as a stiff early pair; the
    dynamic contact between the two bodies is excluded so it is not counted twice."""
    if PHYSICS < 2:
        return []
    out = []
    for h in ("hand_l", "hand_r"):
        out.append(f'<exclude body1="{striker}{h}" body2="{head[0]}"/>')
        out.append(pair_xml(striker + h, head[1], STRIKE_MARGIN))
        if PHYSICS >= 3:   # the target's jaw (on its body: the exclude above covers it); an arena uses limb_contacts
            for j in ("jaw_l", "jaw_r"):
                out.append(pair_xml(striker + h, f"{head[1]}_{j}", STRIKE_MARGIN))
    return out


# physics 3: the striker's fast parts and what they land on, each as a stiff early pair (the hands at 6-10 m/s cover
# 30-50 mm a step: physics 2 let a hand sink 29 mm into the neck and 20-29 mm into the chest, a forearm 28 mm into a jaw)
LIMB_TARGETS = {"hand_l": ("head", "neck", "chest", "belly", "upperarm_l", "upperarm_r", "forearm_l", "forearm_r"),
                "forearm_l": ("head", "neck")}


def limb_contacts(pres):
    """Physics 3, the arena: every pair of LIMB_TARGETS between the two athletes (both ways, each pair once), the
    bodies' dynamic contact excluded. The head body's geoms are the cranium sphere and both jaw capsules."""
    out, seen = [], set()
    geoms = lambda b: ["head", "jaw_l", "jaw_r"] if b == "head" else [b]
    for s, o in (pres, pres[::-1]):
        for limb0, targets in LIMB_TARGETS.items():
            for limb in (limb0, limb0[:-1] + "r"):
                for b in targets:
                    key = frozenset((s + limb, o + b))
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append(f'<exclude body1="{s}{limb}" body2="{o}{b}"/>')
                    out += [pair_xml(s + limb, o + g, STRIKE_MARGIN) for g in geoms(b)]
    return out


def face_parts(pre=""):
    """The geoms of one head that count as the face (physics 3: the cranium sphere and both jaw capsules)."""
    return [pre + "head"] + ([pre + "jaw_l", pre + "jaw_r"] if PHYSICS >= 3 else [])


def jaw_geoms(c, r):
    """Physics 3: the two jaw capsules, chin -> each jaw angle, from the cranium sphere (centre c, radius r)."""
    chin = np.asarray(c, float) + r * np.array(JAW_CHIN)
    out = []
    for s, sg in (("l", 1), ("r", -1)):
        ang = np.asarray(c, float) + r * np.array([JAW_ANGLE[0], sg * JAW_ANGLE[1], JAW_ANGLE[2]])
        out.append(("capsule", "jaw_" + s, chin, ang, JAW_R * r))
    return out


def face_zone(c, r):
    """(centre, half-size) of the head's face_zone box in the head frame. Physics 3: cheeks and jaw, fitted to the
    sphere and the jaw capsules plus FACE_PAD; v1/v2: the old marker box (drawn only, never tested)."""
    if PHYSICS < 3:
        return np.asarray(c, float) + [0.04, 0, -0.02], np.array([0.06, 0.09, 0.06])
    lo = np.array([-FACE_BACK * r, -(r + FACE_PAD), (JAW_CHIN[2] - JAW_R) * r - FACE_PAD])
    hi = np.array([r + FACE_PAD, r + FACE_PAD, FACE_TOP * r])
    return np.asarray(c, float) + (lo + hi) / 2, (hi - lo) / 2


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
    # a card may widen a range for a body that is not adult-proportioned on purpose (tong.json: a baby scan at adult
    # size, thigh 0.18 H); it states why in "length_ratio_note"
    for s, (lo, hi) in {**LENGTH_RATIO, **{k: tuple(v) for k, v in card.get("length_ratio", {}).items()}}.items():
        L = np.linalg.norm(np.array(B[s + "_l"]["tail"]) - B[s + "_l"]["head"]) / H
        if not lo <= L <= hi:
            err.append(f"{s} length {L:.3f} x height outside human range {lo}-{hi}")
    print(f"A8 rig: {len(B)} bones, top {top:.3f} m, feet {low:.3f} m, faces -Y, "
          f"{'OK' if not err else 'FAIL'}")
    if err:
        sys.exit("RIG FAIL:\n  " + "\n  ".join(err))
    mass_total = MASS["pelvis"] + MASS["belly"] + MASS["chest"] + MASS["neck"] + MASS["head"] + 2 * (
        MASS["upperarm"] + MASS["forearm"] + MASS["hand"] + MASS["thigh"] + MASS["shin"] + MASS["foot"])
    assert abs(mass_total - 100.0) < 1e-6, f"anthropometric mass total {mass_total} != 100%"
    for jn, _, (lo, hi), _ in JOINTS["shin"] + JOINTS["forearm"]:
        assert lo >= 0 and hi <= 145, f"A9 FAIL: {jn} range ({lo}, {hi}) permits backward hyperextension"
    print(f"A9 realism limits: knee/elbow anti-hyperextension OK, mass sum {mass_total:.1f}%, damping zeta={ZETA}")


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

    k = card["height_m"] / 1.7   # torso bands were measured on Grandpa (1.70 m); they scale with height
    for n, band, zc in (("pelvis", (0.85, 0.95), None), ("belly", (1.0, 1.1), None), ("chest", (1.15, 1.3), "mid")):
        band = (band[0] * k, band[1] * k)
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
    if PHYSICS >= 3:   # the drawn head's centre: its mesh's (absolute) fore-aft middle, at the bone's mid height
        c = np.array([-(hy[0] + hy[1]) / 2, 0.0, (h[2] + t[2]) / 2]) - h
    else:              # (v1/v2: the bone midpoint + the mesh's absolute centre: off by the bone's own fore-aft offset)
        c = (h + t) / 2 + [-(hy[0] + hy[1]) / 2, 0, 0] - h
    r = 0.92 * st["head"]["r_med"]
    add("head", ("sphere", "head", c, None, r), *(jaw_geoms(c, r) if PHYSICS >= 3 else []))
    if PHYSICS >= 3:   # the head's mass split by volume (cranium, jaw): the body keeps its card mass
        vol = [4 / 3 * math.pi * g[4] ** 3 if g[0] == "sphere" else
               math.pi * g[4] ** 2 * (4 / 3 * g[4] + np.linalg.norm(np.asarray(g[3]) - g[2])) for g in geo["head"]["geoms"]]
        geo["head"]["mass_frac"] = [v / sum(vol) for v in vol]
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


def athlete_xml(geo, card, pre, pos, quat, scale, rgba, inertia=None):
    """inertia: joint -> stand-pose joint-space inertia (joint_inertia); None = KV_RATIO everywhere (the first pass)."""
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
            arm = f' armature="{fmt(ARM_ARMATURE)}"' if seg(n) in ("upperarm", "forearm", "hand") else ""
            out.append(f'{ind}  <joint name="{j}" axis="{fmt(axis)}" range="{rng}"{arm}/>')
            st = S * card.get("toughness", 1.0) if seg(n) == "neck" else S   # toughness: neck bracing (KO resistance)
            t_neg, t_pos = TORQUE_V2.get(jn, (tq, tq)) if PHYSICS >= 2 else (tq, tq)
            tq = max(t_neg, t_pos)   # the stiffness from the stronger direction; the forcerange clips the weaker one
            kp = tq * st * KP_PER_NM
            kv = kp * KV_RATIO
            if inertia:   # minus the joint's own damping (0.5, <default>)
                aim = max(0.0, 2 * ZETA * math.sqrt(kp * inertia[jn + side]) - 0.5)
                kv = aim if seg(n) in FREE else max(kv, aim)
            # force-velocity: not here - every engine caps forcerange by Hill's law each step (brain_cpu.hill)
            acts.append(f'<position name="{j}" joint="{j}" kp="{fmt(kp)}" kv="{fmt(kv)}" '
                        f'ctrlrange="{rng}" forcerange="{fmt([-t_neg * st, t_pos * st])}" timeconst="{fmt(MUSCLE_TC)}"/>')
        # physics 2: palm and head do not give like skin in any contact (their pair is set apart); physics 3: no part does
        stiff = (f' solref="{fmt(STIFF_SOLREF)}" solimp="{fmt(STIFF_SOLIMP)}"'
                 if (PHYSICS >= 2 and seg(n) in ("head", "hand")) or PHYSICS >= 3 else "")
        if PHYSICS >= 3 and seg(n) == "foot":   # the sole wins its floor/podium contacts: its own (randomised) friction
            stiff += f' priority="{FOOT_PRIORITY}" condim="4" friction="{fmt([0.9, 0.02, 0.01])}"'
        frac = g.get("mass_frac", [1 / len(g["geoms"])] * len(g["geoms"]))
        for gg, fr in zip(g["geoms"], frac):
            out.append(f"{ind}  " + geom_xml(gg, g["mass"] * fr, pre, scale).replace("/>", f' rgba="{rgba}"{stiff}/>'))
        if n == "head":
            fc, fh = face_zone(g["geoms"][0][2], g["geoms"][0][4] * scale.get("head", 1.0))
            out.append(f'{ind}  <site name="{pre}face_zone" type="box" pos="{fmt(fc)}" size="{fmt(fh)}" rgba="1 1 0 0.2"/>')
        for c in kids[n]:
            out += body(c, depth + 1)
        out.append(f"{ind}</body>")
        return out

    return "\n".join(body("pelvis", 2)), acts, athlete_contacts(pre)


HEADER = f"""<mujoco model="{{model}}">
  <compiler angle="radian" autolimits="true"/>
  <option timestep="0.005" integrator="implicitfast" iterations="20"/>
  <default>
    <joint armature="{fmt(0.02)}" damping="0.5"/>
    <geom condim="3" friction="{fmt([0.8, 0.02, 0.01])}"/>
    <position ctrllimited="true" forcelimited="true"/>
  </default>
  <worldbody>
    <light pos="0 0 4" dir="0 0 -1" diffuse="0.8 0.8 0.8"/>
    <geom name="floor" type="plane" size="10 10 0.1" friction="{fmt([0.9, 0.02, 0.01])}" priority="1" rgba="0.25 0.25 0.28 1"/>
"""


def header(model):
    """Physics 2: the floor rigid, with torsional friction (condim 4) - the floor has priority, so its condim and
    softness are every floor contact's (the soles' included)."""
    h = HEADER.format(model=model)
    if PHYSICS >= 2:
        h = h.replace('priority="1"', f'priority="1" condim="4" solref="{fmt(STIFF_SOLREF)}" solimp="{fmt(STIFF_SOLIMP)}"')
    return h


def ground():
    """What a world obstacle (podium, step) adds in physics 2: as rigid as the floor, with its priority and condim."""
    return f' priority="1" condim="4" solref="{fmt(STIFF_SOLREF)}" solimp="{fmt(STIFF_SOLIMP)}"' if PHYSICS >= 2 else ""


def scene_xml(model, parts, extra_world="", contacts=()):
    bodies = "\n".join(p[0] for p in parts)
    acts = "\n    ".join(a for p in parts for a in p[1])
    pairs = [c for p in parts for c in (p[2] if len(p) > 2 else [])] + list(contacts)
    con = ("\n  <contact>\n    " + "\n    ".join(pairs) + "\n  </contact>") if pairs else ""
    return (header(model) + extra_world + bodies + "\n  </worldbody>" + con + "\n  <actuator>\n    " + acts
            + "\n  </actuator>\n</mujoco>\n")


# ---------------------------------------------------------------- poses and checks
def set_pose(m, d, pose, pre=""):
    for j, deg in pose.items():
        d.qpos[m.joint(pre + j).qposadr[0]] = math.radians(deg)


def stand_pose(raise_deg, card=None):
    """Arms down (abducted raise_deg), elbows soft; a card's style stance (its balancing style) overrides any joint."""
    p = {}
    for s in ("_l", "_r"):
        p["shoulder_raise" + s] = raise_deg
        p["elbow" + s] = 10
    p.update((card or {}).get("style", {}).get("stance_deg", {}))
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


def stride_poses(base):
    """Walking and jogging stride poses: alternating hip flex/extension and knee bend must stay self-collision free."""
    for phase in (-20, -10, 0, 15, 30):
        p = dict(base)
        p.update({
            "hip_flex_l": phase, "knee_l": max(0, phase),
            "hip_flex_r": -phase, "knee_r": max(0, -phase),
            "ankle_flex_l": -10 if phase > 0 else 10,
            "ankle_flex_r": -10 if phase < 0 else 10,
        })
        yield f"stride {phase}", p


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
                mujoco.mj_resetData(m, d); set_pose(m, d, stand_pose(r, card))
                if not self_overlaps(m, d):
                    stand = r
                    break
            if stand is None:
                mujoco.mj_resetData(m, d); set_pose(m, d, stand_pose(-70, card))
                bad, where = self_overlaps(m, d), "stand"
        if not bad:
            for label, p in list(swing_poses(stand_pose(stand, card))) + list(stride_poses(stand_pose(stand, card))):
                mujoco.mj_resetData(m, d); set_pose(m, d, p)
                bad = self_overlaps(m, d)
                if bad:
                    where = label
                    break
        if not bad:
            shrunk = {k: round(v, 3) for k, v in scale.items()}
            print(f"A10 self-collision: clean in reference, stand (arms {stand} deg), swing & stride arcs; "
                  f"shrunk {shrunk or 'nothing'}")
            return scale, stand
        for pair in sorted(bad):   # sorted: the fingerprint's geom_scale order no longer depends on the hash seed
            for g in pair:
                scale[g] = scale.get(g, 1.0) * 0.95
        print(f"    overlap at {where}: {sorted(bad)} -> shrink 5 %")
    sys.exit("A10 FAIL: overlaps remain after 25 shrink rounds")


def sole_gap(xml, stand, card):
    """Lowest sole corner in the stand pose (m): a bent-knee style stance lifts the feet off the floor."""
    m = mujoco.MjModel.from_xml_string(xml)
    d = mujoco.MjData(m)
    set_pose(m, d, stand_pose(stand, card))
    mujoco.mj_kinematics(m, d)
    low = 9.0
    for f in ("foot_l", "foot_r"):
        g = m.geom(f).id
        R = d.geom_xmat[g].reshape(3, 3)
        for c in np.array(np.meshgrid([-1, 1], [-1, 1], [-1, 1])).T.reshape(-1, 3):
            low = min(low, float((d.geom_xpos[g] + R @ (c * m.geom_size[g]))[2]))
    return low


def joint_inertia(xml, stand, card):
    """Joint-space inertia (diagonal of M, armature included) of every hinge in the stand pose, pelvis held."""
    m = mujoco.MjModel.from_xml_string(xml)
    d = mujoco.MjData(m)
    set_pose(m, d, stand_pose(stand, card))
    mujoco.mj_forward(m, d)
    M = np.zeros((m.nv, m.nv))
    mujoco.mj_fullM(m, d, M)
    return {m.joint(j).name: float(M[m.jnt_dofadr[j], m.jnt_dofadr[j]]) for j in range(m.njnt) if m.jnt_type[j] == 3}


def balance_and_drop(xml, stand, card):
    m = mujoco.MjModel.from_xml_string(xml)
    d = mujoco.MjData(m)
    set_pose(m, d, stand_pose(stand, card))
    mujoco.mj_forward(m, d)
    com = d.subtree_com[m.body("pelvis").id]
    feet = (d.geom_xpos[m.geom("foot_l").id] + d.geom_xpos[m.geom("foot_r").id]) / 2
    off = com[:2] - feet[:2]
    print(f"A11 balance: COM {off[0] * 100:+.1f} cm fore/aft, {off[1] * 100:+.1f} cm sideways of mid-feet "
          f"({'OK' if abs(off[0]) < 0.04 and abs(off[1]) < 0.02 else 'OFF-CENTRE'})")
    for j in range(m.nu):
        d.ctrl[j] = d.qpos[m.jnt_qposadr[m.actuator_trnid[j, 0]]]
    d.act[:] = d.ctrl   # muscles start settled at the stance (MUSCLE_TC filter state), as every engine's reset
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


SHOULDER_BACK_DEG, SHOULDER_ACROSS_DEG = 60, 50   # human shoulder cone: the upper arm at most 60 deg behind the chest's
# frontal plane (extension / horizontal abduction) and 50 deg across its midline. ponytail: direction only, twist ignored


def shoulder_cone(xml, stand, card, n=4000):
    """A13: no corner or sample of the shoulder's hinge box (swing x raise x twist) points the upper arm outside the human
    cone (2026-10-26: the three hinges at 0.8-0.85 flexibility reach 42 deg behind, 28 across: no penalty needed)."""
    m = mujoco.MjModel.from_xml_string(xml)
    d = mujoco.MjData(m)
    set_pose(m, d, stand_pose(stand, card))
    rng = np.random.default_rng(0)
    js = [m.joint(f"shoulder_{k}_l") for k in ("swing", "raise", "twist")]
    corners = np.array(np.meshgrid(*[j.range for j in js])).T.reshape(-1, 3)
    ua, fa, chest = m.body("upperarm_l").id, m.body("forearm_l").id, m.body("chest").id
    back, across = math.sin(math.radians(SHOULDER_BACK_DEG)), math.sin(math.radians(SHOULDER_ACROSS_DEG))
    behind = over = 0.0
    for q in list(corners) + [[rng.uniform(*j.range) for j in js] for _ in range(n)]:
        for j, x in zip(js, q):
            d.qpos[j.qposadr[0]] = x
        mujoco.mj_kinematics(m, d)
        v = d.xmat[chest].reshape(3, 3).T @ (d.xpos[fa] - d.xpos[ua])
        v /= np.linalg.norm(v)
        behind, over = max(behind, -v[0]), max(over, -v[1])   # left arm: across the midline = -Y
    behind, over = math.degrees(math.asin(max(0.0, behind))), math.degrees(math.asin(max(0.0, over)))
    print(f"A13 shoulder cone: upper arm at most {behind:.0f} deg behind (limit {SHOULDER_BACK_DEG}), "
          f"{over:.0f} deg across (limit {SHOULDER_ACROSS_DEG})")
    if behind > SHOULDER_BACK_DEG or over > SHOULDER_ACROSS_DEG:
        sys.exit("A13 FAIL: shoulder ranges reach past a human shoulder")


GAP = 0.85   # pelvis to pelvis (PRD 0.85-1.0 m; shortest so a lean reaches)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("card")
    ap.add_argument("--blue", help="card for the blue athlete (default: same as red)")
    ap.add_argument("--physics", type=int, default=1, choices=(1, 2, 3),
                    help="1: the shipped physics (models/); 2: the realism batch (models/v2/); 3: + face, stiff limbs, sole friction (models/v3/)")
    a = ap.parse_args()
    global PHYSICS
    PHYSICS = a.physics
    if mujoco.__version__ != MUJOCO_PIN:
        sys.exit(f"A12 FAIL: MuJoCo {mujoco.__version__}, pinned {MUJOCO_PIN} (URLab)")
    out = models_dir()
    out.mkdir(parents=True, exist_ok=True)
    if PHYSICS >= 2:
        print(f"physics {PHYSICS}: writing {out} (the shipped models in models/ are untouched)")
    built = {}
    for path in dict.fromkeys([a.card, a.blue or a.card]):
        card = json.loads(Path(path).read_text())
        sk = json.loads((HERE / card["skeleton"]).read_text())
        validate(sk, card)
        geo = athlete_geometry(sk, card)
        scale, stand = fit(geo, card)
        root = geo["pelvis"]["origin"] + [0, 0, 0.001]
        # physics 2: the target sphere is named (its strike pair) and as stiff as a head
        tgeom = f' name="target" solref="{fmt(STIFF_SOLREF)}" solimp="{fmt(STIFF_SOLIMP)}"' if PHYSICS >= 2 else ""
        if PHYSICS >= 3:   # the L2 target is a face too: turned to look at the striker (-X), its jaw and face zone as a head's
            sf = f' solref="{fmt(STIFF_SOLREF)}" solimp="{fmt(STIFF_SOLIMP)}" rgba="0.9 0.2 0.2 0.5"/>'
            fc, fh = face_zone(np.zeros(3), 0.09)
            target = ('    <body name="target" mocap="true" pos="5 0 1.6" quat="0 0 0 1">'
                      f'<geom name="target" type="sphere" size="0.09"{sf}'
                      + "".join(geom_xml(g, 0.1, "target_", {}).replace("/>", sf) for g in jaw_geoms(np.zeros(3), 0.09))
                      + f'<site name="target_face_zone" type="box" pos="{fmt(fc)}" size="{fmt(fh)}"/></body>\n')
        else:
            target = f'    <body name="target" mocap="true" pos="5 0 1.6"><geom{tgeom} type="sphere" size="0.09" rgba="0.9 0.2 0.2 0.5"/></body>\n'
        solo_pairs = strike_contacts("", ("target", "target"))
        pod_h = round(0.59 * card["height_m"], 3)   # the arena's podium where the arena has it (L2's free hand found it)
        target += (f'    <geom name="podium" type="box" pos="{fmt([root[0] + GAP / 2, root[1], pod_h / 2])}" '
                   f'size="{fmt([0.10, 0.30, pod_h / 2])}" rgba="0.15 0.15 0.18 1"{ground()}/>\n')
        solo = scene_xml(card["name"], [athlete_xml(geo, card, "", root, (1, 0, 0, 0), scale, "0.8 0.6 0.5 1")], target, solo_pairs)
        if card.get("style", {}).get("stance_deg"):   # style stance: lower the root until the soles stand on the floor
            root = root - [0, 0, sole_gap(solo, stand, card) - 0.001]
            solo = scene_xml(card["name"], [athlete_xml(geo, card, "", root, (1, 0, 0, 0), scale, "0.8 0.6 0.5 1")], target, solo_pairs)
            print(f"style stance: root lowered to {root[2]:.3f} m, soles {sole_gap(solo, stand, card) * 1000:.1f} mm")
        shoulder_cone(solo, stand, card)
        inertia = joint_inertia(solo, stand, card)   # second pass: damping from each joint's own load
        solo = scene_xml(card["name"], [athlete_xml(geo, card, "", root, (1, 0, 0, 0), scale, "0.8 0.6 0.5 1", inertia)], target, solo_pairs)
        (out / f"{card['name']}_solo.xml").write_text(solo)
        # the locomotion obstacle scene (S3/S4, training/envs/locomotion.py): the same athlete on the same floor,
        # plus a LOW STEP to climb and the podium to walk around. The env moves and resizes both per world; the
        # solo and arena models are untouched, so the shipped brains and the Unreal import stay valid.
        step = (f'    <geom name="step" type="box" pos="3 0 0.08" size="{fmt([0.25, 0.60, 0.08])}" '
                f'rgba="0.35 0.30 0.25 1"{ground()}/>\n')
        loco = scene_xml(card["name"], [athlete_xml(geo, card, "", root, (1, 0, 0, 0), scale, "0.8 0.6 0.5 1", inertia)], target + step, solo_pairs)
        (out / f"{card['name']}_loco.xml").write_text(loco)
        m, off, up = balance_and_drop(solo, stand, card)
        joints = [m.joint(m.actuator_trnid[i, 0]).name for i in range(m.nu)]
        fp = {"mujoco": mujoco.__version__, "model": f"{card['name']}_solo.xml", **({"physics": PHYSICS} if PHYSICS >= 2 else {}),
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
              **({"torque_limit_neg_nm": (-m.actuator_forcerange[:, 0]).tolist()} if PHYSICS >= 2 else {}),
              "max_joint_speed_rad_s": [SEG_SPEED.get(JOINT_SEG[seg(j) if j.endswith(("_l", "_r")) else j], ADULT_JOINT_SPEED)
                                        * card["speed"] for j in joints],   # per joint, in joint order
              "stand_pose_deg": stand_pose(stand, card), "stand_pose_rad": {k: math.radians(v) for k, v in stand_pose(stand, card).items()},
              "root_height_m": float(root[2]),
              "geom_scale": scale, "balance_offset_m": off.tolist(), "drop_test_upright_s": up,
              "card": card}
        (out / f"{card['name']}_fingerprint.json").write_text(json.dumps(fp, indent=1))
        built[path] = (card, geo, scale, root, inertia)
        print(f"A12 fingerprint: MuJoCo {fp['mujoco']}, {fp['sizes']}, {fp['mass_kg']} kg, sha {fp['sha256'][:12]}")

    # arena: red at -X facing +X, blue at +X facing -X, podium between (Blueprint 5)
    (rc, rg, rs, rr, ri), (bc, bg, bs, br, bi) = built[a.card], built[a.blue or a.card]
    gap = GAP
    pod_h = round(0.59 * min(rc["height_m"], bc["height_m"]), 3)  # below the belly bulge
    world = (f'    <geom name="podium" type="box" pos="0 0 {fmt(pod_h / 2)}" size="{fmt([0.10, 0.30, pod_h / 2])}" rgba="0.15 0.15 0.18 1"{ground()}/>\n'
             f'    <site name="footbox_red" type="box" pos="{fmt([-gap / 2, 0, 0.001])}" size="{fmt([0.20, 0.25, 0.001])}" rgba="0.8 0.1 0.1 0.4"/>\n'
             f'    <site name="footbox_blue" type="box" pos="{fmt([gap / 2, 0, 0.001])}" size="{fmt([0.20, 0.25, 0.001])}" rgba="0.1 0.2 0.8 0.4"/>\n')
    red = athlete_xml(rg, rc, "red_", [-gap / 2, 0, rr[2]], (1, 0, 0, 0), rs, "0.85 0.3 0.3 1", ri)
    blue = athlete_xml(bg, bc, "blue_", [gap / 2, 0, br[2]], (0, 0, 0, 1), bs, "0.3 0.4 0.85 1", bi)
    arena = scene_xml(f"{rc['name']}_vs_{bc['name']}", [red, blue], world,
                      limb_contacts(("red_", "blue_")) if PHYSICS >= 3 else
                      strike_contacts("red_", ("blue_head", "blue_head")) + strike_contacts("blue_", ("red_head", "red_head")))
    name = f"{rc['name']}_vs_{bc['name']}_arena.xml"
    (out / name).write_text(arena)
    m = mujoco.MjModel.from_xml_string(arena)
    d = mujoco.MjData(m)
    # each corner in ITS OWN stand pose (a mixed pairing has two; posing both with red's hid blue's contacts)
    for pref, card_i in (("red_", rc), ("blue_", bc)):
        st = json.loads((out / f"{card_i['name']}_fingerprint.json").read_text())["stand_pose_deg"]
        set_pose(m, d, st, pref)
    mujoco.mj_forward(m, d)
    touching = {tuple(sorted((m.geom(c.geom1).name, m.geom(c.geom2).name))) for c in d.contact[: d.ncon]
                if not {m.geom(c.geom1).name, m.geom(c.geom2).name} & {"floor"}}
    print(f"arena: {name}, podium top {pod_h} m, gap {gap} m, contacts at stance besides floor: {sorted(touching) or 'none'}")
    if touching:
        sys.exit("ARENA FAIL: athletes touch each other or the podium at stance")
    if PHYSICS == 1:   # Content/Data is what a packaged build plays: only the shipped physics goes there
        stage_data(a.card, a.blue or a.card)
    else:
        print(f"physics {PHYSICS}: not staged into Content/Data (the packaged build plays physics 1)")


def stage_data(*cards):
    """Copy the cards, skeletons and fingerprints into Content/Data, the directory a packaged (Android) build stages.
    Without them the packaged build has no character card and no fingerprint: the athlete binds with a zero strike
    mass (no KO) and the roster tab is empty. Staging here keeps them in step with the source of truth."""
    dst = HERE.parent / "Content" / "Data"
    dst.mkdir(parents=True, exist_ok=True)
    for path in cards:
        card = json.loads(Path(path).read_text())
        name = card["name"]
        for src in (Path(path), HERE / card["skeleton"], HERE / "models" / f"{name}_fingerprint.json"):
            if not src.exists():
                print(f"stage: {src.name} missing, skipped")
                continue
            (dst / src.name).write_bytes(src.read_bytes())
    print(f"staged into Content/Data: {', '.join(sorted(p.name for p in dst.glob('*.json')))}")


if __name__ == "__main__":
    main()
