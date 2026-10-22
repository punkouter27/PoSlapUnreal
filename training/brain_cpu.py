"""A brain driving athletes in CPU MuJoCo (double precision), built exactly like Unreal's USlapController:
same observation (DOCS/BRAIN_IO.md), same action rule, same timing. Used for checkpoint videos and for the
live half of the matching check (Blueprint 8).

    .venv/Scripts/python.exe brain_cpu.py video models/brains/grandpa_L0.onnx out.gif
"""
import json, math, sys
from pathlib import Path

import mujoco
import numpy as np

HERE = Path(__file__).parent
OBS = 130                          # DOCS/BRAIN_IO.md (108 before memory and fatigue; older brains read the first 108)
LAGS = (5, 10, 15)                 # memory: inputs 102-107 as they were 0.1, 0.2, 0.3 s ago (a wind-up, a feint)
MIN_STAMINA, MIN_NECK = 0.7, 0.5   # fatigue floors (the card sets how fast an athlete gets there)


def card(fp):
    """The character card (training/characters/<name>.json): the source of truth, the fingerprint keeps a build-time copy."""
    return json.loads((HERE / "characters" / f"{fp['card']['name']}.json").read_text())


class Athlete:
    def __init__(self, m, fp, prefix=""):
        self.order = fp["joint_order"]
        J = [m.joint(prefix + n).id for n in self.order]
        self.act = np.array([m.actuator(prefix + n).id for n in self.order])
        self.qa, self.va = m.jnt_qposadr[J], m.jnt_dofadr[J]
        root = m.joint(prefix + "root").id
        self.rq, self.rv = m.jnt_qposadr[root], m.jnt_dofadr[root]
        self.pelvis = m.body(prefix + "pelvis").id
        self.box = m.qpos0[self.rq:self.rq + 2].copy()   # foot-box centre = start pelvis x, y
        self.stand = np.array([fp["stand_pose_rad"].get(n, 0.0) for n in self.order])   # exact, as Unreal reads it
        self.lo, self.hi = np.array(fp["ctrl_range_rad"]).T   # listed in joint order (as Unreal reads it)
        self.max_step = fp["max_joint_speed_rad_s"] * 0.02
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
        return o.astype(np.float32)

    def decide(self, action):
        a = np.clip(np.asarray(action, float), -1.0, 1.0)
        t = np.clip(self.stand + a * 0.5 * (self.hi - self.lo), self.lo, self.hi)
        step = self.max_step * self.stamina   # fatigue: a tired athlete moves slower
        self.goal = np.clip(t, self.goal - step, self.goal + step)
        self.prev = a.astype(np.float32)


def stand(m, d, fp):
    mujoco.mj_resetData(m, d)
    for i in range(m.njnt):
        for j, rad in fp["stand_pose_rad"].items():
            if m.joint(i).name.endswith(j):
                d.qpos[m.jnt_qposadr[i]] = rad
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
        mujoco.mj_step(m, d)
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
        return a.obs(d, role, 1 - k * 0.005 / 30, opp_head, opp_hand(k * 0.005, head0) if role == -1 else None)
    d = rollout(m, fp, policy, seconds=seconds, on_step=grab, obs=obs, setup=setup)
    frames[0].save(out, save_all=True, append_images=frames[1:], duration=40, loop=0)
    return float(d.qpos[2])


if __name__ == "__main__":
    if sys.argv[1] == "video":
        print("final pelvis z", video(onnx_policy(sys.argv[2]), sys.argv[3]))
