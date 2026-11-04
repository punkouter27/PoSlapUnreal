r"""Grandpa's drawn hands remodelled as open palms that match the MuJoCo hand boxes (BLUEPRINT 2.1: the hand is a flat
box, fingers fixed open). Approved 2026-11-04 after DOCS/20261103/collision_review.md found the scan's right hand curled
and rolled ~30 deg off its box and the left hand a fist.

    "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" -b --factory-startup --python Scripts/blender_open_hands.py -- [--no-save] [--no-export] [--no-web] [--out <test.blend>]

Source: Content/Art/Raw/Grandpa_rigged_scanhands.blend (the scan's hands; made from Grandpa_rigged.blend on the first
run), so the script always starts from the scan and re-runs give the same result. Output: Content/Art/Raw/Grandpa_rigged.blend,
then Scripts/blender_cheek_morphs.py's export(): Content/Art/Exports/Grandpa_rigged.fbx + .glb and Web/assets/grandpa.glb.
LODs after it: Scripts/athlete_lods.py on the new blend.

Per hand:
  1. The hand box from training/models/grandpa_solo.xml in the Blender frame: the box's pose in its body (geom pos/quat),
     the body's rest frame, turned into Blender by the rigid map Blender rest -> MuJoCo qpos0 (Kabsch over the 17 bone
     heads vs the 17 body origins, as Scripts/grandpa_v2.py drives the rig) and anchored at the hand bone's head (bone =
     body position, as Web/art.js and Unreal pose the bones). Hand frame: u = the box's long axis (distal), w = its
     wide axis (thumb side, the athlete's front), n = its thin axis (the back of the hand, up in the T-pose).
  2. The cut: the narrowest section of the arm between 8 cm before and 4 cm past the wrist joint (the scan's own wrist).
     Everything distal of it is removed; every other vertex of the scan keeps its exact position, weights and UVs.
  3. The new hand: a signed-distance model in the hand frame, sized to the box (8.4 x 18.4 x 3 cm, the card's hand =
     10.8 % of height): a wrist stub whose cross-section starts as the cut's own outline and eases into the wrist,
     a palm (rounded box, thenar and hypothenar pads), four fingers (three tapered phalanges each, 3-5 deg of natural
     flex, a slight fan) and the thumb adducted along the index finger, smooth-unioned. Meshed with surface nets
     (1 mm grid, vertices projected onto the surface), decimated to ~1,600 faces, cut 4 mm past the arm's cut and
     zipped to it; the new vertices at the seam are relaxed against the fixed scan vertices.
  4. Weights: hand bone = smoothstep over 3 cm either side of the wrist joint, eased from the cut's own weights, the
     rest to the forearm bone (the same two bones the scan used there).
  5. UVs: the new hand is cut into ~2.5 cm patches (hand-frame cells x the face's main direction), each unwrapped
     angle-based (near-isometric) and scaled to the scan hand's own texel density, then placed whole on the scan's own
     hand skin in the existing texture: the removed hand's texels, colour-filtered to the 60 % nearest the median skin
     colour and brighter than its darkest quarter (no cuff, nail or crease texels), 3 px in from every edge; an FFT
     search picks the deepest spot and patches spread over the skin. Patches may share texels (the texture is only
     read; the removed hand no longer uses them). Same material, same texture files, no new texture.
  6. Shape keys: CheekDent_L / CheekDent_R and _CHEEKMASK rebuilt by blender_cheek_morphs.add_cheek_morphs (a function
     of the head, which is untouched: same dents). The scan's custom normals are kept
     (the new corners carry 0 = the auto normal).
The armature is never touched: bone names, rest matrices, joint frames and limb lengths stay exactly as they were.
"""
import json
import math
import os
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import bmesh
import bpy
import numpy as np
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from blender_cheek_morphs import add_cheek_morphs, export, rig_and_mesh  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "Content/Art/Raw"
SRC = RAW / "Grandpa_rigged.blend"
SCAN = RAW / "Grandpa_rigged_scanhands.blend"
XML = ROOT / "training/models/grandpa_solo.xml"
HANDS = ("hand_l", "hand_r")
GRID = 0.001          # m, the surface-nets grid
TARGET_FACES = 1600   # per hand, after decimation
SKIN_PCT = 60        # % of the scan hand's texels (closest to the median skin colour) a patch may sample
PATCH = 0.025        # m, the UV patch cell (the scan's hand skin comes in islands ~5 cm across at its density)
GAP = 0.004           # m, the zipped band between the arm's cut and the new hand


# ---------------------------------------------------------------- MuJoCo rest frames and the hand boxes (Blender frame)
def quat_mat(q):
    w, x, y, z = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
                     [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
                     [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)]])


def mujoco_rest(xml=XML):
    """{body: (world pos, world rot)} at qpos0 and {geom: (body, pos, rot, size)}, from the solo model's XML."""
    bodies, geoms = {}, {}
    vec = lambda e, k, d: np.array([float(x) for x in e.get(k, d).split()])

    def walk(el, p, R):
        for b in el.findall("body"):
            P, RR = p + R @ vec(b, "pos", "0 0 0"), R @ quat_mat(vec(b, "quat", "1 0 0 0"))
            bodies[b.get("name")] = (P, RR)
            for g in b.findall("geom"):
                if g.get("name"):
                    geoms[g.get("name")] = (b.get("name"), vec(g, "pos", "0 0 0"), quat_mat(vec(g, "quat", "1 0 0 0")),
                                            vec(g, "size", "0"))
            walk(b, P, RR)

    walk(ET.parse(xml).getroot().find("worldbody"), np.zeros(3), np.eye(3))
    return bodies, geoms


