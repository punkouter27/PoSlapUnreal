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
        self.goal, self.prev = self.stand.copy(), np.zeros(len(self.order))

    def obs(self, d, role=0.0, clock=0.0, target=None):
        o = np.zeros(108)
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
        else:              # striker: turn clock left, target (the defender's head centre) - pelvis, heading frame
            o[101] = clock
            r = target - d.xpos[self.pelvis]
            o[102:105] = [c * r[0] + s * r[1], -s * r[0] + c * r[1], r[2]]
        return o.astype(np.float32)

    def decide(self, action):
        a = np.clip(np.asarray(action, float), -1.0, 1.0)
        t = np.clip(self.stand + a * 0.5 * (self.hi - self.lo), self.lo, self.hi)
        self.goal = np.clip(t, self.goal - self.max_step, self.goal + self.max_step)
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
    return lambda o: s.run(None, {"obs": o[None]})[0][0]


def rollout(m, fp, policy, prefixes=("",), seconds=5.0, on_step=None):
    """Same order as Unreal: every 4th physics step kinematics -> obs -> brain -> new goals; ctrl = goals; mj_step."""
    d = mujoco.MjData(m)
    stand(m, d, fp)
    ath = [Athlete(m, fp, p) for p in prefixes]
    for k in range(int(seconds / m.opt.timestep)):
        if k % 4 == 0:
            mujoco.mj_kinematics(m, d)
            for a in ath:
                a.decide(policy(a.obs(d)))
        for a in ath:
            d.ctrl[a.act] = a.goal
        if on_step:
            on_step(k, d)
        mujoco.mj_step(m, d)
    return d


def video(policy, out, character="grandpa", seconds=6.0, size=(360, 480)):
    """A GIF of the brain standing in CPU MuJoCo (pushes off), for reviewing a checkpoint without Unreal."""
    from PIL import Image
    fp = json.loads((HERE / "models" / f"{character}_fingerprint.json").read_text())
    m = mujoco.MjModel.from_xml_path(str(HERE / "models" / f"{character}_solo.xml"))
    r = mujoco.Renderer(m, size[1], size[0])
    cam = mujoco.MjvCamera(); cam.azimuth, cam.elevation, cam.distance = 120, -12, 3.0
    frames = []

    def grab(k, d):
        if k % 8 == 0:   # 25 fps
            cam.lookat[:] = [d.qpos[0], d.qpos[1], 0.9]
            r.update_scene(d, cam); frames.append(Image.fromarray(r.render()))
    d = rollout(m, fp, policy, seconds=seconds, on_step=grab)
    frames[0].save(out, save_all=True, append_images=frames[1:], duration=40, loop=0)
    return float(d.qpos[2])


if __name__ == "__main__":
    if sys.argv[1] == "video":
        print("final pelvis z", video(onnx_policy(sys.argv[2]), sys.argv[3]))
