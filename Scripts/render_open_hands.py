r"""Before/after check of Grandpa's open hands (Scripts/blender_open_hands.py): stills and the drawn-palm vs hand-box angles.

    "C:\Program Files\Blender Foundation\Blender 5.2\blender.exe" -b --factory-startup --python Scripts/render_open_hands.py -- <in.blend> <out_dir> [poses.json]

poses.json: Scripts/open_hands_poses.py (default Saved/open_hands/grandpa_poses.json): MuJoCo body frames for bind
(the T-pose the mesh is skinned in), stand and a real slap contact. The rig is driven the way Scripts/grandpa_v2.py
does it (MuJoCo body -> bone through the rigid map Blender rest -> qpos0) and each hand box rides on its hand bone.

Measured per pose and hand, on the skinned (evaluated) mesh: the hand's vertices (hand-bone weight >= 0.5) in the
box's frame, principal axes: finger axis = the largest axis vs the box's long axis, palm normal = the smallest axis vs
the box's thin axis (degrees, sign-free), and how far the drawn hand sits outside the box (p90 of the vertices'
distance outside, mm, as Scripts/collision_mesh_clip.py). Writes <out_dir>/angles.json and PNG stills
(<pose>_<view>.png) with the boxes drawn as wireframes. Nothing is saved to the .blend.
"""
import json
import sys
from pathlib import Path

import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from blender_open_hands import HANDS, hand_boxes  # noqa: E402
from blender_cheek_morphs import rig_and_mesh  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ARGV = sys.argv[sys.argv.index("--") + 1:]
SRC, OUT = Path(ARGV[0]), Path(ARGV[1])
POSES = Path(ARGV[2]) if len(ARGV) > 2 else ROOT / "Saved/open_hands/grandpa_poses.json"
OUT.mkdir(parents=True, exist_ok=True)

bpy.ops.wm.open_mainfile(filepath=str(SRC))
arm, mesh = rig_and_mesh()
if mesh.data.shape_keys:
    for k in mesh.data.shape_keys.key_blocks:
        k.value = 0.0
boxes = hand_boxes(arm)
J = json.loads(POSES.read_text())
BODIES = J["bodies"]


def T(p):
    return Matrix.Translation(Vector(p[:3])) @ Quaternion(p[3:]).to_matrix().to_4x4()


REST = {b.name: b.matrix_local.copy() for b in arm.data.bones}
H = np.array([list(arm.data.bones[n].head_local) for n in BODIES])
P = np.array([p[:3] for p in J["bind"]])
hc, pc = H.mean(0), P.mean(0)
U, _, Vt = np.linalg.svd((H - hc).T @ (P - pc))
Rk = (U @ np.diag([1, 1, np.sign(np.linalg.det(U @ Vt))]) @ Vt).T
G = Matrix.Translation(Vector(pc - Rk @ hc)) @ Matrix([list(r) for r in Rk]).to_4x4()
Gi = G.inverted()
T0i = [T(p).inverted() for p in J["bind"]]
ORDER = []


def walk(b):
    ORDER.append(b.name)
    for c in b.children:
        walk(c)


for b in arm.data.bones:
    if not b.parent:
        walk(b)


def apply_pose(frame):
    Pm = {n: Gi @ T(frame[i]) @ T0i[i] @ G @ REST[n] for i, n in enumerate(BODIES)}
    out = {}
    for n in ORDER:
        b, pb = arm.data.bones[n], arm.pose.bones[n]
        Rp = b.parent.matrix_local if b.parent else Matrix.Identity(4)
        Pp = out[b.parent.name] if b.parent else Matrix.Identity(4)
        Pb = Pm.get(n, Pp @ Rp.inverted() @ b.matrix_local)
        out[n] = Pb
        loc, rot, _ = ((Rp.inverted() @ b.matrix_local).inverted() @ Pp.inverted() @ Pb).decompose()
        pb.rotation_mode = "QUATERNION"
        pb.location, pb.rotation_quaternion, pb.scale = loc, rot, (1, 1, 1)
    bpy.context.view_layer.update()


