"""A brain driving athletes in CPU MuJoCo (double precision), built exactly like Unreal's USlapController:
same observation (DOCS/BRAIN_IO.md), same action rule, same timing. Used for checkpoint videos and for the
live half of the matching check (Blueprint 8).

    .venv/Scripts/python.exe brain_cpu.py video models/brains/grandpa_L0.onnx out.gif
"""
import json, math, sys
from pathlib import Path

import mujoco
import numpy as np

from constants import HILL_K, LAGS, MIN_NECK, MIN_STAMINA, OBS, REACT   # the shared numbers (DOCS/BRAIN_IO.md)

HERE = Path(__file__).parent
# OBS, LAGS, REACT, HILL_K, MIN_STAMINA and MIN_NECK live in constants.py (one source of truth for the three
# engines); they are re-exported here so `from brain_cpu import OBS` keeps working.


def card(fp):
    """The character card (training/characters/<name>.json): the source of truth, the fingerprint keeps a build-time copy."""
    return json.loads((HERE / "characters" / f"{fp['card']['name']}.json").read_text())


# ---------------------------------------------------------------- the streamlined sensory view (Option 5)
# The 136 floats carry redundancy: the 18 memory floats repeat inputs 102-107, the palm facing (133-135) is
# implied by the palm centre and the arm joints, and the previous action (70-99) is a second copy of the
# command the brain has just sent.  Changing the layout would break the three-engine parity (Blueprint 8),
# so the pruning happens INSIDE the network's input layer instead: a fixed linear projection (PCA over a
# sample of real observations) maps 136 -> N before the first hidden layer.  The projection is baked into the
# exported ONNX, so Unreal's NNE and the browser's onnxruntime-web run exactly the same graph and need no
# change at all: the ONNX input stays [1, 136].
#
# summarise() is the same idea in readable form: a named, redundant-free summary of the observation, used by
# the reference path (this module) for logs and by the trainer for the projection's warm-up statistics.

SUMMARY_FIELDS = (
    "pelvis_z", "gravity_x", "gravity_y", "gravity_z",          # 60-63: height and the tilt of the body
    "v_fwd", "v_lat", "v_up",                                  # 64-66: the velocity in the heading frame
    "roll", "pitch", "yaw_rate",                               # 67-69: the angular velocity (scaled)
    "action_mean", "action_std",                               # 70-99: the previous action, summarised
    "role", "clock",                                           # 100-101
    "target_fwd", "target_lat", "target_up",                   # 102-104: the target / foot box
    "hand_fwd", "hand_lat", "hand_up",                         # 105-107: the opponent's hand (6 decisions late)
    "mem_strike_time", "mem_strike_speed",                     # 126-127
    "stamina", "neck_strength",                                # 128-129
    "palm_fwd", "palm_lat", "palm_up",                         # 130-132
    "palm_facing_x", "palm_facing_y", "palm_facing_z",         # 133-135
)


def summarise(obs):
    """A compact, redundant-free view of the 136-float observation (numpy, one row or a batch).

    31 numbers instead of 136: the state that actually drives a decision, with the duplicated channels
    (the joint-by-joint history, the lagged memory) collapsed into their statistics.  Used for logs and for
    the projection's warm-up sample; the brain itself still reads the full 136 (the ONNX input is [1, 136])."""
    o = np.atleast_2d(np.asarray(obs, float))
    n = o.shape[-1]
    a = o[:, 70:100]
    out = np.stack([
        o[:, 60], o[:, 61], o[:, 62], o[:, 63],
        o[:, 64], o[:, 65], o[:, 66],
        o[:, 67], o[:, 68], o[:, 69],
        a.mean(-1), a.std(-1),
        o[:, 100], o[:, 101],
        o[:, 102], o[:, 103], o[:, 104],
        o[:, 105], o[:, 106], o[:, 107],
        o[:, 126], o[:, 127],
        o[:, 128], o[:, 129],
        o[:, 130], o[:, 131], o[:, 132],
        o[:, 133], o[:, 134], o[:, 135],
    ], -1)
    assert out.shape[-1] == len(SUMMARY_FIELDS) == 31, (out.shape, len(SUMMARY_FIELDS))
    assert n >= 136, f"the observation is {n} floats, expected 136"
    return out


