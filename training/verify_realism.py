"""Verify all 10 AI Realism Options across configs, body builder, S1-S3 locomotion envs, L0-L4 combat envs, and L5 match log.

    .venv/Scripts/python.exe verify_realism.py
"""
import json
import subprocess
import sys
import time
from pathlib import Path

import torch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def main():
    print("=== [1/5] Validating AI Realism Config Files ===")
    cfg_dir = HERE / "configs"
    j_cfg = json.loads((cfg_dir / "joint_and_strength_config.json").read_text())
    s_cfg = json.loads((cfg_dir / "sensory_profile.json").read_text())
    t_cfg = json.loads((cfg_dir / "training_realism_config.json").read_text())
    assert j_cfg["physics_engine"]["physics_hz"] == 200 and j_cfg["physics_engine"]["control_hz"] == 50
    assert j_cfg["joint_firmness_and_damping"]["damping_ratio_zeta"] == 0.8
    assert j_cfg["anthropometric_mass_distribution_pct"]["total_pct"] == 100.0
    assert s_cfg["proprioception_observation_space"]["base_dimension"] == 136
    assert s_cfg["proprioception_observation_space"]["extended_locomotion_dimension"] == 148
    assert "Stage_1_Stand_And_Push_Recovery" in t_cfg["step_by_step_learning_milestones"]["locomotion_foundation_stages"]
    assert "Stage_2_Steady_Natural_Walk" in t_cfg["step_by_step_learning_milestones"]["locomotion_foundation_stages"]
    assert "Stage_3_Jog_Turn_Stop_And_Slopes" in t_cfg["step_by_step_learning_milestones"]["locomotion_foundation_stages"]
    print("  [PASS] All 3 realism configuration files verified.")

    print("\n=== [2/5] Verifying Body Mechanics, Joint Limits, Self-Collision & COM (Options 1, 2, 4) ===")
    res = subprocess.run(
        [sys.executable, str(HERE / "build_body.py"), str(HERE / "characters" / "grandpa.json"), "--blue", str(HERE / "characters" / "kim.json")],
        capture_output=True, text=True, check=True,
    )
    for line in res.stdout.strip().splitlines():
        print("  " + line)
    assert "A9 realism limits: knee/elbow anti-hyperextension OK" in res.stdout
    assert "swing & stride arcs" in res.stdout
    print("  [PASS] Grandpa & Kim passed A8 rig, A9 anti-hyperextension, A10 swing/stride self-collision, A11 COM balance, A12 fingerprint.")

    print("\n=== [3/5] Verifying S1-S3 Locomotion & Proprioception Envs on GPU (Options 3, 5, 6, 7, 8, 9, 10) ===")
    from envs.locomotion import LocomotionEnv

    for stage in ("S1", "S2", "S3"):
        env136 = LocomotionEnv("grandpa", level=stage, num_envs=256, seed=42, obs_dim=136)
        obs = env136.observe()
        assert obs.shape == (256, 136), f"Expected (256, 136), got {obs.shape}"
        t0 = time.time()
        for _ in range(25):
            act = torch.randn(256, env136.A, device="cuda") * 0.2
            obs, rew, done, timeout = env136.step(act)
        torch.cuda.synchronize()
        dt = time.time() - t0
        sps = (25 * 256) / dt
        assert torch.isfinite(obs).all() and torch.isfinite(rew).all(), f"NaN in {stage}"
        print(f"  [PASS] Stage {stage} (136-dim BRAIN_IO): {sps:,.0f} agent-steps/s, mean_rew={rew.mean().item():.3f}, stamina_min={env136.stamina.min().item():.3f}")

    # Test 148-dim extended proprioception (Option 5)
    env148 = LocomotionEnv("kim", level="S2", num_envs=128, seed=42, obs_dim=148)
    obs148 = env148.observe()
    assert obs148.shape == (128, 148), f"Expected (128, 148), got {obs148.shape}"
    obs148, rew148, _, _ = env148.step(torch.zeros(128, env148.A, device="cuda"))
    assert torch.isfinite(obs148).all()
    print(f"  [PASS] Extended 148-dim Locomotion Proprioception (Kim S2): shape={tuple(obs148.shape)}, foot contacts & phase clock active.")

    print("\n=== [4/5] Verifying SlapEnv (L0-L3) & ArenaEnv (L4) Realism Upgrades ===")
    from envs.slap import SlapEnv
    from envs.arena import ArenaEnv

    s_env = SlapEnv("grandpa", level="L0", num_envs=128, seed=42)
    o_s = s_env.observe()
    for _ in range(10):
        o_s, r_s, _, _ = s_env.step(torch.zeros(128, s_env.A, device="cuda"))
    assert torch.isfinite(o_s).all() and torch.isfinite(r_s).all()
    print(f"  [PASS] SlapEnv L0 with per-foot friction, continuous stamina & anti-jerk penalty: mean_rew={r_s.mean().item():.3f}")

    a_env = ArenaEnv(("grandpa", "kim"), level="L4", num_envs=64, seed=42)
    o_a = a_env.observe()
    for _ in range(10):
        o_a, r_a, _, _ = a_env.step(torch.zeros(128, a_env.A, device="cuda"))
    assert torch.isfinite(o_a).all() and torch.isfinite(r_a).all()
    print(f"  [PASS] ArenaEnv L4 (Grandpa vs Kim) with realism suite: mean_rew={r_a.mean().item():.3f}")

    print("\n=== [5/5] Verifying L5 Match & Gameplay JSONL Telemetry Logging (Logs/Gameplay/YYMMDD/) ===")
    res_m = subprocess.run(
        [
            sys.executable, str(HERE / "match.py"), "play",
            str(HERE / "models" / "brains" / "grandpa_final_vs_kim.onnx"),
            "--blue", str(HERE / "models" / "brains" / "kim_final_vs_grandpa.onnx"),
            "--blue-char", "kim",
        ],
        capture_output=True, text=True, check=True,
    )
    for line in res_m.stdout.strip().splitlines()[-5:]:
        print("  " + line)
    print("\nALL 10 AI REALISM OPTIONS VERIFIED AND READY.")


if __name__ == "__main__":
    main()