# the box overlays: a cube per hand (wireframe), placed every pose from its hand bone
sc = bpy.context.scene
box_obj = {}
for hand in HANDS:
    me = bpy.data.meshes.new(f"box_{hand}")
    me.from_pydata([(x, y, z) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)], [],
                   [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)])
    ob = bpy.data.objects.new(f"box_{hand}", me)
    sc.collection.objects.link(ob)
    w = ob.modifiers.new("wire", "WIREFRAME")     # a 1.5 mm frame: the box's edges, the hand visible inside
    w.thickness = 0.0015
    w.use_even_offset = False
    w.use_relative_offset = False
    mat = bpy.data.materials.new(f"M_box_{hand}")
    mat.diffuse_color = (0.1, 1.0, 0.2, 1.0)
    me.materials.append(mat)
    box_obj[hand] = ob


def box_rest(hand):
    B = boxes[hand]
    M = Matrix.Identity(4)
    for i in range(3):
        for j in range(3):
            M[i][j] = B["axes"][i, j] * B["size"][j]
        M[i][3] = B["centre"][i]
    return M


box_M = {}
CORNERS = [Vector((x, y, z)) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]


def place_boxes():
    """The box rides on its hand bone; its corners are written in world space (the wire keeps its 1.5 mm)."""
    for hand in HANDS:
        pb = arm.pose.bones[hand]
        box_M[hand] = arm.matrix_world @ pb.matrix @ REST[hand].inverted() @ box_rest(hand)
        for v, c in zip(box_obj[hand].data.vertices, CORNERS):
            v.co = box_M[hand] @ c
        box_obj[hand].data.update()
    bpy.context.view_layer.update()


gi = {g.index: g.name for g in mesh.vertex_groups}
hand_ids = {h: np.array([v.index for v in mesh.data.vertices
                         if any(gi[g.group] == h and g.weight >= 0.5 for g in v.groups)]) for h in HANDS}


def measure():
    dg = bpy.context.evaluated_depsgraph_get()
    ev = mesh.evaluated_get(dg)
    em = ev.to_mesh()
    co = np.array([v.co[:] for v in em.vertices])
    ev.to_mesh_clear()
    mw = np.array(mesh.matrix_world)
    co = co @ mw[:3, :3].T + mw[:3, 3]
    out = {}
    for hand in HANDS:
        Mb = np.array(box_M[hand])
        A = Mb[:3, :3] / np.linalg.norm(Mb[:3, :3], axis=0)
        c = Mb[:3, 3]
        size = boxes[hand]["size"]
        q = (co[hand_ids[hand]] - c) @ A          # in the box's frame (x wide, y long, z thin)
        w, V = np.linalg.eigh(np.cov(q.T))
        e_long, e_thin = V[:, 2], V[:, 0]
        ang = lambda a, b: float(np.degrees(np.arccos(min(1.0, abs(a @ b)))))
        outside = np.max(np.abs(q) - size, axis=1)
        out[hand] = dict(finger_axis_deg=round(ang(e_long, np.array([0, 1, 0])), 1),
                         palm_normal_deg=round(ang(e_thin, np.array([0, 0, 1])), 1),
                         outside_box_p90_mm=round(float(np.percentile(np.maximum(outside, 0), 90)) * 1000, 1),
                         inside_box_pct=round(100 * float((outside <= 0).mean()), 1), verts=int(len(q)))
    return out


# --- render setup
for o in [o for o in bpy.data.objects if o.type in ("CAMERA", "LIGHT")]:
    bpy.data.objects.remove(o)