def kabsch(arm, bodies):
    """Rotation Rk (Blender -> MuJoCo) of the rigid map bone heads -> body origins, and the fit's residual (m)."""
    names = [b.name for b in arm.data.bones if b.name in bodies]
    H = np.array([list(arm.matrix_world @ arm.data.bones[n].head_local) for n in names])
    P = np.array([bodies[n][0] for n in names])
    hc, pc = H.mean(0), P.mean(0)
    U, _, Vt = np.linalg.svd((H - hc).T @ (P - pc))
    Rk = (U @ np.diag([1, 1, np.sign(np.linalg.det(U @ Vt))]) @ Vt).T
    resid = np.linalg.norm((Rk @ (H - hc).T).T + pc - P, axis=1)
    return Rk, resid


def hand_boxes(arm, xml=XML):
    """Per hand, at rest in the Blender frame: the wrist joint O (bone head), the hand frame E (columns u, w, n) and the
    box (centre, axes as columns in the box's own x/y/z order, half sizes)."""
    bodies, geoms = mujoco_rest(xml)
    Rk, _ = kabsch(arm, bodies)
    out = {}
    for hand in HANDS:
        body, gp, gR, size = geoms[hand]
        R = bodies[body][1]
        O = np.array(arm.matrix_world @ arm.data.bones[hand].head_local)
        c = O + Rk.T @ (R @ gp)
        A = Rk.T @ R @ gR
        eu = A[:, 1] * np.sign(A[:, 1] @ (c - O))
        en = A[:, 2] * np.sign(A[2, 2])
        ew = np.cross(en, eu)
        ew *= np.sign(-ew[1])                      # the athletes face -Y: the thumb side (front) is -Y
        out[hand] = dict(O=O, E=np.stack([eu, ew, en], 1), centre=c, axes=A, size=size)
    return out


# ------------------------------------------------------------------------------------- signed distances (numpy, vectorised)
def smin(a, b, k):
    h = np.clip(0.5 + 0.5 * (b - a) / k, 0, 1)
    return b + (a - b) * h - k * h * (1 - h)


def round_cone(p, a, b, r1, r2):
    """Exact SDF of a cone with rounded ends (spheres r1 at a, r2 at b); p (N, 3)."""
    ba = b - a
    l2 = ba @ ba
    rr = r1 - r2
    a2 = l2 - rr * rr
    il2 = 1.0 / l2
    pa = p - a
    y = pa @ ba
    z = y - l2
    x2 = ((pa * l2 - y[:, None] * ba) ** 2).sum(1)
    y2, z2 = y * y * l2, z * z * l2
    k = np.sign(rr) * rr * rr * x2
    d_end = np.sqrt(x2 + z2) * il2 - r2
    d_start = np.sqrt(x2 + y2) * il2 - r1
    d_side = (np.sqrt(np.maximum(x2 * a2 * il2, 0)) + y * rr) * il2 - r1
    return np.where(np.sign(z) * a2 * z2 > k, d_end, np.where(np.sign(y) * a2 * y2 < k, d_start, d_side))


def ellipsoid(p, c, r):
    q = (p - c) / r
    return (np.linalg.norm(q, axis=1) - 1) * r.min()


def smoothstep(x):
    x = np.clip(x, 0, 1)
    return x * x * (3 - 2 * x)


def finger_chain(base, splay, flex, lengths, radii):
    """Joint centres of one finger from its knuckle: splay (deg, toward the thumb side), flex per joint (deg, toward the
    palm), segment lengths and radii at each joint. Returns [(a, b, ra, rb), ...] in (u, w, n)."""
    out, p, theta = [], np.array(base, float), 0.0
    s = math.radians(splay)
    for L, f, r1, r2 in zip(lengths, flex, radii[:-1], radii[1:]):
        theta += math.radians(f)
        d = np.array([math.cos(theta) * math.cos(s), math.cos(theta) * math.sin(s), -math.sin(theta)])
        q = p + L * d
        out.append((p, q, r1, r2))
        p = q
    return out


