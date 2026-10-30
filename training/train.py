"""Train one level of the skill ladder from scratch (or resume), on the GPU.

    .venv/Scripts/python.exe train.py --level L0 --minutes 5            (Unreal Editor closed for long runs)

TensorBoard starts with the run (http://localhost:6006). A brain is exported to ONNX only on graduation
(AGENTS.md): mean episode return >= the level's target over 100 consecutive episodes, zero NaN, and the
level's own exam passed by the deterministic policy. runs/<run>/: latest.pt, telemetry.jsonl, meta.json,
report.html (runs over 30 min). At start, runs older than 24 h are pruned (housekeeping).
"""
import argparse, copy, json, os, shutil, socket, subprocess, sys, time
from collections import deque
from pathlib import Path

import torch
import warp as wp
from torch.utils.tensorboard import SummaryWriter

from brain_cpu import LAGS, OBS, pca_projection, video
from constants import GAZE_ON
from envs.arena import ArenaEnv, VsFrozen
from envs.locomotion import LocomotionEnv
from envs.slap import SlapEnv
from levels import LEVELS
from ppo import PPO, PPOConfig, export_onnx, obs_dim_of

HERE = Path(__file__).parent
RUNS = HERE / "runs"
TAG = {0: "guard", 1: "strike", -1: "absorb"}


def housekeeping(keep=()):
    """Clean up runs (AGENTS.md). Never touched: runs in `keep`, any run with a file written in the last 24 h (still
    active or just finished), and any run a queue stage still to run names (its resume point). Every other run goes:
    failed ones (a crash.log, or a non-zero exit code in meta.json) and, since 2026-10-27 (user), old finished ones too,
    whose brain, if it graduated, is already in models/brains. Its runs/<run>.log goes with it.
    (Replaces 'prune under 5,000 steps': 2026-10-22 that rule deleted long crashed runs, one of them mid-resume.)"""
    keep = {Path(k).resolve() for k in keep}
    pending = ""
    for q in RUNS.glob("*queue*.json"):
        try:
            pending += json.dumps([s for s in json.loads(q.read_text()) if not s.get("done")])
        except (ValueError, TypeError, AttributeError):
            pending += q.read_text()   # not a stage list: keep every run it mentions
    for d in RUNS.glob("*"):
        if not d.is_dir() or d.resolve() in keep or f"runs/{d.name}/" in pending or f'"{d.name}"' in pending:
            continue
        files = [f for f in d.rglob("*") if f.is_file()]
        if files and time.time() - max(f.stat().st_mtime for f in files) < 24 * 3600:
            continue
        meta = json.loads((d / "meta.json").read_text()) if (d / "meta.json").exists() else {}
        failed = (d / "crash.log").exists() or meta.get("exit_code", 0) != 0
        shutil.rmtree(d, ignore_errors=True)
        (RUNS / f"{d.name}.log").unlink(missing_ok=True)
        print(f"removed {'failed' if failed else 'old'} run {d.name}")


def tensorboard():
    with socket.socket() as s:
        if s.connect_ex(("127.0.0.1", 6006)) == 0:
            return
    exe = Path(sys.executable).with_name("tensorboard.exe")
    subprocess.Popen([str(exe), "--logdir", str(RUNS), "--port", "6006"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    print("TensorBoard: http://localhost:6006")


def git_sha():
    """The commit the run trains from (and whether the tree was dirty): 'dirty' means the run is not the commit."""
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, check=True).stdout.strip())
        return sha, dirty
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "", False


