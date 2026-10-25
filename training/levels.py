"""Skill ladder (Blueprint 7): what each level trains on and how it passes.

`roles` is the share of worlds playing each role (0 guard, +1 strike, -1 absorb; envs/slap.py): one brain keeps every
role it has learned (2026-10-22: strike-only L2 training had wiped out the guard).
`reward_target[role]` is mean episode return over 100 consecutive episodes of that role (AGENTS.md graduation; a 30 s
guard episode is 1500 control steps, so 2400 = standing the whole time at 1.6 reward per step);
`check[role]` is that role's pass/fail exam, run on the deterministic policy before an ONNX is exported. A level passes
only when every role in it passes.
"""
GUARD = dict(episodes=100, min_no_fall=0.95, min_in_box=0.95)
STRIKE = dict(episodes=100, min_hit=0.90, min_no_fall=0.90)        # hit = any touch, >= 8 m/s, up 1.5 s after
# and the striker's feet stay in the box (2026-10-23: Kim scored 94 % clean, 99 % up, then stepped out in every arena turn)
STRIKE["min_in_box"] = 0.90
ABSORB = dict(episodes=100, min_absorbed=0.90)                       # up 5 s after a median slap, COM back within 5 cm

LEVELS = {
    "L0": dict(  # Stand: upright 30 s, feet planted, survives random pushes
        roles={0: 1.0}, seconds=30.0, pushes=True,
        reward_target={0: 2400.0},
        check={0: dict(episodes=100, min_no_fall=0.95)},
    ),
    "L1": dict(  # Guard hold: stand in the foot box facing the opponent, 30 s
        roles={0: 1.0}, seconds=30.0, pushes=True, foot_box=(0.20, 0.25),
        max_push=0.6,  # 90-180 N. User 2026-10-21: full 300 N head shoves with planted feet are beyond a 0.5-strength
                       # grandpa (stalled at 60 %); real slap impacts are tested in L3.
        reward_target={0: 2500.0},
        check={0: GUARD},
    ),
    "L2": dict(  # Strike a fixed head target: palm first, >= 8 m/s, striker stays up, inside the 30 s clock
        roles={1: 0.7, 0: 0.3}, seconds=30.0, pushes=True, foot_box=(0.20, 0.25), max_push=0.6,
        hit_speed=8.0, hit_speed_start=2.0,   # curriculum: clean-hit speed bar 2 -> 8 m/s (stuck at 1 m/s taps without it)
        reach_start=1.0,  # the real head from the start: the striker imitates the reference slap (slap_demo.py), which reaches it
        imit_start=1.0,   # imitation weight per brain step, faded by the trainer as clean hits come (floor 0.2)
        reward_target={1: 100.0, 0: 2500.0},  # a clean 8 m/s hit pays 114 (v3); the exam is what counts
        check={1: STRIKE, 0: GUARD},
    ),
    "L3": dict(  # Absorb one scripted slap of rising impulse; COM back within 5 cm in 5 s
        roles={-1: 0.6, 1: 0.4}, seconds=30.0, pushes=False, foot_box=(0.20, 0.25), max_push=0.6,
        hit_speed=8.0,
        slap_impulse=15.0,        # N s, the median slap (exam). Estimate: Grandpa's palm at 8 m/s, ~2.8 kg of hand+forearm+
                                  # upper arm into ~4.5 kg of head on the neck, e = 0.2: 1.73 kg x 8 m/s x 1.2 = 16.6 N s
        slap_impulse_start=3.0,   # curriculum 3 -> 15 N s (training slaps are 0.6-1.4 x the current value)
        reward_target={-1: 800.0, 1: 100.0},   # absorb: ~360 steps at ~2 + the COM term after the slap
        check={-1: ABSORB, 1: STRIKE},
    ),
    "L4": dict(  # Duel, self-play in the arena (envs/arena.py): trained striker vs trained defender, one brain
        model="arena", roles={1: 0.5, -1: 0.5}, seconds=30.0, pushes=False, foot_box=(0.20, 0.25), max_push=0.6,
        hit_speed=8.0,
        reward_target={1: 100.0, -1: 300.0},
        check=dict(episodes=100, ko_rate=(0.10, 0.40), max_self_fall=0.05, min_strikes=0.90),   # KO rate per landed strike
    ),
    "L5": dict(  # Full match: 3 rounds, clocks, judges; Unreal replay must match
        model="arena", seconds=None, pushes=False, role=None,
        check=dict(matches=100, max_blowups=0),
    ),
    # 3-Stage Natural Human Locomotion & Realism Milestones (training/envs/locomotion.py)
    "S1": dict(  # Stage 1: Stand upright and recover balance from randomized shoves (150-300 N)
        model="locomotion", roles={0: 1.0}, seconds=30.0, pushes=True, max_push=0.6,
        reward_target={0: 2400.0},
        check={0: dict(episodes=100, min_no_fall=0.95, min_absorbed=0.95)},
    ),
    "S2": dict(  # Stage 2: Take steady, natural alternating heel-to-toe steps and walk at 1.0-1.4 m/s
        model="locomotion", roles={0: 1.0}, seconds=20.0, pushes=False, target_speed=(1.0, 1.4),
        reward_target={0: 1800.0},
        check={0: dict(episodes=100, min_no_fall=0.95)},
    ),
    "S3": dict(  # Stage 3: Jog (2.0-3.2 m/s), turn smoothly, stop smoothly, and handle +/- 8 deg uneven slopes
        model="locomotion", roles={0: 1.0}, seconds=20.0, pushes=False, target_speed=(2.0, 3.2), max_slope_deg=8.0,
        reward_target={0: 1600.0},
        check={0: dict(episodes=100, min_no_fall=0.92)},
    ),
}