class HandSDF:
    """The open hand in the hand frame (u distal, w thumb side, n back of the hand), metres. `outline` is the arm's
    cut: centre (w, n) and radius per angle (bins over -pi..pi) at u_cut."""

    def __init__(self, u_cut, centre, radius):
        self.u_cut, self.c0, self.R0 = u_cut, np.asarray(centre), np.asarray(radius)
        nb = len(radius)
        self.th = -math.pi + (np.arange(nb) + 0.5) * 2 * math.pi / nb
        # the wrist the stub eases into: 5.8 x 3.9 cm, centred a little toward the scan's forearm
        self.c1 = np.clip(0.2 * self.c0, -0.005, 0.005)
        a1, b1 = 0.029, 0.0195
        self.R1 = a1 * b1 / np.sqrt((b1 * np.cos(self.th)) ** 2 + (a1 * np.sin(self.th)) ** 2)
        self.u_s = max(u_cut, 0.0) + 0.02          # the stub's end (it hands over to the palm)
        self.cn = float(np.clip(0.3 * self.c0[1], -0.005, 0.005))   # the palm's mid plane, eased toward the forearm
        cn = self.cn
        # fingers: knuckle (u, w), splay, flex, phalanx lengths, radii (knuckle, PIP, DIP, tip)
        F = {
            "index": ((0.094, 0.0255), 4.0, (3, 5, 3), (0.037, 0.0225, 0.0135), (0.0094, 0.0087, 0.0077, 0.0069)),
            "middle": ((0.097, 0.0085), 1.0, (3, 5, 3), (0.041, 0.0255, 0.0145), (0.0097, 0.0091, 0.0080, 0.0071)),
            "ring": ((0.094, -0.0085), -2.5, (3, 5, 3), (0.038, 0.0240, 0.0140), (0.0092, 0.0085, 0.0075, 0.0067)),
            "little": ((0.086, -0.0250), -7.0, (3, 5, 3), (0.029, 0.0185, 0.0120), (0.0081, 0.0075, 0.0068, 0.0061)),
        }
        self.segs = []
        self.knuckles = []
        for (u0, w0), splay, flex, L, r in F.values():
            self.segs += finger_chain((u0, w0, cn + 0.001), splay, flex, L, r)
            self.knuckles.append((np.array([u0, w0, cn + 0.0045]), r[0] * 0.95))
        # thumb, adducted along the index finger, a little toward the palm (CMC -> MCP -> IP -> tip)
        self.lim = u_cut + GAP + 0.004    # nothing but the stub reaches back past the seam (one clean loop there)
        T = [np.array(x) for x in ((max(0.020, u_cut + 0.015), 0.020, cn - 0.007), (0.058, 0.039, cn - 0.008), (0.087, 0.044, cn - 0.006),
                                   (0.108, 0.045, cn - 0.004))]
        Tr = (0.0125, 0.0105, 0.0094, 0.0082)
        self.thumb = [(T[i], T[i + 1], Tr[i], Tr[i + 1]) for i in range(3)]

    def stub(self, p):
        u = p[:, 0]
        t = smoothstep((u - self.u_cut) / max(self.u_s - 0.005 - self.u_cut, 1e-3))[:, None]
        c = self.c0 * (1 - t) + self.c1 * t
        q = p[:, 1:] - c
        rho = np.linalg.norm(q, axis=1)
        th = np.arctan2(q[:, 1], q[:, 0])
        nb = len(self.R0)
        x = (th + math.pi) / (2 * math.pi) * nb - 0.5
        i0 = np.floor(x).astype(int) % nb
        f = x - np.floor(x)
        i1 = (i0 + 1) % nb
        R0 = self.R0[i0] * (1 - f) + self.R0[i1] * f
        R1 = self.R1[i0] * (1 - f) + self.R1[i1] * f
        d = rho - (R0 * (1 - t[:, 0]) + R1 * t[:, 0])
        return np.maximum(d, np.maximum(self.u_cut - 0.012 - u, u - self.u_s))

    def palm(self, p):
        u, w, n = p[:, 0], p[:, 1], p[:, 2] - self.cn
        hw = 0.027 + (0.041 - 0.027) * smoothstep(u / 0.06)
        ht = 0.018 + (0.0128 - 0.018) * smoothstep(u / 0.05)
        r = 0.9 * ht
        qx, qy = np.abs(w) - (hw - r), np.abs(n) - (ht - r)
        d2 = np.hypot(np.maximum(qx, 0), np.maximum(qy, 0)) + np.minimum(np.maximum(qx, qy), 0) - r
        u1 = 0.090 + 0.006 * (1 - np.clip(w / 0.041, -1, 1) ** 2)    # the knuckle line, the middle finger's furthest
        du = np.maximum(u - u1, max(self.u_cut, 0.0) - 0.005 - u)
        return np.hypot(np.maximum(d2, 0), np.maximum(du, 0)) + np.minimum(np.maximum(d2, du), 0)

    def __call__(self, p):
        cn = self.cn
        back = self.lim - p[:, 0]
        d = smin(self.stub(p), np.maximum(self.palm(p), back), 0.012)
        pads = np.minimum(ellipsoid(p, np.array([0.036, 0.022, cn - 0.005]), np.array([0.030, 0.017, 0.0105])),     # thenar
                          ellipsoid(p, np.array([0.045, -0.026, cn - 0.004]), np.array([0.036, 0.0125, 0.0095])))   # hypothenar
        d = smin(d, np.maximum(pads, back), 0.008)
        th = np.maximum(np.min([round_cone(p, *s) for s in self.thumb], axis=0), back)
        d = smin(d, th, 0.007)
        fing = np.min([round_cone(p, *s) for s in self.segs], axis=0)
        d = smin(d, fing, 0.004)
        for c, r in self.knuckles:
            d = smin(d, np.linalg.norm(p - c, axis=1) - r, 0.004)
        return d