def write_config_snapshot(run, args):
    """The run's config snapshot (Realism 10: multi-instance runs record exactly what they train).

    The resolved level config, the character card, the git commit, the library versions and the command line,
    written before the first step into runs/<run>/config_snapshot.json and into DOCS/<YYYYMMDD>/config_snapshots/.
    """
    sha, dirty = git_sha()
    card_path = HERE / "characters" / f"{args.char}.json"
    card = json.loads(card_path.read_text()) if card_path.exists() else {}
    snap = {
        "run": run.name,
        "started": time.strftime("%Y-%m-%d %H:%M:%S"),
        "command": " ".join(sys.argv),
        "args": {k: v for k, v in vars(args).items()},
        "level": args.level,
        "level_config": LEVELS.get(args.level, {}),
        "character": args.char,
        "card": card,
        "git_sha": sha,
        "git_dirty": dirty,
        "versions": {"python": sys.version.split()[0], "torch": torch.__version__, "mujoco": __import__("mujoco").__version__},
        "obs_dim": OBS,
        "ppo": {"lr": PPOConfig().lr, "init_std": PPOConfig().init_std, "max_std": args.max_std, "entropy": args.entropy},
        "worlds": args.envs,
        "gaze_servo": GAZE_ON,
        "servo_mix": os.environ.get("SLAP_SERVO_MIX", "0.0"),
    }
    (run / "config_snapshot.json").write_text(json.dumps(snap, indent=1, default=str))
    day = HERE.parent / "DOCS" / time.strftime("%Y%m%d") / "config_snapshots"
    day.mkdir(parents=True, exist_ok=True)
    (day / f"{run.name}.json").write_text(json.dumps(snap, indent=1, default=str))
    print(f"config snapshot: {run.name}/config_snapshot.json (git {sha[:8]}{' dirty' if dirty else ''})")


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
def arena_exam(ppo, character, level, opponent=None):
    """L4: 100 fresh worlds, one turn each, coin-toss striker: KO rate per landed strike, striker self-falls, strikes."""
    chk = LEVELS[level]["check"]
    env = _exam_envs.get((character, level, opponent))
    if env is None:   # opponent: (character, checkpoint) of a frozen corner (VsFrozen): the exam scores the matchup
        env = _exam_envs[(character, level, opponent)] = (VsFrozen(character, *opponent, level, num_envs=chk["episodes"], seed=12345)
                                                          if opponent else ArenaEnv(character, level, num_envs=chk["episodes"], seed=12345))
    torch.cuda.set_stream(env.stream)
    wp.set_stream(wp.stream_from_torch(env.stream))
    env.reset(torch.ones(env.W, dtype=torch.bool, device="cuda"))
    obs = env.observe()
    ko, fall, struck, seen = (torch.zeros(env.W, dtype=torch.bool, device="cuda") for _ in range(4))
    for _ in range(env.T):
        a = ppo.model.actor(ppo.obs_rms.normalize(obs, ppo.cfg.obs_clip))
        st = env.struck.clone()
        obs, _, done, _ = env.step(a)
        dw = done.view(env.W, -1)[:, 0]
        live = ~seen & dw
        ko |= live & env.ko_now
        fall |= live & env.self_fall_now
        struck |= live & st
        seen |= dw
        if bool(seen.all()):
            break
    n = max(1, int(struck.sum()))
    res = {"strikes": struck.float().mean().item(), "ko_rate": int(ko.sum()) / n, "self_fall": fall.float().mean().item()}
    lo, hi = chk["ko_rate"]
    passed = res["strikes"] >= chk["min_strikes"] and lo <= res["ko_rate"] <= hi and res["self_fall"] <= chk["max_self_fall"]
    res["score"] = res["strikes"] * (1 - res["self_fall"]) * (1.0 if lo <= res["ko_rate"] <= hi else 0.5)
    torch.cuda.set_stream(ppo_stream)
    wp.set_stream(wp.stream_from_torch(ppo_stream))
    return passed, res


