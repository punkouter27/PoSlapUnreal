"""Train one level of the skill ladder from scratch (or resume), on the GPU.

    .venv/Scripts/python.exe train.py --level L0 --minutes 5            (Unreal Editor closed for long runs)

TensorBoard starts with the run (http://localhost:6006). A brain is exported to ONNX only on graduation
(AGENTS.md): mean episode return >= the level's target over 100 consecutive episodes, zero NaN, and the
level's own exam passed by the deterministic policy. runs/<run>/: latest.pt, telemetry.jsonl, meta.json,
report.html (runs over 30 min). Runs that never reached 5,000 steps are pruned at start.
"""
import argparse, copy, json, shutil, socket, subprocess, sys, time
from collections import deque
from pathlib import Path

import torch
import warp as wp
from torch.utils.tensorboard import SummaryWriter

from brain_cpu import video
from envs.slap import SlapEnv
from levels import LEVELS
from ppo import PPO, PPOConfig, export_onnx

HERE = Path(__file__).parent
RUNS = HERE / "runs"


def housekeeping(keep=()):
    """Prune abandoned runs (AGENTS.md): under 5,000 env steps by their own record, idle 30 min, not in `keep`.
    2026-10-22: the first version counted a run without meta.json (any crashed or killed run) as 0 steps and judged
    idleness by the folder's mtime (which file rewrites don't touch): it deleted long runs, one of them mid-resume."""
    keep = {Path(k).resolve() for k in keep}
    for d in RUNS.glob("*"):
        if not d.is_dir() or d.resolve() in keep:
            continue
        files = [f for f in d.rglob("*") if f.is_file()]
        if files and time.time() - max(f.stat().st_mtime for f in files) < 1800:
            continue
        meta, tele = d / "meta.json", d / "telemetry.jsonl"
        if meta.exists():
            steps = json.loads(meta.read_text()).get("steps", 0)
        elif tele.exists() and tele.stat().st_size:
            steps = json.loads(tele.read_text().strip().splitlines()[-1]).get("steps", 0)
        else:
            steps = 0
        if steps < 5000:
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


def cpu_policy(ppo):
    """The current deterministic policy as numpy-in, numpy-out (what the exported ONNX computes)."""
    actor = copy.deepcopy(ppo.model.actor).cpu().eval()
    mean, std = ppo.obs_rms.mean.cpu(), torch.sqrt(ppo.obs_rms.var + 1e-8).cpu()
    clip = ppo.cfg.obs_clip

    @torch.no_grad()
    def act(o):
        x = torch.clamp((torch.from_numpy(o) - mean) / std, -clip, clip)
        return actor(x[None])[0].numpy()
    return act


_exam_envs = {}