# --------------------------------------------------------------------------------------------- surface nets (numpy)
def surface_nets(sdf, lo, hi, h):
    """Quad mesh of sdf = 0 on a regular grid: one vertex per sign-changing cell (the mean of its edge crossings,
    then projected onto the surface), one quad per sign-changing grid edge. Quads face outward (+gradient)."""
    axes = [np.arange(lo[i], hi[i] + h * 0.5, h) for i in range(3)]
    shape = tuple(len(a) for a in axes)
    G = np.stack(np.meshgrid(*axes, indexing="ij"), -1)
    flat = G.reshape(-1, 3)
    f = np.concatenate([sdf(flat[i:i + 400000]) for i in range(0, len(flat), 400000)]).reshape(shape)
    inside = f < 0
    cs = tuple(s - 1 for s in shape)
    acc = np.zeros(cs + (3,))
    cnt = np.zeros(cs)
    for ax in range(3):
        sl0 = [slice(None)] * 3
        sl1 = [slice(None)] * 3
        sl0[ax], sl1[ax] = slice(0, -1), slice(1, None)
        f0, f1 = f[tuple(sl0)], f[tuple(sl1)]
        ch = (f0 < 0) != (f1 < 0)
        t = np.where(ch, f0 / np.where(ch, f0 - f1, 1), 0)
        P = G[tuple(sl0)] + t[..., None] * (G[tuple(sl1)] - G[tuple(sl0)])
        o = [a for a in range(3) if a != ax]
        for da in (0, 1):
            for db in (0, 1):
                sl = [slice(None)] * 3
                sl[o[0]] = slice(da, da + cs[o[0]])
                sl[o[1]] = slice(db, db + cs[o[1]])
                acc += (P * ch[..., None])[tuple(sl)]
                cnt += ch[tuple(sl)]
    active = cnt > 0
    idx = -np.ones(cs, int)
    idx[active] = np.arange(active.sum())
    V = acc[active] / cnt[active][:, None]
    quads = []
    for ax in range(3):
        o = [(ax + 1) % 3, (ax + 2) % 3]
        sl0 = [slice(None)] * 3
        sl1 = [slice(None)] * 3
        sl0[ax], sl1[ax] = slice(0, -1), slice(1, None)
        ch = inside[tuple(sl0)] != inside[tuple(sl1)]
        neg = inside[tuple(sl0)]
        e = np.argwhere(ch)
        e = e[(e[:, o[0]] >= 1) & (e[:, o[0]] <= cs[o[0]] - 1) & (e[:, o[1]] >= 1) & (e[:, o[1]] <= cs[o[1]] - 1)]
        if not len(e):
            continue

        def cell(da, db):
            c = e.copy()
            c[:, o[0]] -= da
            c[:, o[1]] -= db
            return idx[c[:, 0], c[:, 1], c[:, 2]]

        q = np.stack([cell(1, 1), cell(0, 1), cell(0, 0), cell(1, 0)], 1)
        flip = ~neg[e[:, 0], e[:, 1], e[:, 2]]
        q[flip] = q[flip][:, ::-1]
        quads.append(q)
    Q = np.concatenate(quads)
    for _ in range(4):                               # Newton steps onto the surface
        g = np.stack([(sdf(V + dv) - sdf(V - dv)) / (2 * 2e-4) for dv in np.eye(3) * 2e-4], 1)
        V = V - (sdf(V) / np.maximum((g * g).sum(1), 1e-12))[:, None] * g
    # orientation check: the quad normal along the gradient
    g = np.stack([(sdf(V + dv) - sdf(V - dv)) for dv in np.eye(3) * 2e-4], 1)
    nq = np.cross(V[Q[:, 2]] - V[Q[:, 0]], V[Q[:, 3]] - V[Q[:, 1]])
    if (np.einsum("ij,ij->i", nq, g[Q].mean(1)) < 0).mean() > 0.5:
        Q = Q[:, ::-1]
    return V, Q


# ------------------------------------------------------------------------------------------------------- mesh helpers
def local(P, O, E):
    return (np.asarray(P) - O) @ E


def world(L, O, E):
    return np.asarray(L) @ E.T + O


def boundary_loop(edges):
    """Order a closed chain of boundary edges into one vertex loop (raises if it is not exactly one loop)."""
    adj = {}
    for e in edges:
        a, b = e.verts
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)
    if any(len(v) != 2 for v in adj.values()):
        raise RuntimeError(f"boundary is not a simple loop ({sorted(len(v) for v in adj.values())[-3:]})")
    start = next(iter(adj))
    loop, prev, cur = [start], None, start
    while True:
        a, b = adj[cur]
        nxt = a if prev is None or b is prev else b
        if nxt is start:
            break
        loop.append(nxt)
        prev, cur = cur, nxt
    if len(loop) != len(adj):
        raise RuntimeError(f"boundary has {len(adj)} vertices but the loop only {len(loop)}: more than one hole")
    return loop


def section_perimeter(bm, O, E, u):
    """Perimeter (m) of the arm's section at u (the plane's intersection with the faces near the axis)."""
    co = np.array([v.co[:] for v in bm.verts])
    L = local(co, O, E)
    ev = np.array([[e.verts[0].index, e.verts[1].index] for e in bm.edges])
    a, b = L[ev[:, 0]], L[ev[:, 1]]
    cross = (a[:, 0] - u) * (b[:, 0] - u) < 0
    near = (np.hypot(a[:, 1], a[:, 2]) < 0.09) & (np.hypot(b[:, 1], b[:, 2]) < 0.09)
    hit = {}
    for i in np.where(cross & near)[0]:
        t = (u - a[i, 0]) / (b[i, 0] - a[i, 0])
        hit[i] = (a[i] + t * (b[i] - a[i]))[1:]
    per = 0.0
    for f in bm.faces:
        pts = [hit[e.index] for e in f.edges if e.index in hit]
        if len(pts) == 2:
            per += float(np.linalg.norm(pts[0] - pts[1]))
    return per


def outline(P2, nb=180):
    """Centre (centroid) and radius per angle bin of a closed 2D polygon (ray from the centre, outermost hit)."""
    c = P2.mean(0)
    th = -math.pi + (np.arange(nb) + 0.5) * 2 * math.pi / nb
    R = np.zeros(nb)
    A, B = P2 - c, np.roll(P2, -1, 0) - c
    for k, t in enumerate(th):
        d = np.array([math.cos(t), math.sin(t)])
        best = 0.0
        for a, b in zip(A, B):
            M = np.array([d, a - b]).T
            if abs(np.linalg.det(M)) < 1e-12:
                continue
            s, r = np.linalg.solve(M, a)
            if s > 0 and -1e-9 <= r <= 1 + 1e-9:
                best = max(best, s)
        R[k] = best
    R = (np.roll(R, 1) + 2 * R + np.roll(R, -1)) / 4    # a touch of smoothing
    return c, R


def uv_islands(faces, uvl):
    """Island id per face (faces sharing an edge with the same UVs on both ends are one island)."""
    parent = {f.index: f.index for f in faces}
    fset = set(parent)

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def uv_at(f, v):
        for lp in f.loops:
            if lp.vert is v:
                return lp[uvl].uv.to_tuple(6)

    for f in faces:
        for e in f.edges:
            for g in e.link_faces:
                if g is not f and g.index in fset and all(uv_at(f, v) == uv_at(g, v) for v in e.verts):
                    parent[find(f.index)] = find(g.index)
    return {i: find(i) for i in parent}


