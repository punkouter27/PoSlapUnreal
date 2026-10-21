"""Train one level of the skill ladder from scratch (or resume), on the GPU.

    .venv/Scripts/python.exe train.py --level L0 --minutes 5            (Unreal Editor closed for long runs)

TensorBoard starts with the run (http://localhost:6006). A brain is exported to ONNX only on graduation
(AGENTS.md): mean episode return >= the level's target over 100 consecutive episodes, zero NaN, and the
level's own exam passed by the deterministic policy. runs/<run>/: latest.pt, telemetry.jsonl, meta.json,
report.html (runs over 30 min). Runs that never reached 5,000 steps are pruned at start.
"""
import argparse, json, shutil, socket, subprocess, sys, time
from collections import deque
from pathlib import Path

import torch
import warp as wp
from torch.utils.tensorboard import SummaryWriter

from envs.slap import SlapEnv
from levels import LEVELS
from ppo import PPO, PPOConfig, export_onnx

HERE = Path(__file__).parent
RUNS = HERE / "runs"


def housekeeping():
    """Prune abandoned runs: no meta (crashed) or under 5,000 env steps."""
    for d in RUNS.glob("*"):
        meta = d / "meta.json"
        steps = json.loads(meta.read_text()).get("steps", 0) if meta.exists() else 0
        if d.is_dir() and steps < 5000 and time.time() - d.stat().st_mtime > 600:
            shutil.rmtree(d, ignore_errors=True)
            print(f"pruned {d.name} ({steps} steps)")


def tensorboard():
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", 6006)) == 0:
            return
    exe = Path(sys.executable).with_name("tensorboard.exe")
    subprocess.Popen([str(exe), "--logdir", str(RUNS), "--port", "6006"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    print("TensorBoard: http://localhost:6006")


@torch.no_grad()
def exam(ppo, character, level):
    """The level's pass/fail check on the deterministic policy, 100 fresh randomised worlds."""
    chk = LEVELS[level]["check"]
    env = SlapEnv(character, level, num_envs=chk["episodes"], seed=12345)
    obs = env.observe()
    fell_any = torch.zeros(env.N, dtype=torch.bool, device="cuda")
    left_box = torch.zeros_like(fell_any)
    for _ in range(env.T):
        a = ppo.model.actor(ppo.obs_rms.normalize(obs, ppo.cfg.obs_clip))
        lb = env.left_box.clone()
        obs, _, done, timeout = env.step(a)
        fell_any |= done & ~timeout
        left_box |= lb
    res = {"no_fall": 1.0 - fell_any.float().mean().item()}
    passed = res["no_fall"] >= chk["min_no_fall"]
    if "min_in_box" in chk:
        res["in_box"] = 1.0 - left_box.float().mean().item()
        passed &= res["in_box"] >= chk["min_in_box"]
    torch.cuda.set_stream(ppo_stream)                 # the exam env switched both streams to its own
    wp.set_stream(wp.stream_from_torch(ppo_stream))
    return passed, res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--char", default="grandpa")
    ap.add_argument("--level", default="L0")
    ap.add_argument("--minutes", type=float, default=5.0)
    ap.add_argument("--envs", type=int, default=4096)
    ap.add_argument("--run", default=None)
    ap.add_argument("--resume", default=None)
    a = ap.parse_args()
    RUNS.mkdir(exist_ok=True)
    housekeeping()
    tensorboard()
    run = RUNS / (a.run or f"{a.char}_{a.level}_{time.strftime('%y%m%d_%H%M')}")
    run.mkdir(exist_ok=True)
    tb, tele = SummaryWriter(str(run)), open(run / "telemetry.jsonl", "a")

    env = SlapEnv(a.char, a.level, num_envs=a.envs)
    global ppo_stream
    ppo_stream = env.stream
    ppo = PPO(108, env.A, a.envs, "cuda", PPOConfig(init_std=0.25))
    if a.resume:
        ppo.load(a.resume)
    target = LEVELS[a.level]["reward_target"]
    recent = deque(maxlen=100)
    obs, t0, steps, it, last_exam, graduated, nan = env.observe(), time.time(), 0, 0, -1e9, None, False
    print(f"run {run.name}: {a.level}, {a.envs} worlds, {a.minutes} min, graduate at return {target} over 100 episodes")

    while time.time() - t0 < a.minutes * 60:
        for _ in range(ppo.cfg.steps_per_env):
            act = ppo.act(obs)
            obs, rew, done, timeout = env.step(act)
            ppo.record(rew, done, timeout)
        steps += ppo.cfg.steps_per_env * a.envs
        stats = ppo.update(obs)
        it += 1
        recent.extend(env.episodes)
        eps, env.episodes = env.episodes, []
        nan = nan or any(v != v for v in stats.values()) or not all(torch.isfinite(p).all() for p in ppo.model.parameters())
        if nan:
            print("NaN in training: stopping, nothing exported")
            break
        ret = sum(e[0] for e in recent) / max(1, len(recent))
        fall = sum(e[1] for e in recent) / max(1, len(recent))
        eplen = sum(e[3] for e in recent) / max(1, len(recent)) / 50
        for k, v in {**stats, "episode_return": ret, "fall_rate": fall, "episode_s": eplen}.items():
            tb.add_scalar(k, v, steps)
        tele.write(json.dumps({"it": it, "steps": steps, "min": round((time.time() - t0) / 60, 2), "episode_return": ret,
                               "fall_rate": fall, "episode_s": eplen, "episodes": len(eps), **stats,
                               "pelvis_z_w0": env.qpos[0, 2].item()}) + "\n")
        if it % 10 == 0:
            print(f"it {it:5d} {steps / 1e6:7.1f}M steps {(time.time() - t0) / 60:5.1f} min | return {ret:7.1f} "
                  f"fall {fall:.2f} ep {eplen:4.1f}s | std {stats['action_std']:.3f} lr {stats['lr']:.1e}")
        if it % 50 == 0:
            ppo.save(str(run / "latest.pt"), {"steps": steps, "level": a.level})
        if len(recent) == 100 and ret >= target and time.time() - last_exam > 120:
            last_exam = time.time()
            ok, res = exam(ppo, a.char, a.level)
            print(f"exam {a.level}: {res} -> {'PASS' if ok else 'not yet'}")
            tb.add_scalar("exam_no_fall", res["no_fall"], steps)
            if ok:
                graduated = res
                break

    ppo.save(str(run / "latest.pt"), {"steps": steps, "level": a.level})
    minutes = (time.time() - t0) / 60
    meta = {"char": a.char, "level": a.level, "steps": steps, "iterations": it, "minutes": round(minutes, 1),
            "episode_return": ret if it else 0, "fall_rate": fall if it else 1, "graduated": graduated, "nan": nan}
    if graduated and not nan:
        out = HERE / "models" / "brains" / f"{a.char}_{a.level}.onnx"
        export_onnx(ppo, str(out), 108, fixed_batch=True)
        meta["onnx"] = str(out.relative_to(HERE))
        print(f"GRADUATED -> {out}")
    (run / "meta.json").write_text(json.dumps(meta, indent=1))
    tele.close(); tb.close()
    if minutes > 30:
        from report import write_report
        print("report:", write_report(run))
    print(json.dumps(meta))


if __name__ == "__main__":
    main()