def pca_projection(sample, n_components):
    """The fixed linear map (136 -> n_components) that prunes the redundant channels: the principal
    directions of a sample of real observations, mean-centred.

    Returns (mean, components) so the map is x -> (x - mean) @ components.T.  The trainer bakes both into
    the exported ONNX (the network's first layer), which is why Unreal and the browser need no change."""
    s = np.atleast_2d(np.asarray(sample, float))
    mean = s.mean(0)
    _, _, vt = np.linalg.svd(s - mean, full_matrices=False)
    k = int(min(n_components, vt.shape[0]))
    return mean.astype(np.float32), vt[:k].T.astype(np.float32)


class Athlete:
    def __init__(self, m, fp, prefix=""):
        self.order = fp["joint_order"]
        J = [m.joint(prefix + n).id for n in self.order]
        self.act = np.array([m.actuator(prefix + n).id for n in self.order])
        self.qa, self.va = m.jnt_qposadr[J], m.jnt_dofadr[J]
        root = m.joint(prefix + "root").id
        self.rq, self.rv = m.jnt_qposadr[root], m.jnt_dofadr[root]
        self.pelvis = m.body(prefix + "pelvis").id
        self.hand = m.geom(prefix + "hand_r").id
        self.box = m.qpos0[self.rq:self.rq + 2].copy()   # foot-box centre = start pelvis x, y
        self.stand = np.array([fp["stand_pose_rad"].get(n, 0.0) for n in self.order])   # exact, as Unreal reads it
        self.lo, self.hi = np.array(fp["ctrl_range_rad"]).T   # listed in joint order (as Unreal reads it)
        self.vmax = np.broadcast_to(np.asarray(fp["max_joint_speed_rad_s"], float), (len(self.order),))   # per joint
        self.max_step = self.vmax * 0.02   # (a scalar in older fingerprints)
        self.neck = np.array([a for a, n in zip(self.act, self.order) if n.startswith("neck")])
        self.goal, self.prev = self.stand.copy(), np.zeros(len(self.order))
        self.hist = []                       # inputs 102-107 of earlier decisions this turn (memory)
        self.mem = (0.0, 0.0)                # the opponent's last strike this match: touch time / 30, palm speed / 10
        self.stamina, self.neck_strength = 1.0, 1.0

    def obs(self, d, role=0.0, clock=0.0, target=None, hand=None):
        o = np.zeros(OBS)
        N = len(self.order)
        o[:N] = d.qpos[self.qa] - self.stand
        o[N:2 * N] = d.qvel[self.va] * 0.1
        R = np.zeros(9); mujoco.mju_quat2Mat(R, d.qpos[self.rq + 3:self.rq + 7])
        yaw = math.atan2(R[3], R[0]); c, s = math.cos(yaw), math.sin(yaw)
        v = d.qvel[self.rv:self.rv + 3]
        o[60] = d.xpos[self.pelvis][2]
        o[61:64] = -R[6:9]
        o[64:67] = [c * v[0] + s * v[1], -s * v[0] + c * v[1], v[2]]
        o[67:70] = d.qvel[self.rv + 3:self.rv + 6] * 0.25
        o[70:100] = self.prev
        o[100] = role
        if role == 0:      # own foot-box centre (start pelvis x, y) - pelvis, heading frame; opponent hand stays zero
            bx, by = self.box[0] - d.qpos[self.rq], self.box[1] - d.qpos[self.rq + 1]
            o[102:105] = [c * bx + s * by, -s * bx + c * by, 0.0]
        else:              # striker / defender: turn clock left, opponent's head centre - pelvis, heading frame
            o[101] = clock
            r = target - d.xpos[self.pelvis]
            o[102:105] = [c * r[0] + s * r[1], -s * r[0] + c * r[1], r[2]]
        if hand is not None:   # defender: the opponent's right hand - pelvis, heading frame
            r = hand - d.xpos[self.pelvis]
            o[105:108] = [c * r[0] + s * r[1], -s * r[0] + c * r[1], r[2]]
        self.hist = (self.hist or [o[102:108].copy()] * 16)[-15:] + [o[102:108].copy()]
        for i, lag in enumerate(LAGS):
            o[108 + 6 * i:114 + 6 * i] = self.hist[-1 - lag]
        o[126:128] = self.mem
        o[128], o[129] = self.stamina, self.neck_strength
        r, n = d.geom_xpos[self.hand] - d.xpos[self.pelvis], -d.geom_xmat[self.hand].reshape(3, 3)[:, 2]   # own palm: centre, facing
        o[130:133] = [c * r[0] + s * r[1], -s * r[0] + c * r[1], r[2]]
        o[133:136] = [c * n[0] + s * n[1], -s * n[0] + c * n[1], n[2]]
        return o.astype(np.float32)

    def decide(self, action):
        a = np.clip(np.asarray(action, float), -1.0, 1.0)
        t = np.clip(self.stand + np.where(a > 0, a * (self.hi - self.stand), a * (self.stand - self.lo)), self.lo, self.hi)   # a = +-1: the range ends
        step = self.max_step * self.stamina   # fatigue: a tired athlete moves slower
        self.goal = np.clip(t, self.goal - step, self.goal + step)
        self.prev = a.astype(np.float32)