def uv_stretch(faces, uvl):
    """Per triangle (fan): texel density sqrt(s1 s2) (UV units per metre) and anisotropy s1 / s2 of the 3D -> UV map."""
    dens, aniso = [], []
    for f in faces:
        ls = f.loops
        for i in range(1, len(ls) - 1):
            P = [np.array(ls[k].vert.co[:]) for k in (0, i, i + 1)]
            T = [np.array(ls[k][uvl].uv[:]) for k in (0, i, i + 1)]
            e1, e2 = P[1] - P[0], P[2] - P[0]
            x = e1 / (np.linalg.norm(e1) + 1e-12)
            nrm = np.cross(e1, e2)
            if np.linalg.norm(nrm) < 1e-12:
                continue
            y = np.cross(nrm / np.linalg.norm(nrm), x)
            S = np.array([[e1 @ x, e2 @ x], [e1 @ y, e2 @ y]])
            Tm = np.array([T[1] - T[0], T[2] - T[0]]).T
            try:
                J = Tm @ np.linalg.inv(S)
            except np.linalg.LinAlgError:
                continue
            s = np.linalg.svd(J, compute_uv=False)
            dens.append(math.sqrt(max(s[0] * s[1], 0)))
            aniso.append(s[0] / max(s[1], 1e-9))
    return np.array(dens), np.array(aniso)


