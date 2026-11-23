"""configs/reward_config.json is what the trainer uses (idea 13: one source, mirrored by the Unreal DataAsset)."""

import json
import re

import levels
import numpy as np
import pytest
import reward_config as rc
from conftest import ROOT

CFG = json.loads(rc.PATH.read_text(encoding="utf-8"))


def test_the_four_penalties_are_configured_and_negative():
    assert set(CFG["penalties"]) == {"torque_sq", "joint_ang_vel_sq", "action_rate_sq", "foot_slip"}
    assert all(w < 0 for w in CFG["penalties"].values())
    assert rc.PENALTIES == CFG["penalties"]


def test_levels_read_their_thresholds_from_the_file():
    stages = {row["stage"] for row in CFG["curriculum"]}
    with_target = {name for name, lv in levels.LEVELS.items() if "reward_target" in lv}
    assert with_target == stages
    for row in CFG["curriculum"]:
        target = levels.LEVELS[row["stage"]]["reward_target"]
        assert target == {int(k): v for k, v in row["role_targets"].items()}
        assert row["threshold"] in target.values()


def test_penalty_terms():
    torque, vel = np.array([[2.0, 1.0]]), np.array([[0.5, 0.5]])
    a, prev = np.array([[1.0, 0.0]]), np.array([[0.0, 0.0]])
    speed, on = np.array([[rc.FOOT_SLIP_DEADBAND + 0.5, 3.0]]), np.array([[True, False]])
    t = rc.terms(torque, vel, a, prev, speed, on)
    assert t["torque_sq"][0] == pytest.approx(5.0)
    assert t["joint_ang_vel_sq"][0] == pytest.approx(0.5)
    assert t["action_rate_sq"][0] == pytest.approx(1.0)
    assert t["foot_slip"][0] == pytest.approx(0.5)  # the airborne foot does not slip
    expect = sum(CFG["penalties"][k] * t[k][0] for k in t)
    assert rc.penalty(torque, vel, a, prev, speed, on)[0] == pytest.approx(expect)


@pytest.mark.parametrize("env", ["slap.py", "locomotion.py"])
def test_envs_have_no_literal_penalty_weights(env):
    """The weights come from reward_config, not from numbers typed into the env."""
    src = (ROOT / "training" / "envs" / env).read_text(encoding="utf-8")
    assert 'PENALTIES["action_rate_sq"] * smooth' in src
    assert 'PENALTIES["torque_sq"]' in src and 'PENALTIES["joint_ang_vel_sq"]' in src
    assert not re.search(r"-\s*0\.01\s*\*\s*smooth", src)
    assert not re.search(r"-\s*0\.30?\s*\*\s*leg_drag", src)


def test_each_penalty_term_is_logged_on_its_own():
    """TermLog keeps every term apart: the weighted batch mean per step, averaged over the steps when train.py reads it."""
    log = rc.TermLog()
    for k in (1.0, 3.0):  # two steps, two envs each
        log.add({name: np.array([k, k]) for name in CFG["penalties"]})
    out = log.pop()
    assert set(out) == {f"pen_{name}" for name in CFG["penalties"]}
    for name, w in CFG["penalties"].items():
        assert out[f"pen_{name}"] == pytest.approx(w * 2.0)
        assert out[f"pen_{name}"] < 0  # a penalty
    assert log.pop() == {}  # read once per iteration, then empty


@pytest.mark.parametrize("env", ["slap.py", "locomotion.py"])
def test_envs_log_every_penalty_term(env):
    """Both training envs feed all four terms to their TermLog every step, and train.py logs it each iteration."""
    src = (ROOT / "training" / "envs" / env).read_text(encoding="utf-8")
    assert "self.pen_log = TermLog()" in src and "self.pen_log.add(" in src
    if env == "locomotion.py":
        assert all(f'"{name}":' in src for name in CFG["penalties"])
    train = (ROOT / "training" / "train.py").read_text(encoding="utf-8")
    assert "m.update(pen_log.pop())" in train
