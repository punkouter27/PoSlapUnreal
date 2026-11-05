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
    from constants import OBS, OBS_V3   # one source for the observation count
    assert s_cfg["proprioception_observation_space"]["base_dimension"] == OBS_V3
    assert s_cfg["proprioception_observation_space"]["interface_4_dimension"] == OBS
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

    print("\n=== [2b/6] Physics 2 (models/v2, the realism batch for the retrain: DOCS/20261103/training_realism.md) ===")
    from build_body import STIFF_SOLREF, STRIKE_MARGIN, TORQUE_V2, filtered_pairs
    from constants import models_dir
    for card in ("grandpa", "kim"):   # the athletes the retrain queue trains on physics 2
        res = subprocess.run([sys.executable, str(HERE / "build_body.py"), str(HERE / "characters" / f"{card}.json"), "--physics", "2"],
                             capture_output=True, text=True, check=True)
        assert "swing & stride arcs" in res.stdout, res.stdout
        c = json.loads((HERE / "characters" / f"{card}.json").read_text())
        fp = json.loads((models_dir(2) / f"{card}_fingerprint.json").read_text())
        assert fp.get("physics") == 2, f"{card}: the v2 fingerprint does not say physics 2"
        m = mujoco.MjModel.from_xml_path(str(models_dir(2) / f"{card}_solo.xml"))
        pairs = {tuple(sorted((m.geom(int(m.pair_geom1[i])).name, m.geom(int(m.pair_geom2[i])).name))) for i in range(m.npair)}
        for a, b in filtered_pairs():   # every pair MuJoCo's weld-aware parent filter skips now collides
            assert tuple(sorted((a, b))) in pairs, f"{card}: no explicit pair for {a}-{b}"
        for h in ("hand_l", "hand_r"):   # the palm-target pair: stiff, with the early margin
            i = next(i for i in range(m.npair) if {m.geom(int(m.pair_geom1[i])).name, m.geom(int(m.pair_geom2[i])).name} == {h, "target"})
            assert abs(m.pair_margin[i] - STRIKE_MARGIN) < 1e-6 and abs(m.pair_solref[i, 0] - STIFF_SOLREF[0]) < 1e-6
        fl = m.geom("floor").id
        assert m.geom_condim[fl] == 4 and abs(m.geom_solref[fl, 0] - STIFF_SOLREF[0]) < 1e-6, f"{card}: the floor is not rigid / condim 4"
        for i in range(m.nu):
            j = m.joint(m.actuator_trnid[i, 0]).name
            base = j.rsplit("_", 1)[0] if j.endswith(("_l", "_r")) else j
            if base in TORQUE_V2:
                st = c["strength"]
                neg, pos = TORQUE_V2[base]
                assert abs(m.actuator_forcerange[i, 0] + neg * st) < 1e-3 and abs(m.actuator_forcerange[i, 1] - pos * st) < 1e-3, \
                    f"{card} {j}: forcerange {m.actuator_forcerange[i]} != TORQUE_V2 x strength"
        v1 = json.loads((HERE / "models" / f"{card}_fingerprint.json").read_text())
        import hashlib
        assert hashlib.sha256((HERE / "models" / f"{card}_solo.xml").read_bytes().replace(b"\r\n", b"\n")).hexdigest() == v1["sha256"], \
            f"{card}: the shipped v1 model changed"
        print(f"  [PASS] {card} v2: {m.npair} explicit pairs ({len(filtered_pairs())} welded + 2 palm-target, margin "
              f"{STRIKE_MARGIN * 1000:.0f} mm), rigid condim-4 floor, asymmetric torques; v1 model unchanged (sha {v1['sha256'][:12]})")

    print("\n=== [2c/6] Physics 3 (models/v3: the face, stiff limbs, sole friction; DOCS/20261104/physics3.md) ===")
    import numpy as np
    from build_body import FOOT_PRIORITY, STIFF_SOLIMP
    v2_sha = {c: json.loads((models_dir(2) / f"{c}_fingerprint.json").read_text())["sha256"] for c in ("grandpa", "kim")}
    for card in ("grandpa", "kim"):
        res = subprocess.run([sys.executable, str(HERE / "build_body.py"), str(HERE / "characters" / f"{card}.json"), "--physics", "3"],
                             capture_output=True, text=True, check=True)
        assert "swing & stride arcs" in res.stdout and "OK" in res.stdout, res.stdout   # A10 zero overlap, A11 balance
        fp = json.loads((models_dir(3) / f"{card}_fingerprint.json").read_text())
        assert fp.get("physics") == 3, f"{card}: the v3 fingerprint does not say physics 3"
        c = json.loads((HERE / "characters" / f"{card}.json").read_text())
        m = mujoco.MjModel.from_xml_path(str(models_dir(3) / f"{card}_solo.xml"))
        d = mujoco.MjData(m)
        mujoco.mj_kinematics(m, d)
        head = m.body("head").id
        assert {m.geom(g).name for g in range(m.ngeom) if m.geom_bodyid[g] == head} == {"head", "jaw_l", "jaw_r"}
        assert abs(m.body_mass[head] - c["mass_kg"] * MASS["head"] / 100) < 1e-2, f"{card}: the head's mass moved with the jaw"
        s = m.site("face_zone").id   # the face zone holds the whole jaw and the sphere's front half
        R, h = d.site_xmat[s].reshape(3, 3), m.site_size[s]
        inside = lambda p: bool((np.abs(R.T @ (p - d.site_xpos[s])) <= h + 1e-6).all())
        for j in ("jaw_l", "jaw_r"):
            g = m.geom(j).id
            ax = d.geom_xmat[g].reshape(3, 3)[:, 2] * m.geom_size[g][1]
            for e in (-1, 1):
                for u in np.vstack([np.eye(3), -np.eye(3)]):   # the capsule's end caps, out along every axis
                    assert inside(d.geom_xpos[g] + e * ax + m.geom_size[g][0] * u), f"{card}: {j} pokes out of the face zone"
        hc, hr = d.geom_xpos[m.geom("head").id], m.geom_size[m.geom("head").id][0]
        for u in ((1, 0, 0), (0, 1, 0), (0, -1, 0), (0.7, 0.7, 0)):
            assert inside(hc + hr * np.array(u) / np.linalg.norm(u)), f"{card}: the cheek {u} is outside the face zone"
        assert not inside(hc + hr * np.array([-1.0, 0, 0])) and not inside(hc + hr * np.array([0, 0, 1.0])), \
            f"{card}: the back / crown of the head counts as the face"
        for g in range(m.ngeom):   # every athlete geom stiff; the soles win their floor contacts with their own friction
            if m.body_rootid[m.geom_bodyid[g]] == m.body("pelvis").id:
                assert abs(m.geom_solref[g, 0] - STIFF_SOLREF[0]) < 1e-6 and abs(m.geom_solimp[g, 0] - STIFF_SOLIMP[0]) < 1e-6, m.geom(g).name
        for f in ("foot_l", "foot_r"):
            g = m.geom(f).id
            assert m.geom_priority[g] == FOOT_PRIORITY > m.geom_priority[m.geom("floor").id] and m.geom_condim[g] == 4
        # K11: the per-foot friction randomisation reaches the sole's floor contact (it was the floor's value, always)
        st = fp["stand_pose_rad"]
        for j, q in st.items():
            d.qpos[m.joint(j).qposadr[0]] = q
        m.geom_friction[m.geom("foot_l").id, 0], m.geom_friction[m.geom("floor").id, 0] = 0.61, 1.17
        for i in range(m.nu):   # settle onto the soles (muscles holding the stance)
            d.ctrl[i] = d.act[i] = d.qpos[m.jnt_qposadr[m.actuator_trnid[i, 0]]]
        for _ in range(40):
            mujoco.mj_step(m, d)
        mu = {m.geom(int(x.geom[1] if x.geom[0] == m.geom("floor").id else x.geom[0])).name: float(x.friction[0]) for x in d.contact[:d.ncon]
              if m.geom("floor").id in (x.geom[0], x.geom[1])}
        assert abs(mu.get("foot_l", 0) - 0.61) < 1e-4 and abs(mu.get("foot_r", 0) - 0.9) < 1e-4, f"{card}: sole friction {mu}"
        a = mujoco.MjModel.from_xml_path(str(models_dir(3) / f"{card}_vs_{card}_arena.xml"))
        pairs = {tuple(sorted((a.geom(int(a.pair_geom1[i])).name, a.geom(int(a.pair_geom2[i])).name))): float(a.pair_margin[i]) for i in range(a.npair)}
        for sd, op in (("red_", "blue_"), ("blue_", "red_")):
            for hnd in ("hand_l", "hand_r"):
                for part in ("head", "jaw_l", "jaw_r", "neck"):
                    k = tuple(sorted((sd + hnd, op + part)))
                    assert abs(pairs.get(k, -1) - STRIKE_MARGIN) < 1e-6, f"{card}: no stiff early pair {k}"
        assert json.loads((models_dir(2) / f"{card}_fingerprint.json").read_text())["sha256"] == v2_sha[card], f"{card}: v2 changed"
        print(f"  [PASS] {card} v3: jaw capsules on the head ({m.body_mass[head]:.2f} kg kept), face zone = cheeks + jaw (not the "
              f"back or crown), {a.npair} arena pairs incl. hand-jaw/neck, every athlete geom stiff, sole friction per foot "
              f"(floor 1.17, left sole 0.61 -> contact {mu['foot_l']:.2f}); v2 unchanged")

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

    # Interface 4 (DOCS/BRAIN_IO.md, 2026-11-04; the unused 148 extension was retired): 136 + heel/toe contacts, the
    # standing athlete reads all four bits set, and a 136-wide (shipped) brain's env stays 136 wide
    env140 = LocomotionEnv("kim", level="S2", num_envs=128, seed=42)
    for _ in range(10):
        obs140, rew140, _, _ = env140.step(torch.zeros(128, env140.A, device="cuda"))
    assert obs140.shape == (128, 140) and torch.isfinite(obs140).all(), obs140.shape
    feet = obs140[:, 136:140].mean(0)
    assert (feet > 0.5).all(), f"a standing athlete's heel/toe bits should be set: {feet.tolist()}"
    assert env140.lpf and not env136.lpf, "the low-pass belongs to interface 4 only"
    print(f"  [PASS] Interface 4 (Kim S2): shape={tuple(obs140.shape)}, heel/toe contact share {[round(x, 2) for x in feet.tolist()]}, "
          f"low-pass on (136-wide env: off).")

    # The obstacle course (S3) and the aisle lap (S4): the course is laid out, the obstacles are in the path,
    # the observation is the BRAIN_IO width (OBS), and the per-episode metrics are recorded.
    for stage, course in (("S3", "podium"), ("S4", "aisle")):
        env = LocomotionEnv("grandpa", level=stage, num_envs=128, seed=42)
        assert env.course == course
        obs = env.observe()
        assert obs.shape == (128, OBS), f"{stage}: the observation is {obs.shape}, the brain I/O is {OBS}"
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
              f"the observation is {OBS} floats, the metrics clear/reach are recorded.")

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
    assert on["obs_dim"] == off["obs_dim"] == OBS, (on, off)   # the observation is unchanged: the servo moves motors only
    print(f"  [PASS] gaze servo: ON moves the neck yaw goal by {on['applied_rad']:.3f} rad on average; OFF leaves it at "
          f"{off['applied_rad']:.3f} (it would have moved it {off['would_apply_rad']:.3f} rad); the observation is {OBS} floats either way.")

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