# ------------------------------------------------------------------------------------------------------- the remodel
def remodel(mesh, arm):
    boxes = hand_boxes(arm)
    me = mesh.data
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.verts.ensure_lookup_table()
    uvl = bm.loops.layers.uv[0]
    dl = bm.verts.layers.deform.active
    gidx = {g.name: g.index for g in mesh.vertex_groups}
    mw = np.array(mesh.matrix_world)
    assert np.allclose(mw, np.eye(4)), "the mesh object is expected at the origin, unscaled"
    newl = bm.faces.layers.int.new("_newhand")
    celll = bm.faces.layers.int.new("_uvpatch")
    skin_tris, densities = [], []
    report = {}
    n_scan = len(bm.verts)
    for hand in HANDS:
        side = hand[-1]
        fore = f"forearm_{side}"
        B = boxes[hand]
        O, E = B["O"], B["E"]
        bm.verts.index_update()
        bm.edges.index_update()
        bm.faces.index_update()
        # -- 2. the cut: the narrowest section between 8 cm before and 4 cm past the wrist joint
        us = np.round(np.arange(-0.08, 0.0401, 0.005), 4)
        pers = [section_perimeter(bm, O, E, u) for u in us]
        u_cut = float(us[int(np.argmin(pers))]) + 1e-4
        co = np.array([v.co[:] for v in bm.verts])
        L = local(co, O, E)
        rad = np.hypot(L[:, 1], L[:, 2])
        # the scan's hand beyond the cut: its skin texels and texel density are what the new hand's UVs reuse
        old_removed = [v for v in bm.verts if L[v.index, 0] > u_cut and rad[v.index] < 0.15 and L[v.index, 0] < 0.35]
        old_ext = np.array([L[v.index] for v in old_removed])
        gone = {v.index for v in old_removed}
        hand_faces = [f for f in bm.faces if all(v.index in gone for v in f.verts)]
        dens0, aniso0 = uv_stretch(hand_faces, uvl)
        skin_tris.extend([[tuple(lp[uvl].uv) for lp in f.loops] for f in hand_faces])
        densities.append(float(np.median(dens0)))
        # -- bisect at u_cut and delete the hand
        geom_f = [f for f in bm.faces if any(abs(L[v.index, 0] - u_cut) < 0.03 and rad[v.index] < 0.09 for v in f.verts)]
        geom = list({e for f in geom_f for e in f.edges}) + geom_f + list({v for f in geom_f for v in f.verts})
        bmesh.ops.bisect_plane(bm, geom=geom, dist=1e-7, plane_co=Vector(O + u_cut * E[:, 0]), plane_no=Vector(E[:, 0]))
        kill = []
        for v in bm.verts:
            lv = local(np.array(v.co[:]), O, E)
            if lv[0] > u_cut + 1e-6 and math.hypot(lv[1], lv[2]) < 0.15 and lv[0] < 0.35:
                kill.append(v)
        bmesh.ops.delete(bm, geom=kill, context="VERTS")
        loopA = boundary_loop([e for e in bm.edges if e.is_boundary])
        LA = local(np.array([v.co[:] for v in loopA]), O, E)
        if np.abs(LA[:, 0] - u_cut).max() > 1e-4:
            raise RuntimeError(f"{hand}: the cut loop is not planar")
        hA = np.array([v[dl].get(gidx[hand], 0.0) for v in loopA])
        # -- 3. the new hand
        c0, R0 = outline(LA[:, 1:])
        sdf = HandSDF(u_cut, c0, R0)
        lo = np.array([u_cut - 0.02, min(-0.055, LA[:, 1].min() - 0.012), min(-0.04, LA[:, 2].min() - 0.012)])
        hi = np.array([0.205, max(0.075, LA[:, 1].max() + 0.012), max(0.04, LA[:, 2].max() + 0.012)])
        V, Q = surface_nets(sdf, lo, hi, GRID)
        tmp_me = bpy.data.meshes.new(f"_{hand}_nets")
        tmp_me.from_pydata(world(V, O, E).tolist(), [], Q.tolist())
        tmp = bpy.data.objects.new(f"_{hand}_nets", tmp_me)
        bpy.context.scene.collection.objects.link(tmp)
        rem = tmp.modifiers.new("remesh", "REMESH")   # voxel remesh: a clean manifold surface (surface nets can pinch
        rem.mode = "VOXEL"                              # where two fingers almost touch)
        rem.voxel_size = GRID * 1.2
        rem.adaptivity = 0.0
        dg = bpy.context.evaluated_depsgraph_get()
        n_rem = len(tmp.evaluated_get(dg).data.polygons)
        dec = tmp.modifiers.new("dec", "DECIMATE")
        dec.decimate_type = "COLLAPSE"
        dec.ratio = min(1.0, TARGET_FACES / (2.0 * n_rem))
        dg = bpy.context.evaluated_depsgraph_get()
        ev = tmp.evaluated_get(dg)
        hb = bmesh.new()
        hb.from_mesh(ev.to_mesh())
        ev.to_mesh_clear()
        bpy.data.objects.remove(tmp)
        bpy.data.meshes.remove(tmp_me)
        bmesh.ops.triangulate(hb, faces=hb.faces[:])
        nm_edges = sum(1 for e in hb.edges if not e.is_manifold)
        bmesh.ops.bisect_plane(hb, geom=hb.verts[:] + hb.edges[:] + hb.faces[:], dist=1e-7,
                               plane_co=Vector(O + (u_cut + GAP) * E[:, 0]), plane_no=Vector(E[:, 0]), clear_inner=True)
        # -- copy the new hand into the body mesh, then zip the two loops
        vmap = {}
        for v in hb.verts:
            vmap[v] = bm.verts.new(v.co)
        new_faces, dup = [], 0
        for f in hb.faces:
            try:
                new_faces.append(bm.faces.new([vmap[v] for v in f.verts]))
            except ValueError:   # the decimation left two triangles on the same three vertices: keep one
                dup += 1
        new_verts = list(vmap.values())
        hb.free()
        nset = set(new_verts)
        loopB = boundary_loop([e for e in bm.edges if e.is_boundary and e.verts[0] in nset])
        bridge = zip_loops(bm, loopA, loopB, O, E, c0)
        new_faces += bridge
        for f in new_faces:
            f.smooth = True
            f.material_index = 0
        # -- relax the seam: new vertices within 2 cm of the cut, the scan's loop fixed
        seam = [v for v in new_verts if local(np.array(v.co[:]), O, E)[0] < u_cut + 0.02]
        for _ in range(6):
            pos = {}
            for v in seam:
                nb = [e.other_vert(v).co for e in v.link_edges]
                pos[v] = v.co.lerp(sum(nb, Vector()) / len(nb), 0.5)
            for v, p in pos.items():
                v.co = p
        # -- 4. weights
        for v in new_verts:
            u = local(np.array(v.co[:]), O, E)[0]
            target = float(smoothstep((u + 0.03) / 0.06))
            ease = float(np.clip((u - u_cut) / 0.015, 0, 1))
            k = min(range(len(loopA)), key=lambda i: (loopA[i].co - v.co).length_squared)
            h = (1 - ease) * hA[k] + ease * target
            dv = v[dl]
            dv.clear()
            if h > 1e-4:
                dv[gidx[hand]] = h
            if 1 - h > 1e-4:
                dv[gidx[fore]] = 1 - h
        # -- 5. UVs: done for both hands after the merge (place_uvs); keep the scan hand's skin texels and density
        for f in new_faces:   # the UV patch: ~3 cm cells of the hand frame x the face's main direction
            f[newl] = 1
            lc = local(np.array(f.calc_center_median()[:]), O, E)
            nl = E.T @ np.array(f.normal[:])
            ax = int(np.argmax(np.abs(nl)))
            cell = np.floor(lc / PATCH).astype(int) + 50
            f[celll] = (HANDS.index(hand) * 6 + ax * 2 + int(nl[ax] > 0)) * 1000000 + cell[0] * 10000 + cell[1] * 100 + cell[2]
        nfaces = len(new_faces)
        report[hand] = dict(
            cut_u_mm=round(u_cut * 1000, 1), cut_perimeter_cm=round(min(pers) * 100, 1),
            cut_loop_verts=len(loopA), removed_scan_verts=len(old_removed),
            scan_hand_extent_u_mm=round(float(old_ext[:, 0].max()) * 1000, 1), new_verts=len(new_verts),
            new_faces=nfaces, nets_quads=int(len(Q)), nets_nonmanifold_edges_after_decimate=nm_edges, duplicate_faces_dropped=dup,
            scan_uv_anisotropy=dict(median=round(float(np.median(aniso0)), 2), p90=round(float(np.percentile(aniso0, 90)), 2)))
        print(f"[open_hands] {hand}: {json.dumps(report[hand])}", flush=True)
    nonman = sum(1 for e in bm.edges if not e.is_manifold)
    bnd = sum(1 for e in bm.edges if e.is_boundary)
    bm.to_mesh(me)
    bm.free()
    me.update()   # the scan's custom normals stay (dropping them turns some normals by up to 75 deg); new corners get 0 = auto
    report["uv"] = place_uvs(mesh, skin_tris, float(np.median(densities)))
    me.attributes.remove(me.attributes["_newhand"])
    me.attributes.remove(me.attributes["_uvpatch"])
    report["mesh"] = dict(scan_verts=n_scan, verts=len(me.vertices), faces=len(me.polygons), nonmanifold_edges=nonman,
                          boundary_edges=bnd)
    return report


def raster(tris, S):
    """Coverage of UV triangles on an S x S grid (row = v * S, Blender's bottom-up pixel order)."""
    img = np.zeros((S, S), bool)
    for t in tris:
        P = np.array(t) * S - 0.5
        x0, y0 = np.floor(P.min(0)).astype(int)
        x1, y1 = np.ceil(P.max(0)).astype(int)
        x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, S - 1), min(y1, S - 1)
        if x1 < x0 or y1 < y0:
            continue
        for k in range(1, len(P) - 1):
            (ax, ay), (bx, by), (cx, cy) = P[0], P[k], P[k + 1]
            den = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
            if abs(den) < 1e-12:
                continue
            xs, ys = np.meshgrid(np.arange(x0, x1 + 1), np.arange(y0, y1 + 1))
            l1 = ((by - cy) * (xs - cx) + (cx - bx) * (ys - cy)) / den
            l2 = ((cy - ay) * (xs - cx) + (ax - cx) * (ys - cy)) / den
            img[y0:y1 + 1, x0:x1 + 1] |= (l1 >= -0.02) & (l2 >= -0.02) & (1 - l1 - l2 >= -0.02)
    return img