@torch.no_grad()
def exam(ppo, character, level):
    """The level's pass/fail check on the deterministic policy, 100 fresh randomised worlds."""
    chk = LEVELS[level]["check"]
    env = _exam_envs.get((character, level))
    if env is None:   # built once: a fresh env (and CUDA stream) per exam broke Warp's capture after ~30 exams
        env = _exam_envs[(character, level)] = SlapEnv(character, level, num_envs=chk["episodes"], seed=12345)
    else:
        torch.cuda.set_stream(env.stream)
        wp.set_stream(wp.stream_from_torch(env.stream))
        env.reset(torch.ones(env.N, dtype=torch.bool, device="cuda"))
    env.episodes.clear()
    env.push_scale = LEVELS[level].get("max_push", 1.0)
    env.hit_v = float(LEVELS[level].get("hit_speed", 8.0))   # exams use the level's own bar
    obs = env.observe()
    fell_any = torch.zeros(env.N, dtype=torch.bool, device="cuda")   # each world's first episode only
    left_box, hit, seen = torch.zeros_like(fell_any), torch.zeros_like(fell_any), torch.zeros_like(fell_any)
    for _ in range(env.T):
        a = ppo.model.actor(ppo.obs_rms.normalize(obs, ppo.cfg.obs_clip))
        lb = env.left_box.clone()
        obs, _, done, timeout = env.step(a)
        live = ~seen
        fell_any |= live & env.fell_now
        left_box |= live & lb
        hit |= live & done & env.done_hit
        seen |= done
    res = {"no_fall": 1.0 - fell_any.float().mean().item()}
    passed = res["no_fall"] >= chk["min_no_fall"]
    if "min_in_box" in chk:
        res["in_box"] = 1.0 - left_box.float().mean().item()
        passed &= res["in_box"] >= chk["min_in_box"]
    if "min_hit" in chk:   # L2: clean palm strike >= 8 m/s and still up 1.5 s later
        res["hit"] = hit.float().mean().item()
        passed &= res["hit"] >= chk["min_hit"]
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
    ap.add_argument("--max-std", type=float, default=0.3, help="cap on exploration noise (it ran away to 0.47 in grandpa_L0_v1)")
    ap.add_argument("--entropy", type=float, default=0.001)
    ap.add_argument("--keep-going", action="store_true", help="after graduating (ONNX exported), keep training; best.pt keeps the best exam")
    a = ap.parse_args()
    RUNS.mkdir(exist_ok=True)
    run_dir = RUNS / (a.run or "")
    housekeeping(keep=[run_dir] + ([Path(a.resume).parent] if a.resume else []))
    tensorboard()
    run = RUNS / (a.run or f"{a.char}_{a.level}_{time.strftime('%y%m%d_%H%M')}")
    run.mkdir(exist_ok=True)
    tb, tele = SummaryWriter(str(run)), open(run / "telemetry.jsonl", "a")

    env = SlapEnv(a.char, a.level, num_envs=a.envs)
    global ppo_stream
    ppo_stream = env.stream
    ppo = PPO(108, env.A, a.envs, "cuda", PPOConfig(init_std=0.25, lr_min=1e-4, max_std=a.max_std, entropy_coef=a.entropy))
    extra = ppo.load(a.resume) if a.resume else {}
    unseen = ppo.obs_rms.var < 1e-6                     # inputs a resumed brain only ever saw as 0 (a new one in BRAIN_IO)
    ppo.obs_rms.var[unseen], ppo.obs_rms.mean[unseen] = 1.0, 0.0
    with torch.no_grad():
        ppo.model.log_std.clamp_(max=float(torch.log(torch.tensor(a.max_std))))
    # push curriculum: no pushes until he can stand, then up to full 150-300 N; graduation only at full
    env.push_scale = min(extra.get("push_scale", 0.0), LEVELS[a.level].get("max_push", 1.0)) if LEVELS[a.level]["pushes"] else 1.0
    L = LEVELS[a.level]
    env.hit_v = float(extra.get("hit_v", L.get("hit_speed_start", L.get("hit_speed", 8.0))))   # L2 speed curriculum
    target = LEVELS[a.level]["reward_target"]
    recent = deque(maxlen=100)
    obs, t0, steps, it, last_exam, graduated, nan = env.observe(), time.time(), 0, 0, -1e9, None, False
    last_video, best_exam = time.time(), -1.0
    (run / "videos").mkdir(exist_ok=True)
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
        hit_rate = sum(e[4] for e in recent) / max(1, len(recent))                       # L2: clean palm hits
        hits = [e[5] for e in recent if e[5] > 0]
        hit_speed = sum(hits) / len(hits) if hits else 0.0                               # m/s at contact
        if LEVELS[a.level]["pushes"] and len(recent) == 100:
            if eplen > 0.8 * env.T / 50:
                env.push_scale = min(LEVELS[a.level].get("max_push", 1.0), env.push_scale + 0.02)
            elif eplen < 0.4 * env.T / 50:
                env.push_scale = max(0.0, env.push_scale - 0.01)
            env.push_scale = round(env.push_scale, 2)   # 0.01/0.02 steps drifted to 0.5999999999999996 < 0.6: no exam ever
        if "hit_speed" in L and len(recent) == 100 and hit_rate > 0.5:   # raise the clean-hit bar 0.25 m/s at a time
            env.hit_v = min(float(L["hit_speed"]), round(env.hit_v + 0.25, 2))
        for k, v in {**stats, "episode_return": ret, "fall_rate": fall, "episode_s": eplen, "push_scale": env.push_scale,
                     "hit_rate": hit_rate, "hit_speed": hit_speed, "hit_bar": env.hit_v}.items():
            tb.add_scalar(k, v, steps)
        tele.write(json.dumps({"it": it, "steps": steps, "min": round((time.time() - t0) / 60, 2), "episode_return": ret,
                               "fall_rate": fall, "episode_s": eplen, "episodes": len(eps), "push_scale": env.push_scale, "hit_rate": hit_rate, "hit_speed": hit_speed, "hit_bar": env.hit_v, **stats,
                               "pelvis_z_w0": env.qpos[0, 2].item()}) + "\n")
        if it % 10 == 0:
            print(f"it {it:5d} {steps / 1e6:7.1f}M steps {(time.time() - t0) / 60:5.1f} min | return {ret:7.1f} "
                  f"fall {fall:.2f} ep {eplen:4.1f}s | push {env.push_scale:.2f} | hit {hit_rate:.2f} @ {hit_speed:4.1f} m/s (bar {env.hit_v:.2f}) | std {stats['action_std']:.3f} lr {stats['lr']:.1e}", flush=True)
        if it % 50 == 0:
            ppo.save(str(run / "latest.pt"), {"steps": steps, "level": a.level, "push_scale": env.push_scale, "hit_v": env.hit_v})
        if time.time() - last_video > 600:   # checkpoint video every 10 min (CPU MuJoCo, pushes off)
            last_video = time.time()
            gif = run / "videos" / f"it{it:05d}_{(time.time() - t0) / 60:04.0f}min.gif"
            z = video(cpu_policy(ppo), gif, a.char)
            print(f"video {gif.name}: pelvis at {z:.2f} m after 6 s", flush=True)
        if len(recent) == 100 and ret >= target and env.push_scale >= LEVELS[a.level].get("max_push", 1.0) \
                and env.hit_v >= float(L.get("hit_speed", 0.0)) and time.time() - last_exam > 120:
            last_exam = time.time()
            ok, res = exam(ppo, a.char, a.level)
            print(f"exam {a.level}: {res} -> {'PASS' if ok else 'not yet'}", flush=True)
            tb.add_scalar("exam_no_fall", res["no_fall"], steps)
            score = min(res.values())
            if score > best_exam:   # the best exam so far, kept for a deadline that ends before graduation
                best_exam = score
                ppo.save(str(run / "best.pt"), {"steps": steps, "level": a.level, "push_scale": env.push_scale, "hit_v": env.hit_v, "exam": res})
            if ok and not graduated:
                graduated = res
                out = HERE / "models" / "brains" / f"{a.char}_{a.level}.onnx"
                export_onnx(ppo, str(out), 108, fixed_batch=True)
                print(f"GRADUATED -> {out}", flush=True)
                if not a.keep_going:
                    break

    ppo.save(str(run / "latest.pt"), {"steps": steps, "level": a.level, "push_scale": env.push_scale, "hit_v": env.hit_v})
    minutes = (time.time() - t0) / 60
    meta = {"char": a.char, "level": a.level, "steps": steps, "iterations": it, "minutes": round(minutes, 1),
            "episode_return": ret if it else 0, "fall_rate": fall if it else 1, "graduated": graduated, "nan": nan}
    if graduated and not nan:
        meta["onnx"] = f"models/brains/{a.char}_{a.level}.onnx"
    meta["best_exam"] = best_exam
    (run / "meta.json").write_text(json.dumps(meta, indent=1))
    tele.close(); tb.close()
    if minutes > 30:
        from report import write_report
        print("report:", write_report(run))
    print(json.dumps(meta))


if __name__ == "__main__":
    main()
