"""Skill ladder (Blueprint 7): what each level trains on and how it passes.

`reward_target` is mean episode return over 100 consecutive episodes (AGENTS.md graduation; a 30 s episode is
1500 control steps, so 2400 = standing the whole time at 1.6 reward per step);
`check` is the level's own pass/fail exam, run on the deterministic policy before an ONNX is exported.
Mechanics for L2+ (strike target, scripted hits, self-play, match) are added when the ladder reaches them.
"""

LEVELS = {
    "L0": dict(  # Stand: upright 30 s, feet planted, survives random pushes
        model="solo", seconds=30.0, pushes=True, role=0,
        reward_target=2400.0,
        check=dict(episodes=100, min_no_fall=0.95),
    ),
    "L1": dict(  # Guard hold: stand in the foot box facing the opponent, 30 s
        model="solo", seconds=30.0, pushes=True, role=0, foot_box=(0.20, 0.25),
        max_push=0.6,  # 90-180 N. User 2026-10-21: full 300 N head shoves with planted feet are beyond a 0.5-strength
                       # grandpa (stalled at 60 %); real slap impacts are tested in L3.
        reward_target=2500.0,
        check=dict(episodes=100, min_no_fall=0.95, min_in_box=0.95),
    ),
    "L2": dict(  # Strike a fixed head target: palm first, >= 8 m/s, striker stays up, inside the 30 s clock
        model="solo", seconds=30.0, pushes=False, role=1, foot_box=(0.20, 0.25),
        reward_target=100.0,  # a clean 8 m/s hit pays 114 (v3); the exam is what counts
        check=dict(episodes=100, min_hit=0.90, min_no_fall=0.90),   # hit = palm first, >= 8 m/s (envs/slap.py)
    ),
    "L3": dict(  # Absorb scripted strikes of rising impulse; COM back within 5 cm in 5 s
        model="arena", seconds=30.0, pushes=False, role=-1,
        check=dict(episodes=100, min_no_fall=0.90, max_restabilize_s=5.0, max_com_cm=5.0),
    ),
    "L4": dict(  # Duel, self-play
        model="arena", seconds=30.0, pushes=False, role=None,
        check=dict(episodes=100, ko_rate=(0.10, 0.40), max_self_fall=0.05),
    ),
    "L5": dict(  # Full match: 3 rounds, clocks, judges; Unreal replay must match
        model="arena", seconds=None, pushes=False, role=None,
        check=dict(matches=100, max_blowups=0),
    ),
}