@torch.no_grad()
def exam(ppo, character, level):
    """Every role's pass/fail check on the deterministic policy, 100 fresh randomised worlds each (first episode only)."""
    L = LEVELS[level]
    res, passed = {}, True
    env = _exam_envs.get((character, level))
    loco = L.get("model") == "locomotion"
    if env is None:   # built once: a fresh env (and CUDA stream) per exam broke Warp's capture after ~30 exams
        env = _exam_envs[(character, level)] = (LocomotionEnv if loco else SlapEnv)(character, level, num_envs=100, seed=12345)
    torch.cuda.set_stream(env.stream)
    wp.set_stream(wp.stream_from_torch(env.stream))
    env.push_scale = L.get("max_push", 1.0)           # exams use the level's own bars
    env.hit_v = float(L.get("hit_speed", 8.0))
    env.reach = 1.0                                     # and the real opponent's head
    env.slap_j, env.exam = float(L.get("slap_impulse", 0.0)), True
    for role, chk in L["check"].items():
        env.force_role = role
        env.reset(torch.ones(env.N, dtype=torch.bool, device="cuda"))
        obs = env.observe()
        fell, left_box, hit, absorbed, seen = (torch.zeros(env.N, dtype=torch.bool, device="cuda") for _ in range(5))
        pace = torch.zeros(env.N, device="cuda")   # walking levels: distance walked over distance commanded
        stop = torch.zeros(env.N, dtype=torch.bool, device="cuda")   # ... and standing within 0.3 m of the box at the end
        clear = torch.zeros(env.N, dtype=torch.bool, device="cuda")  # the obstacle course: stepped up onto the step
        reach = torch.zeros(env.N, dtype=torch.bool, device="cuda")  # ... and reached the last waypoint
        for _ in range(env.T):
            a = ppo.model.actor(ppo.obs_rms.normalize(obs, ppo.cfg.obs_clip))
            lb = getattr(env, "left_box", torch.zeros_like(fell)).clone()
            if loco:   # read before the step: a world that finishes is reset inside it
                p0 = env.dist_walked / env.cmd_dist.clamp(min=1e-3)
                s0 = env.rem.abs() < 0.3
                c0 = getattr(env, "ep_clear", torch.zeros_like(fell)).clone()
                r0 = getattr(env, "reach_paid", torch.zeros_like(fell)).clone()
            obs, _, done, timeout = env.step(a)
            live = ~seen
            if loco:
                pace = torch.where(live & done, p0.clamp(max=1.5), pace)
                stop = torch.where(live & done, s0 & ~env.fell_now, stop)
                clear = torch.where(live & done, c0, clear)
                reach = torch.where(live & done, r0 & ~env.fell_now, reach)
            fell |= live & env.fell_now
            left_box |= live & lb
            hit |= live & getattr(env, "done_hit", torch.zeros_like(fell))
            absorbed |= live & getattr(env, "done_absorbed", ~env.fell_now & (env.com_err <= 0.08))
            seen |= done
            if bool(seen.all()):
                break
        tag = TAG[role]
        r = {f"{tag}_no_fall": 1.0 - fell.float().mean().item()}
        if "min_in_box" in chk:
            r[f"{tag}_in_box"] = 1.0 - left_box.float().mean().item()
        if "min_hit" in chk:        # strike >= 8 m/s and still up 1.5 s later
            r[f"{tag}_hit"] = hit.float().mean().item()
        if "min_absorbed" in chk:   # up 5 s after a median slap, COM back within 5 cm
            r[f"{tag}_absorbed"] = absorbed.float().mean().item()
        if "min_pace" in chk:       # 2026-10-26: S2's "graduated" brains stood still (0.15 m in 20 s); no-fall alone can't tell
            r[f"{tag}_pace"] = pace.mean().item()
        if "min_stop" in chk:       # 2026-10-29: the walk-in ends at the foot box; Kim's walker ran past hers and fell
            r[f"{tag}_stop"] = stop.float().mean().item()
        if "min_clear" in chk:      # the obstacle course: stepped up onto the low step
            r[f"{tag}_clear"] = clear.float().mean().item()
        if "min_reach" in chk:      # ... and got past the podium to the last waypoint, still on his feet
            r[f"{tag}_reach"] = reach.float().mean().item()
        for k, v in r.items():
            passed &= v >= chk.get("min_" + k.split("_", 1)[1], 0.0) - 1e-6   # float32 means: 90/100 reads 0.8999999762
        res.update(r)
    env.force_role, env.exam = None, False
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
    ap.add_argument("--critic-warmup", type=int, default=0, help="iterations the critic learns alone after --resume (a fine-tune on new conditions collapsed the slap within minutes)")
    ap.add_argument("--roles", default=None, help="override the level's role mix, e.g. 1:1 (strike only); exams still check every role")
    ap.add_argument("--opponent", default=None, help="L4 against a frozen brain: <character>:<checkpoint .pt> (a mixed pairing)")
    ap.add_argument("--out", default=None, help="graduated brain's name in models/brains (default <char>_<level>[_vs_<opponent>]): a challenger keeps the champions")
    ap.add_argument("--reach-start", type=float, default=None, help="strike target from easy (0) to the real head (1); overrides the level's start")
    ap.add_argument("--imit-start", type=float, default=None, help="reference-slap imitation weight at the start; overrides the level's (0: no reference slap)")
    ap.add_argument("--keep-going", action="store_true", help="after graduating (ONNX exported), keep training; best.pt keeps the best exam")
    ap.add_argument("--obs-project", type=int, default=0,
                    help="prune the observation to N channels INSIDE the network's input layer (a fixed projection, "
                         "baked into the exported ONNX: the brain I/O stays the same 136 floats and the C++/JS "
                         "engines need no change)")
    a = ap.parse_args()
    RUNS.mkdir(exist_ok=True)
    run_dir = RUNS / (a.run or "")
    housekeeping(keep=[run_dir] + ([Path(a.resume).parent] if a.resume else []))
    tensorboard()
    run = RUNS / (a.run or f"{a.char}_{a.level}_{time.strftime('%y%m%d_%H%M')}")
    run.mkdir(exist_ok=True)
    tb, tele = SummaryWriter(str(run)), open(run / "telemetry.jsonl", "a")
    write_config_snapshot(run, a)   # what is being trained, with what, from which commit (AGENTS.md: the run's own record)

    arena = LEVELS[a.level].get("model") == "arena"
    loco = LEVELS[a.level].get("model") == "locomotion"
    opp = tuple(a.opponent.split(":", 1)) if a.opponent else None
    vs = f"_vs_{opp[0]}" if opp else ""   # brains from a mixed pairing never overwrite the self-play graduates
    out_name = a.out or f"{a.char}_{a.level}{vs}"
    env_cls = ArenaEnv if arena else (LocomotionEnv if loco else SlapEnv)
    env = VsFrozen(a.char, *opp, a.level, num_envs=a.envs) if opp else env_cls(a.char, a.level, num_envs=a.envs)
    global ppo_stream
    ppo_stream = env.stream
    proj = None
    if a.obs_project > 0:   # Realism 5: the redundant channels pruned inside the network (the I/O is unchanged)
        import numpy as np
        s = [env.observe().detach().cpu().numpy()]
        for _ in range(20):
            env.step(torch.randn(env.N, env.A, device="cuda") * 0.2)
            s.append(env.observe().detach().cpu().numpy())
        _, comp = pca_projection(np.concatenate(s, 0), a.obs_project)
        proj = torch.tensor(comp, device="cuda")
        print(f"input projection: {OBS} -> {comp.shape[1]} (a fixed PCA basis, baked into the ONNX)")
        env.reset(torch.ones(env.N, dtype=torch.bool, device="cuda"))
    ppo = PPO(OBS, env.A, env.N, "cuda", PPOConfig(init_std=0.25, lr_min=1e-4, max_std=a.max_std, entropy_coef=a.entropy, group_obs=100), proj=proj)
    extra = ppo.load(a.resume, widen=True) if a.resume else {}
    if a.resume:   # carry on at the rate it had reached: the 1e-3 default broke a resumed slap within minutes (2026-10-23)
        ppo.cfg.lr = float(extra.get("lr", ppo.cfg.lr_min))
    if a.resume and obs_dim_of(a.resume) == 108:   # memory inputs start with the scale of what they remember (102-107)
        for i in range(len(LAGS)):
            ppo.obs_rms.mean[108 + 6 * i:114 + 6 * i] = ppo.obs_rms.mean[102:108]
            ppo.obs_rms.var[108 + 6 * i:114 + 6 * i] = ppo.obs_rms.var[102:108]
    if a.resume and obs_dim_of(a.resume) <= 130:   # own palm (130-135, new in BRAIN_IO): the scale of the first batch
        o = env.observe()
        ppo.obs_rms.mean[130:], ppo.obs_rms.var[130:] = o[:, 130:].mean(0), o[:, 130:].var(0) + 1e-4
    unseen = ppo.obs_rms.var < 1e-6                    # inputs a resumed brain only ever saw as 0 (a new one in BRAIN_IO)
    ppo.obs_rms.var[unseen], ppo.obs_rms.mean[unseen] = 1.0, 0.0
    with torch.no_grad():
        ppo.model.log_std.clamp_(max=float(torch.log(torch.tensor(a.max_std))))
    L = LEVELS[a.level]
    if a.roles:
        L = {**L, "roles": {int(r): float(p) for r, p in (x.split(":") for x in a.roles.split(","))}}
        env.roles = L["roles"]
        env.reset(torch.ones(env.N, dtype=torch.bool, device="cuda"))
    roles = list(L["roles"])
    # curricula, carried in checkpoints. Guard: pushes from 0 up to the level's cap (graduation only at the cap).
    # Strike: target from easy to the real head, then the clean-hit speed bar. Absorb: slap impulse up to the median.
    # A value carries over only from a checkpoint whose level trained that role (an L1 checkpoint holds placeholder
    # strike values that would skip the strike curriculum).
    had = LEVELS[extra["level"]].get("roles", {}) if extra.get("level") in LEVELS else {}
    carry = lambda key, role, start: float(extra[key]) if role in had and key in extra else start
    env.push_scale = min(carry("push_scale", 0, 0.0), L.get("max_push", 1.0)) if L.get("pushes") else 0.0
    env.hit_v = carry("hit_v", 1, float(L.get("hit_speed_start", L.get("hit_speed", 8.0))))
    env.reach = carry("reach", 1, float(a.reach_start if a.reach_start is not None else L.get("reach_start", 0.0 if "hit_speed_start" in L else 1.0)))
    env.imit = carry("imit", 1, float(a.imit_start if a.imit_start is not None else L.get("imit_start", 0.0)))
    env.slap_j = carry("slap_j", -1, float(L.get("slap_impulse_start", 0.0)))
    curr = lambda: {"push_scale": env.push_scale, "hit_v": env.hit_v, "reach": env.reach, "slap_j": env.slap_j, "imit": env.imit, "lr": ppo.cfg.lr}
    target = L["reward_target"]
    recent = {r: deque(maxlen=100) for r in roles}
    obs, t0, steps, it, last_exam, graduated, nan = env.observe(), time.time(), 0, 0, -1e9, None, False
    last_video, best_exam = time.time(), -1.0
    (run / "videos").mkdir(exist_ok=True)
    print(f"run {run.name}: {a.level}, roles {L['roles']}, {a.envs} worlds, {a.minutes} min, graduate at returns {target} over 100 episodes each")
    mean = lambda xs: sum(xs) / max(1, len(xs))

    while time.time() - t0 < a.minutes * 60:
        for _ in range(ppo.cfg.steps_per_env):
            act = ppo.act(obs)
            obs, rew, done, timeout = env.step(act)
            ppo.record(rew, done, timeout)
        steps += ppo.cfg.steps_per_env * env.N
        ppo.actor_frozen = it < a.critic_warmup
        stats = ppo.update(obs)
        it += 1
        eps, env.episodes = env.episodes, []
        for e in eps:
            recent[e["role"]].append(e)
        nan = nan or any(v != v for v in stats.values()) or not all(torch.isfinite(p).all() for p in ppo.model.parameters())
        if nan:
            print("NaN in training: stopping, nothing exported")
            break
        m = {}
        for r, rec in recent.items():
            tag = TAG[r]
            m[f"{tag}_return"] = mean([e["ret"] for e in rec])
            m[f"{tag}_fall"] = mean([e["fell"] for e in rec])
            m[f"{tag}_episode_s"] = mean([e["len"] for e in rec]) / 50
            if r == 1:
                m["strike_hit"] = mean([e["hit"] for e in rec])
                m["strike_whiff"] = mean([e.get("whiff", False) for e in rec])   # missed swings (each ends the attempt)
                m["strike_speed"] = mean([e["speed"] for e in rec if e["speed"] > 0])   # m/s at contact
            if r == 1 and arena:
                m["strike_ko"] = sum(e["ko"] for e in rec) / max(1, sum(e["struck"] for e in rec))   # per landed strike
            if r == -1:
                m["absorb_ok"] = mean([e["absorbed"] for e in rec])
                m["absorb_com_cm"] = 100 * mean([e["com_err"] for e in rec])
        full = all(len(rec) == 100 for rec in recent.values())
        if 0 in recent and L.get("pushes") and len(recent[0]) == 100:
            if m["guard_episode_s"] > 0.8 * env.T / 50:
                env.push_scale = min(L.get("max_push", 1.0), env.push_scale + 0.02)
            elif m["guard_episode_s"] < 0.4 * env.T / 50:
                env.push_scale = max(0.0, env.push_scale - 0.01)
            env.push_scale = round(env.push_scale, 2)   # 0.01/0.02 steps drifted to 0.5999999999999996 < 0.6: no exam ever
        if 1 in recent and len(recent[1]) == 100 and m["strike_hit"] > 0.5:   # first move the target out, then raise the bar
            if getattr(env, "imit", 0) > 0.5:   # clean hits coming: lean less on the reference slap, but keep its single
                env.imit = max(0.5, round(env.imit - 0.02, 2))   # swing (floor 0.5; at 0.2-0.3 he drifted into flailing)
            if env.reach < 1.0:
                env.reach = min(1.0, round(env.reach + 0.05, 2))
            else:
                env.hit_v = min(float(L["hit_speed"]), round(env.hit_v + 0.25, 2))
        if -1 in recent and "slap_impulse" in L and len(recent[-1]) == 100 and m["absorb_ok"] > 0.7:
            env.slap_j = min(float(L["slap_impulse"]), round(env.slap_j + 0.25, 2))
        for k, v in {**stats, **m, **curr()}.items():
            tb.add_scalar(k, v, steps)
        tele.write(json.dumps({"it": it, "steps": steps, "min": round((time.time() - t0) / 60, 2), "episodes": len(eps),
                               **m, **curr(), **stats, "pelvis_z_w0": env.qpos[0, 2].item()}) + "\n")
        tele.flush()
        if it % 10 == 0:
            line = " | ".join(f"{k} {v:.2f}" for k, v in m.items() if not k.endswith("episode_s"))
            print(f"it {it:5d} {steps / 1e6:7.1f}M {(time.time() - t0) / 60:5.1f} min | {line} | push {env.push_scale:.2f} "
                  f"bar {env.hit_v:.2f} reach {env.reach:.2f} imit {env.imit:.2f} slap {env.slap_j:.2f} | std {stats['action_std']:.3f} lr {stats['lr']:.1e}", flush=True)
        if it % 50 == 0:
            ppo.save(str(run / "latest.pt"), {"steps": steps, "level": a.level, **curr()})
        if time.time() - last_video > 600:   # checkpoint video every 10 min (CPU MuJoCo, pushes off)
            last_video = time.time()
            gif = run / "videos" / f"it{it:05d}_{(time.time() - t0) / 60:04.0f}min.gif"
            z = video(cpu_policy(ppo), gif, a.char, role=roles[0])
            print(f"video {gif.name}: pelvis at {z:.2f} m after 6 s", flush=True)
        ready = full and all(m[f"{TAG[r]}_return"] >= target[r] for r in roles) \
            and (0 not in roles or not L.get("pushes") or env.push_scale >= L.get("max_push", 1.0)) \
            and (1 not in roles or (env.hit_v >= float(L["hit_speed"]) and env.reach >= 1.0)) \
            and (-1 not in roles or env.slap_j >= float(L.get("slap_impulse", 0.0)))
        exam_cd = min(600, max(45, a.minutes * 10))
        if ready and time.time() - last_exam > exam_cd:   # up to 10 min of training between exams, scaled for shorter runs:
            ok, res = arena_exam(ppo, a.char, a.level, opp) if arena else exam(ppo, a.char, a.level)   # an exam takes 2-3 min (100 worlds, 30 s guard,
            last_exam = time.time()                                          # mostly CPU); at 120 s from its start, L2 did
            # 8 exams and 10 iterations in 23 min (2026-10-22)
            print(f"exam {a.level}: {res} -> {'PASS' if ok else 'not yet'}", flush=True)
            for k, v in res.items():
                tb.add_scalar("exam_" + k, v, steps)
            score = res.get("score", min(res.values()))
            if score > best_exam:   # the best exam so far, kept for a deadline that ends before graduation
                best_exam = score
                ppo.save(str(run / "best.pt"), {"steps": steps, "level": a.level, **curr(), "exam": res})
            if ok and not graduated:
                graduated = res
                out = HERE / "models" / "brains" / f"{out_name}.onnx"
                export_onnx(ppo, str(out), OBS, fixed_batch=True)
                print(f"GRADUATED -> {out}", flush=True)
                if not a.keep_going:
                    break

    ppo.save(str(run / "latest.pt"), {"steps": steps, "level": a.level, **curr()})
    minutes = (time.time() - t0) / 60
    meta = {"char": a.char, "level": a.level, "steps": steps, "iterations": it, "minutes": round(minutes, 1),
            **({k: v for k, v in m.items()} if it else {}), "graduated": graduated, "nan": nan, "exit_code": 0}
    if graduated and not nan:
        meta["onnx"] = f"models/brains/{out_name}.onnx"
    meta["best_exam"] = best_exam
    if (run / "crash.log").exists():   # crashed earlier, then resumed to the end: not a failed run
        (run / "crash.log").replace(run / "recovered.log")
    (run / "meta.json").write_text(json.dumps(meta, indent=1))
    tele.close(); tb.close()
    if minutes >= 2:
        from report import write_report
        print("report:", write_report(run))
    print(json.dumps(meta))


if __name__ == "__main__":
    try:
        main()
    except (Exception, KeyboardInterrupt):   # housekeeping's "failed run" marker (AGENTS.md)
        import traceback
        run = next((sys.argv[i + 1] for i, x in enumerate(sys.argv) if x == "--run"), None)
        if run and (RUNS / run).is_dir():
            (RUNS / run / "crash.log").write_text(traceback.format_exc())
        raise
