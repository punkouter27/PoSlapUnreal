"""The articulated athletes' physics contract (Po Game Standard): Earth gravity, joint limits, PD actuators, no
collision between connected bodies, ground friction, primitive colliders and eye contact (gaze tracking through the
neck joints). Checked on the arena the match engines load (training/models, MuJoCo 3.10.0)."""

import json
import math

import mujoco
import numpy as np
import pytest
from conftest import TRAINING

ARENA = TRAINING / "models" / "grandpa_vs_kim_arena.xml"
PRIMITIVES = {
    mujoco.mjtGeom.mjGEOM_PLANE,
    mujoco.mjtGeom.mjGEOM_SPHERE,
    mujoco.mjtGeom.mjGEOM_CAPSULE,
    mujoco.mjtGeom.mjGEOM_BOX,
}


@pytest.fixture(scope="module")
def model():
    return mujoco.MjModel.from_xml_path(str(ARENA))


def test_earth_gravity_in_the_model_and_every_fingerprint(model):
    assert np.allclose(model.opt.gravity, [0, 0, -9.81])
    for fp in (TRAINING / "models").glob("*_fingerprint.json"):
        assert json.loads(fp.read_text(encoding="utf-8"))["option"]["gravity"] == [0.0, 0.0, -9.81], fp.name


def test_every_hinge_is_limited(model):
    hinges = [j for j in range(model.njnt) if model.jnt_type[j] == mujoco.mjtJoint.mjJNT_HINGE]
    assert len(hinges) == 60  # 30 per athlete
    assert all(model.jnt_limited[j] and model.jnt_range[j][0] < model.jnt_range[j][1] for j in hinges)


def test_actuators_are_force_limited_pd_servos(model):
    # position servo: force = kp * (goal - q) - kv * qdot  ->  gain kp, bias [0, -kp, -kv]
    assert model.nu == 60
    for a in range(model.nu):
        kp = model.actuator_gainprm[a][0]
        assert model.actuator_gaintype[a] == mujoco.mjtGain.mjGAIN_FIXED and kp > 0
        assert model.actuator_biastype[a] == mujoco.mjtBias.mjBIAS_AFFINE
        assert model.actuator_biasprm[a][1] == pytest.approx(-kp) and model.actuator_biasprm[a][2] < 0  # P and D
        assert model.actuator_forcelimited[a] and model.actuator_forcerange[a][1] > 0


def test_connected_bodies_do_not_collide(model):
    """Parent-child filtering is on (MuJoCo's default; never disabled here): at the stand pose no contact joins a body
    and its parent, while non-adjacent bodies still collide (contype/conaffinity set on the athletes' geoms)."""
    assert not model.opt.disableflags & mujoco.mjtDisableBit.mjDSBL_FILTERPARENT
    assert not model.opt.disableflags & mujoco.mjtDisableBit.mjDSBL_CONTACT
    d = mujoco.MjData(model)
    mujoco.mj_forward(model, d)
    for c in d.contact[: d.ncon]:
        b1, b2 = model.geom_bodyid[c.geom[0]], model.geom_bodyid[c.geom[1]]
        assert model.body_parentid[b1] != b2 and model.body_parentid[b2] != b1
    athlete = [g for g in range(model.ngeom) if model.body_rootid[model.geom_bodyid[g]] != 0]
    assert all(model.geom_contype[g] or model.geom_conaffinity[g] for g in athlete)


def test_ground_friction_and_primitive_colliders(model):
    floor = model.geom("floor").id
    assert model.geom_friction[floor][0] >= 0.8  # sliding friction on the floor
    assert {int(t) for t in model.geom_type} <= {int(t) for t in PRIMITIVES}


def test_gaze_tracking_turns_the_neck_toward_the_opponent(model, monkeypatch):
    """Eye contact (match.py gaze_*, the same maths as Unreal's ApplyGaze and sim.js): the neck-turn goal walks toward
    the opponent's eyes by at most GAZE_MAX_STEP per decision and stays inside the joint's range."""
    monkeypatch.chdir(TRAINING)
    import match
    from constants import GAZE_MAX_STEP, GAZE_ON

    assert GAZE_ON
    m, fps, pre = match.load(None, "grandpa", "kim")
    game = match.Match(m, fps, pre, (None, None), 0, log=None, seed=1)
    d = game.d
    mujoco.mj_forward(m, d)
    side = game.sides[0]
    want = game.gaze_desire(side, m, d)
    # start looking 90 degrees away (to the side), then let the servo walk back to the eye line
    side.wbias = np.cross(want, [0.0, 0.0, 1.0])
    side.wbias = list(side.wbias / np.linalg.norm(side.wbias))
    angles = []
    for _ in range(40):
        game.gaze_apply(side, m, d)
        angles.append(math.acos(max(-1.0, min(1.0, float(np.dot(side.wbias, want))))))
    steps = -np.diff([math.pi / 2, *angles])
    assert np.all(steps <= GAZE_MAX_STEP + 1e-9)  # never a snap
    assert angles[-1] < 1e-6  # on the eye line
    i = side.i_yaw
    assert side.a.lo[i] <= side.a.goal[i] <= side.a.hi[i]
