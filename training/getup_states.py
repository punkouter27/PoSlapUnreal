"""Real knockdown states for the get-up env (G1, envs/getup.py; item 44, 2026-11-04).

    .venv/Scripts/python.exe getup_states.py --char grandpa --physics 3 [--brain <.pt>] [--count 4000]

A trained brain plays the slap env (envs/slap.py, the solo model the get-up env runs) on the GPU, half the worlds
absorbing scripted slaps of 1.5-3.5 x the median impulse, half guarding under shoves of 1.5-4 x the 150-300 N range.
Every time a world goes down (the knockdown rule: a non-sole part on the floor or the podium) its qpos and qvel at that
step are kept, before the env resets it. That is the state the director hands to the get-up brain, so it is what the
get-up brain trains on. Written to models/v<physics>/getup/<char>_states.npz (qpos, qvel, role, source).
The brain reads its own width (a shipped 136 brain: no foot contacts, no low-pass).
"""
import argparse, json, os, time
from pathlib import Path

import numpy as np
import torch

HERE = Path(__file__).resolve().parent


def default_brain(char, physics):
    runs = sorted((HERE / "runs").glob(f"{char}_L3_v{physics}_*/best.pt"), key=lambda p: p.stat().st_mtime)
    for p in runs[::-1] + [HERE / "models" / "brains" / f"{char}_final.pt"]:
        if p.exists():
            return p
    raise FileNotFoundError(f"no brain for {char}: pass --brain")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--char", default="grandpa")
    ap.add_argument("--physics", type=int, default=3)
    ap.add_argument("--brain", default=None)
    ap.add_argument("--count", type=int, default=4000)
    ap.add_argument("--envs", type=int, default=2048)
    ap.add_argument("--minutes", type=float, default=6.0)
    a = ap.parse_args()
    os.environ["SLAP_PHYSICS"] = str(a.physics)
    from constants import lpf_on, models_dir
    from envs.slap import SlapEnv
    from ppo import PPO, PPOConfig, obs_dim_of
    from envs.getup import states_path

    brain = Path(a.brain) if a.brain else default_brain(a.char, a.physics)
    n_in = obs_dim_of(str(brain))
    env = SlapEnv(a.char, "L3", num_envs=a.envs, seed=7)
    env.lpf = lpf_on(n_in)
    ppo = PPO(n_in, env.A, 1, "cuda", PPOConfig())
    ppo.load(str(brain))
    env.roles = {-1: 0.5, 0: 0.5}
    env.cfg = {**env.cfg, "pushes": True}
    env.push_scale = 2.5            # 375-750 N shoves on chest or head
    env.slap_j = 15.0 * 2.5         # 22-52 N s slaps (training draws 0.6-1.4 x)
    env.reset(torch.ones(env.N, dtype=torch.bool, device="cuda"))
    qs, vs, roles = [], [], []
    orig = env.reset

    def capture(mask):   # the env resets fallen worlds inside step(): take their state first
        down = mask & env.fell_now & ~torch.isnan(env.qpos).any(-1)
        if bool(down.any()):
            qs.append(env.qpos[down].cpu().numpy()); vs.append(env.qvel[down].cpu().numpy()); roles.append(env.role[down].cpu().numpy())
        orig(mask)
    env.reset = capture
    obs, t0, have = env.observe(), time.time(), 0
    with torch.no_grad():
        while have < a.count and time.time() - t0 < a.minutes * 60:
            act = ppo.model.actor(ppo.obs_rms.normalize(obs[:, :n_in], ppo.cfg.obs_clip))
            obs, _, _, _ = env.step(act)
            have = sum(len(q) for q in qs)
    q, v, r = np.concatenate(qs)[:a.count], np.concatenate(vs)[:a.count], np.concatenate(roles)[:a.count]
    ok = (np.abs(v).max(-1) < 50) & np.isfinite(q).all(-1)   # drop a blow-up mid-step
    q, v, r = q[ok], v[ok], r[ok]
    out = states_path(a.char, a.physics)
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out, qpos=q.astype(np.float32), qvel=v.astype(np.float32), role=r.astype(np.int8),
                        source=np.array(json.dumps({"brain": str(brain.relative_to(HERE) if brain.is_relative_to(HERE) else brain),
                                                    "n_in": n_in, "physics": a.physics, "push_scale": 2.5, "slap_j": 37.5,
                                                    "made": time.strftime("%Y-%m-%d %H:%M")})))
    print(f"{out.relative_to(HERE)}: {len(q)} knockdown states ({int((r == -1).sum())} from slaps, {int((r == 0).sum())} from "
          f"shoves) from {brain.name} ({n_in} inputs) in {time.time() - t0:.0f} s; pelvis z median {np.median(q[:, 2]):.2f} m")


if __name__ == "__main__":
    main()