def hill(fr, v, vmax):
    """Hill's force-velocity law on each motor's forcerange (lo, hi): pushing the way the joint already turns, a muscle
    has F0 (1 - u) / (1 + u / HILL_K) at u = |speed| / top speed (0 at the top speed); braking it keeps F0.
    The same arithmetic per step in envs (torch), Unreal (ApplyHill) and Web/sim.js (hill)."""
    u = np.minimum(np.abs(v) / vmax, 1.0)
    h = (1.0 - u) / (1.0 + u / HILL_K)
    out = fr.copy()
    out[:, 0] *= np.where(v < 0, h, 1.0)
    out[:, 1] *= np.where(v > 0, h, 1.0)
    return out


def muscle_vmax(m, athletes):
    """Top speed per actuator (rad/s, the fingerprint's max_joint_speed_rad_s); inf (no curve) for anything else."""
    v = np.full(m.nu, np.inf)
    for a in athletes:
        v[a.act] = a.vmax
    return v


def muscle_step(m, d, vmax):
    """mj_step with every motor's force capped by its joint's speed (hill), then the forceranges back as they were:
    the faint / neck-damage code keeps owning them."""
    fr = m.actuator_forcerange.copy()
    m.actuator_forcerange[:] = hill(fr, d.qvel[m.jnt_dofadr[m.actuator_trnid[:, 0]]], vmax)
    mujoco.mj_step(m, d)
    m.actuator_forcerange[:] = fr


def settle_muscles(m, d):
    """Every muscle's filter state (build_body MUSCLE_TC) = its joint's angle: a reset (act = 0) starts it settled, not
    pulling toward 0 rad. Unreal (SettleMuscles) and Web/sim.js do the same after each reset."""
    a = np.flatnonzero(m.actuator_actadr >= 0)
    d.act[m.actuator_actadr[a]] = d.qpos[m.jnt_qposadr[m.actuator_trnid[a, 0]]]


def stand(m, d, fp):
    mujoco.mj_resetData(m, d)
    for i in range(m.njnt):
        for j, rad in fp["stand_pose_rad"].items():
            if m.joint(i).name.endswith(j):
                d.qpos[m.jnt_qposadr[i]] = rad
    settle_muscles(m, d)
    mujoco.mj_forward(m, d)


def onnx_policy(path):
    import onnxruntime as ort
    s = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    n = s.get_inputs()[0].shape[1]   # brains from before memory read the first 108 numbers
    return lambda o: s.run(None, {"obs": o[None, :n]})[0][0]


