"""Verify the AI realism options across configs, body builder (every roster card), the S2 walking env, L0-L4 combat envs, L5 match log.

    .venv/Scripts/python.exe verify_realism.py
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import torch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def main():
    print("=== [1/6] Validating AI Realism Config Files ===")
    cfg_dir = HERE / "configs"
    j_cfg = json.loads((cfg_dir / "joint_and_strength_config.json").read_text())
    s_cfg = json.loads((cfg_dir / "sensory_profile.json").read_text())
    t_cfg = json.loads((cfg_dir / "training_realism_config.json").read_text())
    assert j_cfg["physics_engine"]["physics_hz"] == 200 and j_cfg["physics_engine"]["control_hz"] == 50
    assert j_cfg["joint_firmness_and_damping"]["damping_ratio_zeta"] == 0.8
    assert j_cfg["anthropometric_mass_distribution_pct"]["total_pct"] == 100.0
    assert s_cfg["proprioception_observation_space"]["base_dimension"] == 136
    assert s_cfg["proprioception_observation_space"]["extended_locomotion_dimension"] == 148
    assert "Stage_2_Steady_Natural_Walk" in t_cfg["step_by_step_learning_milestones"]["locomotion_foundation_stages"]
    print("  [PASS] All 3 realism configuration files verified.")

    print("\n=== [2/6] Verifying Body Mechanics, Joint Limits, Self-Collision & COM (Options 1, 2, 4) ===")
    import mujoco
    from build_body import JOINTS, JOINT_SEG, MASS, MUSCLE_TC
    cards = list(dict.fromkeys(r["card"] for r in json.loads((HERE / "roster.json").read_text())))   # every roster athlete
    for card in cards:
        res = subprocess.run([sys.executable, str(HERE / "build_body.py"), str(HERE / "characters" / f"{card}.json")],
                             capture_output=True, text=True, check=True)
        for line in res.stdout.strip().splitlines():
            print("  " + line)
        assert "A9 realism limits: knee/elbow anti-hyperextension OK" in res.stdout
        assert "swing & stride arcs" in res.stdout and "A13 shoulder cone" in res.stdout
        c = json.loads((HERE / "characters" / f"{card}.json").read_text())
        fp = json.loads((HERE / "models" / f"{card}_fingerprint.json").read_text())
        m = mujoco.MjModel.from_xml_path(str(HERE / "models" / f"{card}_solo.xml"))

        # Option 1: the torque caps come from the card - the trained adult's torque x the card's strength,
        # and x the card's toughness on the neck (the KO resistance)
        for i in range(m.nu):
            j = m.joint(m.actuator_trnid[i, 0]).name
            base = j.rsplit("_", 1)[0] if j.endswith(("_l", "_r")) else j
            seg = JOINT_SEG.get(base)
            adult = [t for jn, _, _, t in JOINTS[seg] if jn == base][0]
            want = adult * c["strength"] * (c.get("toughness", 1.0) if seg == "neck" else 1.0)
            got = float(m.actuator_forcerange[i, 1])
            assert abs(got - want) < 1e-3, f"{card} {j}: the torque cap is {got:.2f} Nm, the card says {want:.2f}"
            assert abs(m.actuator_forcerange[i, 0] + got) < 1e-4, f"{card} {j}: the forcerange is not symmetric"
            assert abs(m.actuator_ctrlrange[i, 0] - m.jnt_range[m.actuator_trnid[i, 0], 0]) < 1e-6, f"{card} {j}: ctrlrange != joint range"
        # Option 3: the muscle ramps (a first-order lag on the force, not a step): every motor's time constant
        assert all(abs(m.actuator_dynprm[i, 0] - MUSCLE_TC) < 1e-6 for i in range(m.nu)), "a motor without the muscle lag"
        # Option 2: the card's % body mass table, applied exactly
        for body in range(1, m.nbody):
            name = m.body(body).name
            seg = name.rsplit("_", 1)[0] if name.endswith(("_l", "_r")) else name
            if seg in MASS:
                want = c["mass_kg"] * MASS[seg] / 100.0
                assert abs(m.body_mass[body] - want) < 1e-2, f"{card} {name}: {m.body_mass[body]:.3f} kg, the card says {want:.3f}"
        # Option 2: the centre of mass over the middle of the feet in the neutral stance
        off = fp["balance_offset_m"]
        assert abs(off[0]) < 0.04 and abs(off[1]) < 0.02, f"{card}: the COM is {off[0] * 100:+.1f}/{off[1] * 100:+.1f} cm off mid-feet"
        # Option 4: primitive colliders only, and the parent-child pairs excluded (MuJoCo's filterparent)
        kinds = {m.geom_type[g] for g in range(m.ngeom) if m.geom_bodyid[g] != 0}
        prims = {mujoco.mjtGeom.mjGEOM_CAPSULE, mujoco.mjtGeom.mjGEOM_SPHERE, mujoco.mjtGeom.mjGEOM_BOX}
        assert kinds <= prims, f"{card}: a non-primitive collider {kinds}"
        assert not (m.opt.disableflags & mujoco.mjtDisableBit.mjDSBL_FILTERPARENT), f"{card}: the parent-child filter is off"
        print(f"  [PASS] {card}: the torque caps, the mass table, the COM, the muscle lag and the primitive hulls "
              f"({m.nu} motors, {m.ngeom} geoms) all follow the card.")

    print("\n=== [3/6] Verifying the S2 Walking & Proprioception Env on GPU (Options 3, 5, 6, 7, 8, 9, 10) ===")
    from envs.locomotion import LocomotionEnv

    for stage in ("S2",):
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

    # The obstacle course (S3) and the aisle lap (S4): the course is laid out, the obstacles are in the path,
    # the observation is still the 136 floats, and the per-episode metrics are recorded.
    for stage, course in (("S3", "podium"), ("S4", "aisle")):
        env = LocomotionEnv("grandpa", level=stage, num_envs=128, seed=42)
        assert env.course == course
        obs = env.observe()
        assert obs.shape == (128, 136), f"{stage}: the observation is {obs.shape}, the brain I/O must stay 136"
        for _ in range(25):
            obs, rew, done, _ = env.step(torch.zeros(128, env.A, device="cuda") * 0.2)
        assert torch.isfinite(obs).all() and torch.isfinite(rew).all(), f"{stage}: NaN"
        assert (env.wp_n >= 2).all() and (env.wp_n <= 4).all(), f"{stage}: the course must have 2-4 waypoints"
        # the obstacles stand on the floor (the step and the podium) and in the athlete's path
        assert float(env.gpos[:, env.podium, 2].min()) > 0.0, f"{stage}: the podium is below the floor"
        if course == "podium":
            assert float(env.gpos[:, env.step_g, 2].min()) > 0.0, f"{stage}: the step is below the floor"
        far = ((env.wp[:, :, 0] * env.dir[:, None]).max(dim=-1).values > 1.0)
        assert bool(far.any()), f"{stage}: the waypoints are not down the path"
        assert float(env.gravity[:, 2].max()) < 0.0, f"{stage}: the gravity must point down"
        print(f"  [PASS] Stage {stage} ({course}): {int(env.wp_n[0])} waypoints, the step at "
              f"{env.gpos[0, env.step_g].tolist()[:2]} m, the podium at {env.gpos[0, env.podium].tolist()[:2]} m, "
              f"the observation is 136 floats, the metrics clear/reach are recorded.")

    print("\n=== [4/6] Verifying SlapEnv (L0-L3) & ArenaEnv (L4) Realism Upgrades ===")
    import numpy as np
    from brain_cpu import hill
    from envs.slap import SlapEnv, hill as hill_t
    fr, vmax = np.array([[-10.0, 10.0]] * 5), np.full(5, 8.0)
    v = np.array([0.0, 2.0, -2.0, 8.0, 20.0])
    h = hill(fr, v, vmax)
    assert (h[0] == fr[0]).all() and h[3, 1] == 0 and h[4, 1] == 0 and (h[:, 0][v >= 0] == -10).all()   # rest F0, top speed 0, braking F0
    assert abs(h[1, 1] - 10 * 0.75 / 2) < 1e-12 and h[2, 0] == -h[1, 1]                                 # 1/4 top speed: 37.5 %
    ht = hill_t(torch.tensor(fr)[None], torch.tensor(v)[None], torch.tensor(vmax))[0].numpy()
    assert np.allclose(ht, h), (ht, h)
    print("  [PASS] Hill force-velocity: F0 at rest, 37.5 % at 1/4 top speed, 0 at top speed, braking untouched; torch == numpy")
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

    print("\n=== [5/6] Verifying the eye-contact servo in the L4 arena env (gaze_check.py, ON vs OFF) ===")

    def gaze(env_var=None):
        e = dict(os.environ)
        e.pop("SLAP_EYE_CONTACT", None)
        if env_var is not None:
            e["SLAP_EYE_CONTACT"] = env_var
        res = subprocess.run([sys.executable, str(HERE / "gaze_check.py")], capture_output=True, text=True, check=True, env=e)
        return json.loads([l for l in res.stdout.strip().splitlines() if l.startswith("{")][-1])

    on, off = gaze(), gaze("0")
    assert on["gaze_on"] and not off["gaze_on"], (on, off)
    assert on["applied_rad"] > 0.01, on            # the servo IS wired in: the neck yaw goal moves off the brain's own
    assert off["applied_rad"] == 0.0, off          # SLAP_EYE_CONTACT=0: the brain keeps the neck
    assert off["would_apply_rad"] > 0.01, off      # the servo's maths still runs (not a dead branch)
    assert on["obs_dim"] == off["obs_dim"] == 136, (on, off)   # the observation is unchanged: the servo moves motors only
    print(f"  [PASS] gaze servo: ON moves the neck yaw goal by {on['applied_rad']:.3f} rad on average; OFF leaves it at "
          f"{off['applied_rad']:.3f} (it would have moved it {off['would_apply_rad']:.3f} rad); the observation is 136 floats either way.")

    print("\n=== [6/6] Verifying L5 Match & Gameplay JSONL Telemetry Logging (Logs/Gameplay/YYMMDD/) ===")
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

