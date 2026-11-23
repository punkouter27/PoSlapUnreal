"""Reward penalty weights and curriculum thresholds, read from configs/reward_config.json.

One source for the trainer (envs/locomotion.py, envs/slap.py, levels.py); the Unreal DataAsset USlapRewardConfig
mirrors the same numbers (tests/test_reward_config.py keeps them equal). Every weight is added to the per-step reward,
so a penalty weight is negative.

    penalty(joint_torque, joint_vel, action, prev_action, foot_speed, foot_contact)   -> the weighted penalty sum
    TermLog: each env adds its step's terms; train.py logs the per-term means (pen_<term>) once per iteration

The arrays may be numpy or torch (batched on the last axis). Changing a weight does not retrain the shipped brains.
"""

import json
from pathlib import Path

PATH = Path(__file__).resolve().parent / "configs" / "reward_config.json"
_CFG = json.loads(PATH.read_text(encoding="utf-8"))

PENALTIES = dict(_CFG["penalties"])  # torque_sq, joint_ang_vel_sq, action_rate_sq, foot_slip
FOOT_SLIP_DEADBAND = float(_CFG["foot_slip_deadband_mps"])
CURRICULUM = list(_CFG["curriculum"])  # [{"stage", "threshold", "metric", "role_targets"}], ladder order

W_TORQUE_SQ = PENALTIES["torque_sq"]
W_JOINT_ANG_VEL_SQ = PENALTIES["joint_ang_vel_sq"]
W_ACTION_RATE_SQ = PENALTIES["action_rate_sq"]
W_FOOT_SLIP = PENALTIES["foot_slip"]


def role_targets(stage):
    """levels.py's reward_target for a stage: {role: mean episode return over 100 episodes}."""
    for row in CURRICULUM:
        if row["stage"] == stage:
            return {int(role): float(v) for role, v in row["role_targets"].items()}
    raise KeyError(f"no curriculum stage {stage!r} in {PATH.name}")


def terms(joint_torque, joint_vel, action, prev_action, foot_speed, foot_contact):
    """The four penalty terms (unweighted), each summed over its last axis.

    joint_torque (..., nj) N m, joint_vel (..., nj) rad/s, action / prev_action (..., na), foot_speed (..., nf) planar
    m/s, foot_contact (..., nf) bool: the sole is on the floor."""
    slip = (foot_speed - FOOT_SLIP_DEADBAND).clip(min=0.0)
    return {
        "torque_sq": (joint_torque**2).sum(-1),
        "joint_ang_vel_sq": (joint_vel**2).sum(-1),
        "action_rate_sq": ((action - prev_action) ** 2).sum(-1),
        "foot_slip": (foot_contact * slip).sum(-1),
    }


def penalty(joint_torque, joint_vel, action, prev_action, foot_speed, foot_contact):
    """The weighted sum of the four penalty terms (negative): add it to the reward."""
    t = terms(joint_torque, joint_vel, action, prev_action, foot_speed, foot_contact)
    return sum(PENALTIES[k] * v for k, v in t.items())


class TermLog:
    """Each penalty term tracked on its own: the weighted mean over the batch, summed per step and averaged when read.

    The envs call add() every step (no host sync: the sums stay tensors on the GPU); train.py calls pop() once per
    iteration and logs pen_torque_sq, pen_joint_ang_vel_sq, pen_action_rate_sq and pen_foot_slip (reward per step,
    negative) to TensorBoard and telemetry.jsonl."""

    def __init__(self):
        self.sums, self.n = {}, 0

    def add(self, t):
        for k, v in t.items():
            x = PENALTIES[k] * v.mean()
            self.sums[k] = self.sums[k] + x if k in self.sums else x
        self.n += 1

    def pop(self):
        out = {f"pen_{k}": float(v) / self.n for k, v in self.sums.items()} if self.n else {}
        self.sums, self.n = {}, 0
        return out