cam = bpy.data.objects.new("Cam", bpy.data.cameras.new("Cam"))
sc.collection.objects.link(cam)
sc.camera = cam
sc.render.engine = "BLENDER_WORKBENCH"
sc.render.resolution_x = sc.render.resolution_y = 720
sh = sc.display.shading
sh.light = "STUDIO"
sh.color_type = "TEXTURE"
sh.show_cavity = True
sh.cavity_type = "BOTH"
sc.view_settings.view_transform = "Standard"
sc.render.film_transparent = False
sc.world = sc.world or bpy.data.worlds.new("W")


def look(centre, direction, up, scale, path):
    """Orthographic camera at centre - direction, looking along direction, image-up = up."""
    d = Vector(direction).normalized()
    u = Vector(up)
    u = (u - u.dot(d) * d).normalized()
    r = d.cross(u)
    R = Matrix((r, u, -d)).transposed()
    cam.data.type = "ORTHO"
    cam.data.ortho_scale = scale
    cam.matrix_world = Matrix.Translation(Vector(centre) - d * 2.0) @ R.to_4x4()
    sc.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)


def axes_now(hand):
    Mb = np.array(box_M[hand])
    A = Mb[:3, :3] / np.linalg.norm(Mb[:3, :3], axis=0)
    return Mb[:3, 3], A


report = {}
shots = []
for pose in ("bind", "stand", "slap"):
    apply_pose(J[pose])
    place_boxes()
    report[pose] = measure()
    for box in box_obj.values():
        box.hide_render = False
    if pose == "bind":
        for hand in HANDS:
            c, A = axes_now(hand)
            # top: down the box's thin axis (back of the hand), fingers to the image's side; edge: along the long axis
            look(c, -A[:, 2] * np.sign(A[2, 2]), -A[:, 0] if hand == "hand_r" else A[:, 0], 0.30, OUT / f"bind_{hand}_top.png")
            look(c, A[:, 0] * (1 if hand == "hand_r" else -1), A[:, 2] * np.sign(A[2, 2]), 0.30, OUT / f"bind_{hand}_front.png")
            look(c, -A[:, 1] * np.sign(A[:, 1] @ (c - boxes[hand]["O"])), A[:, 2] * np.sign(A[2, 2]), 0.20,
                 OUT / f"bind_{hand}_end.png")
            shots += [f"bind_{hand}_{v}.png" for v in ("top", "front", "end")]
        look((0, 0, 1.15), (0, 1, 0), (0, 0, 1), 2.0, OUT / "bind_body.png")
        shots.append("bind_body.png")
    elif pose == "stand":
        look((0, 0, 0.95), (0, 1, 0), (0, 0, 1), 2.0, OUT / "stand_body.png")
        for hand in HANDS:
            c, A = axes_now(hand)
            look(c, (0.0, 1.0, 0.0), (0, 0, 1), 0.32, OUT / f"stand_{hand}.png")
            shots.append(f"stand_{hand}.png")
        shots.append("stand_body.png")
    else:
        c, A = axes_now("hand_r")
        look(c, -A[:, 2], A[:, 1], 0.32, OUT / "slap_hand_r_palm.png")       # square onto the palm
        look(c, A[:, 0], A[:, 2], 0.32, OUT / "slap_hand_r_edge.png")        # edge-on: the palm plane as a line
        look(c, -A[:, 2] + A[:, 0] + 0.6 * A[:, 1], A[:, 1], 0.30, OUT / "slap_hand_r_34.png")   # three-quarter
        pel = np.array(arm.matrix_world @ arm.pose.bones["pelvis"].head)
        look((pel + c) / 2 + np.array([0, 0, 0.2]), (0.0, 1.0, -0.15), (0, 0, 1), 1.6, OUT / "slap_body.png")
        shots += ["slap_hand_r_palm.png", "slap_hand_r_edge.png", "slap_hand_r_34.png", "slap_body.png"]
report["slap_info"] = J.get("slap_info")
(OUT / "angles.json").write_text(json.dumps(dict(report=report, shots=shots), indent=1))
print("[render_open_hands] " + json.dumps(report))