def grow(m, n):
    for _ in range(n):
        m = m | np.roll(m, 1, 0) | np.roll(m, -1, 0) | np.roll(m, 1, 1) | np.roll(m, -1, 1)
    return m


PLACE = 2048     # px, the grid the charts are placed on (half the 4096 texture: 1 px here = 2 texels)


def place_uvs(mesh, skin_tris, density):
    """UVs for the new faces (attribute _newhand): patches of ~3 cm (attribute _uvpatch) unwrapped angle-based (low
    distortion), each scaled to the scan
    hand's texel density and moved, whole, onto the scan's own hand skin in the texture (an FFT search for a spot where
    the chart, grown by 3 px, lies on skin only: inside the scan hand's UV triangles, colour within the skin's spread,
    as deep inside the skin as possible). Charts may share texels (the texture is only read). Returns stats."""
    me = mesh.data
    # the skin mask: the scan hand's texels whose colour is near the skin's median (no cuff, nail or crease texels)
    img = next(n.image for m in me.materials for n in m.node_tree.nodes if n.type == "TEX_IMAGE" and n.image
               and n.image.colorspace_settings.name.lower().startswith("srgb"))
    w, h = img.size
    px = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(px)
    rgb = px.reshape(h, w, 4)[..., :3].reshape(PLACE, h // PLACE, PLACE, w // PLACE, 3).mean((1, 3))
    del px
    cover = raster(skin_tris, PLACE)
    med = np.median(rgb[cover], 0)
    dist = np.linalg.norm(rgb - med, axis=2)
    lum = rgb.mean(2)
    skin = cover & (dist < np.percentile(dist[cover], SKIN_PCT)) & (lum > np.percentile(lum[cover], 25))   # no creases
    skin = ~grow(~grow(skin, 1), 3)                         # close pinholes, then keep 3 px off every edge
    depth = np.zeros((PLACE, PLACE))                        # how far inside the skin (px, up to 16)
    m = skin.copy()
    for _ in range(16):
        depth += m
        m = ~grow(~m, 1)
    if os.environ.get("OPEN_HANDS_DEBUG"):
        np.savez_compressed(os.environ["OPEN_HANDS_DEBUG"], cover=cover, skin=skin, rgb=rgb.astype(np.float16))
    Fbad, Fdep = np.fft.rfft2((~skin).astype(float)), np.fft.rfft2(depth)
    # Smart UV Project on the new faces only
    bpy.context.view_layer.objects.active = mesh
    for o in bpy.context.view_layer.objects:
        o.select_set(o is mesh)
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(me)
    lay = bm.faces.layers.int["_newhand"]
    cl = bm.faces.layers.int["_uvpatch"]
    for e in bm.edges:
        fs = e.link_faces
        if any(f[lay] for f in fs):
            e.seam = len({f[cl] if f[lay] else -1 for f in fs}) > 1
    for f in bm.faces:
        f.select_set(bool(f[lay]))
    bmesh.update_edit_mesh(me)
    bpy.ops.uv.unwrap(method="ANGLE_BASED", fill_holes=True, correct_aspect=True, margin=0.0)
    bpy.ops.object.mode_set(mode="OBJECT")
    bm = bmesh.new()
    bm.from_mesh(me)
    uvl = bm.loops.layers.uv[0]
    lay = bm.faces.layers.int["_newhand"]
    faces = [f for f in bm.faces if f[lay]]
    bm.faces.index_update()
    isl = uv_islands(faces, uvl)
    charts = {}
    for f in faces:
        charts.setdefault(isl[f.index], []).append(f)
    scales = []
    for fs in sorted(charts.values(), key=len, reverse=True):
        loops = [lp for f in fs for lp in f.loops]
        U = np.array([lp[uvl].uv[:] for lp in loops])
        a3 = sum(f.calc_area() for f in fs)
        auv = 0.0
        for f in fs:
            t = np.array([lp[uvl].uv[:] for lp in f.loops])
            auv += 0.5 * abs(np.cross(t[1] - t[0], t[2] - t[0]))
        k = density / max(math.sqrt(auv / max(a3, 1e-12)), 1e-9)
        done = False
        for shrink in (1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4):
            for rot in range(4):
                R = np.linalg.matrix_power(np.array([[0, -1], [1, 0]]), rot)
                V = (U - U.mean(0)) @ R.T * k * shrink
                V -= V.min(0) - 4.0 / PLACE
                ext = V.max(0) + 4.0 / PLACE
                if (ext * PLACE).max() >= PLACE - 2:
                    continue
                tris, i = [], 0
                for f in fs:
                    tris.append(V[i:i + len(f.loops)])
                    i += len(f.loops)
                cm = grow(raster(tris, PLACE), 2).astype(float)
                Fc = np.conj(np.fft.rfft2(cm))
                over = np.fft.irfft2(Fbad * Fc, s=(PLACE, PLACE))
                score = np.fft.irfft2(Fdep * Fc, s=(PLACE, PLACE))
                wx, wy = int(PLACE - ext[0] * PLACE), int(PLACE - ext[1] * PLACE)
                ok = np.zeros((PLACE, PLACE), bool)
                ok[:wy, :wx] = over[:wy, :wx] < 0.5
                if not ok.any():
                    continue
                ty, tx = np.unravel_index(np.argmax(np.where(ok, score, -1e9)), ok.shape)
                V = V + np.array([tx, ty]) / PLACE
                used = np.roll(np.roll(cm > 0, ty, 0), tx, 1)
                depth[used] *= 0.5        # the next patches prefer other skin: less visible repetition
                Fdep = np.fft.rfft2(depth)
                for lp, uv in zip(loops, V):
                    lp[uvl].uv = uv
                scales.append(shrink)
                done = True
                break
            if done:
                break
        if not done:
            raise RuntimeError(f"place_uvs: a chart of {len(fs)} faces fits nowhere on the hand skin")
    dens, aniso = uv_stretch(faces, uvl)
    bm.to_mesh(me)
    bm.free()
    return dict(charts=len(charts), skin_texels_2048=int(skin.sum()), density_scan_uv_per_m=round(density, 3),
                density_vs_scan=dict(median=round(float(np.median(dens) / density), 3),
                                     p10=round(float(np.percentile(dens, 10) / density), 3),
                                     p90=round(float(np.percentile(dens, 90) / density), 3)),
                anisotropy=dict(median=round(float(np.median(aniso)), 2), p90=round(float(np.percentile(aniso, 90)), 2)),
                charts_shrunk=int(sum(1 for x in scales if x < 1)), smallest_scale=min(scales))


def zip_loops(bm, A, B, O, E, c):
    """Triangles between two closed loops around the hand axis (A at the cut, B 4 mm past it), walking both by angle
    around the centre c (w, n); faces oriented outward."""
    def ang(loop):
        L = local(np.array([v.co[:] for v in loop]), O, E)
        return np.arctan2(L[:, 2] - c[1], L[:, 1] - c[0])

    def orient(loop):
        """Counter-clockwise about +u, starting at the smallest angle; angles unwrapped from there."""
        if np.unwrap(ang(loop))[-1] < np.unwrap(ang(loop))[0]:
            loop = loop[::-1]
        k = int(np.argmin(np.mod(ang(loop), 2 * math.pi)))
        loop = loop[k:] + loop[:k]
        a = np.unwrap(ang(loop))
        return loop, a - a[0] + (ang(loop)[0] % (2 * math.pi))

    A, ta = orient(A)
    B, tb = orient(B)
    ta = np.append(ta, ta[0] + 2 * math.pi)
    tb = np.append(tb, tb[0] + 2 * math.pi)
    i = j = 0
    nA, nB = len(A), len(B)
    faces = []
    while i < nA or j < nB:
        if j >= nB or (i < nA and ta[i + 1] < tb[j + 1]):
            tri = (A[i % nA], A[(i + 1) % nA], B[j % nB])
            i += 1
        else:
            tri = (A[i % nA], B[(j + 1) % nB], B[j % nB])
            j += 1
        if len(set(tri)) < 3:
            continue
        P = [np.array(v.co[:]) for v in tri]
        nrm = np.cross(P[1] - P[0], P[2] - P[0])
        cen = sum(P) / 3
        Lc = local(cen, O, E)
        out = E[:, 1] * (Lc[1] - c[0]) + E[:, 2] * (Lc[2] - c[1])
        if nrm @ out < 0:
            tri = tri[::-1]
        try:
            faces.append(bm.faces.new(tri))
        except ValueError:
            pass
    return faces


def head_state(mesh):
    """{rounded rest position: (CheekDent_L/R displacement (2, 3), _CHEEKMASK)} for the head and neck vertices."""
    me = mesh.data
    kb = me.shape_keys.key_blocks
    gs = {mesh.vertex_groups[n].index for n in ("head", "neck")}
    mask = me.attributes["_CHEEKMASK"].data
    out = {}
    for v in me.vertices:
        if any(g.group in gs and g.weight > 0.3 for g in v.groups):
            b = kb["Basis"].data[v.index].co
            disp = np.array([(kb[k].data[v.index].co - b)[:] for k in ("CheekDent_L", "CheekDent_R")])
            out[tuple(round(x, 6) for x in v.co)] = (disp, mask[v.index].value)
    return out


def main():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    if not SCAN.exists():
        bpy.ops.wm.open_mainfile(filepath=str(SRC))
        if any("open_hands" in o for o in bpy.data.objects):
            raise SystemExit(f"{SRC.name} already has open hands and {SCAN.name} is missing: restore it from git")
        shutil.copy2(SRC, SCAN)
        print(f"[open_hands] kept the scan's hands in {SCAN.name}")
    bpy.ops.wm.open_mainfile(filepath=str(SCAN))
    bpy.context.preferences.filepaths.save_version = 0
    arm, mesh = rig_and_mesh()
    head = head_state(mesh)
    mesh.shape_key_clear()
    report = remodel(mesh, arm)
    stats, _ = add_cheek_morphs(mesh)
    after = head_state(mesh)
    shared = set(head) & set(after)
    report["cheeks"] = dict(keys=[k.name for k in mesh.data.shape_keys.key_blocks], head_verts=len(head),
                            head_verts_unmoved=len(shared),
                            dent_max_diff_m=max(float(np.abs(head[k][0] - after[k][0]).max()) for k in shared),
                            mask_max_diff=max(abs(head[k][1] - after[k][1]) for k in shared),
                            dent_verts={k: stats[k]["verts"] for k in ("CheekDent_L", "CheekDent_R")})
    mesh["open_hands"] = "Scripts/blender_open_hands.py 2026-11-04"
    out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else SRC   # --out: a test copy elsewhere
    if "--no-save" not in argv:
        bpy.ops.wm.save_mainfile(filepath=str(out))
    if "--no-export" not in argv:
        report["exports"] = export("grandpa", arm, mesh, web="--no-web" not in argv)
    print("[open_hands] REPORT " + json.dumps(report))


if __name__ == "__main__":
    main()