def rollout(m, fp, policy, prefixes=("",), seconds=5.0, on_step=None, obs=None, setup=None):
    """Same order as Unreal: every 4th physics step kinematics -> obs -> brain -> new goals; ctrl = goals; mj_step."""
    d = mujoco.MjData(m)
    stand(m, d, fp)
    ath = [Athlete(m, fp, p) for p in prefixes]
    obs = obs or (lambda a, d, k: a.obs(d))
    vmax = muscle_vmax(m, ath)
    if setup:
        setup(d)
    for k in range(int(seconds / m.opt.timestep)):
        if k % 4 == 0:
            mujoco.mj_kinematics(m, d)
            for a in ath:
                a.decide(policy(obs(a, d, k)))
        for a in ath:
            d.ctrl[a.act] = a.goal
        if on_step:
            on_step(k, d)
        muscle_step(m, d, vmax)
    return d


def video(policy, out, character="grandpa", seconds=6.0, size=(360, 480), role=0, slap=15.0):
    """A GIF of the brain in CPU MuJoCo, for reviewing a checkpoint without Unreal. Role 0: standing (pushes off);
    +1: striking the target sphere where the opponent's head is; -1: one `slap` N s slap to the face at 2 s
    (the training env's scripted slap, envs/slap.py)."""
    from PIL import Image
    fp = json.loads((HERE / "models" / f"{character}_fingerprint.json").read_text())
    m = mujoco.MjModel.from_xml_path(str(HERE / "models" / f"{character}_solo.xml"))
    r = mujoco.Renderer(m, size[1], size[0])
    cam = mujoco.MjvCamera(); cam.azimuth, cam.elevation, cam.distance = 120, -12, 3.0
    frames = []
    head = m.body("head").id
    q0 = opp_head = head0 = None
    cheek, f = np.array([0.05, 0.07, -0.03]), np.array([-0.3, -1.0, 0.05])
    f = f / np.linalg.norm(f) * slap / 0.01

    def opp_hand(t, head0):   # rest -> wound up -> our left cheek at 2 s -> rest, as envs/slap.py opp_hand()
        rest, wind, hit = q0 + [0.80, 0.20, -0.05], q0 + [0.70, 0.60, 0.55], head0 + cheek
        t = t - 2.0
        if t < -0.15:
            return rest + np.clip((t + 0.6) / 0.45, 0, 1) * (wind - rest)
        if t < 0:
            return wind + np.clip((t + 0.15) / 0.15, 0, 1) * (hit - wind)
        return hit + np.clip(t / 0.5, 0, 1) * (rest - hit)

    def setup(d):
        nonlocal q0, opp_head, head0
        q0 = d.qpos[:3].copy(); opp_head = q0 + [0.8015, 0.0, 0.74]; head0 = d.xpos[head].copy()
        d.mocap_pos[0] = opp_head if role == 1 else q0 + [5.0, 0.0, 0.7]

    def grab(k, d):
        d.xfrc_applied[:] = 0
        if role == -1 and k in (400, 401):   # 2 s, 10 ms
            d.xfrc_applied[head, :3] = f
            d.xfrc_applied[head, 3:] = np.cross(cheek, f)
        if k % 8 == 0:   # 25 fps
            cam.lookat[:] = [d.qpos[0], d.qpos[1], 0.9]
            r.update_scene(d, cam); frames.append(Image.fromarray(r.render()))
    def obs(a, d, k):
        if role == 0:
            return a.obs(d)
        return a.obs(d, role, 1 - k * 0.005 / 30, opp_head, opp_hand(k * 0.005 - REACT * 0.02, head0) if role == -1 else None)   # seen 0.12 s late
    d = rollout(m, fp, policy, seconds=seconds, on_step=grab, obs=obs, setup=setup)
    frames[0].save(out, save_all=True, append_images=frames[1:], duration=40, loop=0)
    return float(d.qpos[2])


if __name__ == "__main__":
    if sys.argv[1] == "video":
        print("final pelvis z", video(onnx_policy(sys.argv[2]), sys.argv[3]))
