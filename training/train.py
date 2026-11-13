"""Train one level of the skill ladder from scratch (or resume), on the GPU.

    .venv/Scripts/python.exe train.py --level L0 --minutes 5            (Unreal Editor closed for long runs)

    .venv/Scripts/python.exe train.py --level L2 --physics 2 --minutes 600 --supervise 5   (crash guard, below)

TensorBoard starts with the run (http://localhost:6006). A brain is exported to ONNX only on graduation
(AGENTS.md): mean episode return >= the level's target over 100 consecutive episodes, zero NaN, and the
level's own exam passed by the deterministic policy. runs/<run>/: latest.pt, telemetry.jsonl, meta.json,
report.html (runs over 30 min). At start, runs older than 24 h are pruned (housekeeping).

--physics 2 trains on the realism batch (models/v2/, DOCS/20261103/training_realism.md); the default is the shipped 1.
--allow-config-change: a resume compares the card, the model and (same level) the level config with the checkpoint's
config snapshot (item 49) and refuses on a mismatch; this flag lets a deliberate change (a new physics) through.
Checkpoints carry their interface and physics tags (extra.interface / physics / obs_dim / config, AGENTS.md section 3).
--supervise N (the CUDA 719 guard, tasks J10): the run is a child process; when it dies (a GPU fault kills the CUDA
context, so it cannot be caught in-process) it is resumed from its latest.pt after the GPU cools, up to N times,
inside the same --minutes. Checkpoints are written every 3 minutes, so a crash costs at most that.
Every 10 iterations the telemetry carries the buffers' fill (nefc vs njmax, contacts vs naconmax, Warp's overflow
bits), the worlds reset after a blow-up, and the GPU's temperature and clock.
"""
import argparse, copy, hashlib, json, os, shutil, socket, subprocess, sys, time
from collections import deque
from pathlib import Path

import torch
import warp as wp
from torch.utils.tensorboard import SummaryWriter

from brain_cpu import LAGS, OBS, pca_projection, video
from constants import GAZE_ON, INTERFACE, interface_of, models_dir, physics
from envs.common import buffer_health
from gpuwatch import cooldown, gpu_status
from envs.arena import ArenaEnv, Field, VsFrozen
from envs.locomotion import LocomotionEnv
from envs.slap import SlapEnv
from envs.getup import GetUpEnv
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


def _h(obj):
    """sha256 of a JSON-able object, canonical (key order, tuples as lists, int keys as strings: what a snapshot holds)."""
    return hashlib.sha256(json.dumps(json.loads(json.dumps(obj, default=str)), sort_keys=True).encode()).hexdigest()


def config_hashes(char, level):
    """Item 49: what a checkpoint was trained on, hashed: the character card, the model (the fingerprint's sha256 of the
    solo XML, with the physics version) and the level's config."""
    card_path = HERE / "characters" / f"{char}.json"
    fp_path = models_dir() / f"{char}_fingerprint.json"
    return {"character": char, "card": _h(json.loads(card_path.read_text())) if card_path.exists() else None,
            "model": json.loads(fp_path.read_text()).get("sha256") if fp_path.exists() else None,
            "physics": physics(), "level": level, "level_config": _h(LEVELS.get(level, {}))}


def tags(char, level):
    """The tags every checkpoint carries (AGENTS.md section 3): its interface, physics and the config hashes."""
    return {"interface": INTERFACE, "physics": physics(), "obs_dim": OBS, "config": config_hashes(char, level)}


def check_config(resume, char, level, allow):
    """Item 49, before the policy is built: the checkpoint's config (its tags, else the config_snapshot.json beside it)
    against this run's. A different card, model, physics or character - or the same level with a different level
    config - is refused unless --allow-config-change. Returns the differences (written into the run's snapshot)."""
    ck = Path(resume)
    old = None
    try:
        old = torch.load(str(ck), map_location="cpu", weights_only=True).get("extra", {}).get("config")
    except Exception:
        pass
    if old is None and (ck.parent / "config_snapshot.json").exists():   # checkpoints before the tags: the run's snapshot
        snap = json.loads((ck.parent / "config_snapshot.json").read_text())
        old = snap.get("hashes") or {"character": snap.get("character"), "card": _h(snap["card"]) if snap.get("card") else None,
                                     "model": snap.get("fingerprint_sha256"), "physics": snap.get("physics", 1),
                                     "level": snap.get("level"), "level_config": _h(snap.get("level_config", {}))}
    if old is None:
        print(f"config check: {ck} has no tags and no config snapshot beside it: unchecked")
        return ["unchecked: no snapshot"]
    new = config_hashes(char, level)
    keys = ["character", "card", "model", "physics"] + (["level_config"] if old.get("level") == level else [])
    diff = [f"{k}: {str(old.get(k))[:12]} -> {str(new.get(k))[:12]}" for k in keys if old.get(k) is not None and old.get(k) != new.get(k)]
    if diff and not allow:
        sys.exit(f"config check: refusing to resume {ck} - its config differs ({'; '.join(diff)}). "
                 f"Pass --allow-config-change if the change is meant (AGENTS.md section 3: log it in DOCS first).")
    same = "same card, model and physics" + (", same level config" if "level_config" in keys else "")
    print(f"config check: {ck.name} vs this run: " + ("; ".join(diff) + " (allowed)" if diff else same))
    return diff


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
        "interface": INTERFACE,
        "hashes": config_hashes(args.char, args.level),
        "config_changes": getattr(args, "config_changes", []),
        "ppo": {"lr": PPOConfig().lr, "init_std": PPOConfig().init_std, "max_std": args.max_std, "entropy": args.entropy},
        "worlds": args.envs,
        "gaze_servo": GAZE_ON,
        "servo_mix": os.environ.get("SLAP_SERVO_MIX", "0.0"),
        "physics": physics(),
        "models_dir": str(models_dir().relative_to(HERE)),
        "fingerprint_sha256": (json.loads((models_dir() / f"{args.char}_fingerprint.json").read_text()).get("sha256")
                               if (models_dir() / f"{args.char}_fingerprint.json").exists() else None),
        "gpu": gpu_status(),
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
        x = torch.clamp((torch.from_numpy(o[:mean.shape[0]]) - mean) / std, -clip, clip)
        return actor(x[None])[0].numpy()
    act.n_in = int(mean.shape[0])   # brain_cpu keys the low-pass on the width
    return act


_exam_envs = {}


@torch.no_grad()
def arena_exam(ppo, character, level, opponent=None):
    """L4: 100 fresh worlds, one turn each, coin-toss striker: KO rate per landed strike, striker self-falls, strikes."""
    chk = LEVELS[level]["check"]
    env = _exam_envs.get((character, level, opponent))
    if env is None:   # opponent: (character, checkpoint[, learner's corner]) of a frozen corner (VsFrozen): the exam scores the matchup
        env = _exam_envs[(character, level, opponent)] = (VsFrozen(character, opponent[0], opponent[1], level, num_envs=chk["episodes"], seed=12345,
                                                                   side=opponent[2] if len(opponent) > 2 else None)
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


def field_exam(ppo, character, level, opponents):
    """--field: the arena exam in every seat (self-play, then each opponent with the learner red and blue). Passes when
    every seat passes; the score is the weakest seat's, so a brain cannot graduate on its easy pairings."""
    res, passed, scores = {}, True, []
    seats = [None] + [(o, c, side) for o, c in opponents for side in (0, 1)]
    for seat in seats:
        ok, r = arena_exam(ppo, character, level, seat)
        tag = "self" if seat is None else f"{'red' if seat[2] == 0 else 'blue'}_vs_{seat[0]}"
        res.update({f"{tag}_{k}": v for k, v in r.items()})
        passed &= ok
        scores.append(r["score"])
    res["score"] = min(scores)
    return passed, res


@torch.no_grad()
def exam(ppo, character, level):
    """Every role's pass/fail check on the deterministic policy, 100 fresh randomised worlds each (first episode only)."""
    L = LEVELS[level]
    res, passed = {}, True
    env = _exam_envs.get((character, level))
    loco = L.get("model") == "locomotion"
    if env is None:   # built once: a fresh env (and CUDA stream) per exam broke Warp's capture after ~30 exams
        cls = LocomotionEnv if loco else GetUpEnv if L.get("model") == "getup" else SlapEnv
        env = _exam_envs[(character, level)] = cls(character, level, num_envs=100, seed=12345)
    torch.cuda.set_stream(env.stream)
    wp.set_stream(wp.stream_from_torch(env.stream))
    env.push_scale = L.get("max_push", 1.0)           # exams use the level's own bars
    env.hit_v = float(L.get("hit_speed", 8.0))
    env.reach = 1.0                                     # and the real opponent's head
    env.slap_j, env.exam = float(L.get("slap_impulse", 0.0)), True
    env.step_scale = 1.0                                # the obstacle course: the full step
    env.slope_scale = 1.0                               # and the full tilt (S5)
    for role, chk in L["check"].items():
        env.force_role = role
        env.reset(torch.ones(env.N, dtype=torch.bool, device="cuda"))
        obs = env.observe()
        fell, left_box, hit, absorbed, seen = (torch.zeros(env.N, dtype=torch.bool, device="cuda") for _ in range(5))
        pace = torch.zeros(env.N, device="cuda")   # walking levels: distance walked over distance commanded
        stop = torch.zeros(env.N, dtype=torch.bool, device="cuda")   # ... and standing within 0.3 m of the box at the end
        clear = torch.zeros(env.N, dtype=torch.bool, device="cuda")  # the obstacle course: stepped up onto the step
        down = torch.zeros(env.N, dtype=torch.bool, device="cuda")   # ... stepped down off its far side
        reach = torch.zeros(env.N, dtype=torch.bool, device="cuda")  # ... and reached the last waypoint
        up = torch.zeros(env.N, dtype=torch.bool, device="cuda")     # get-up (G1): up and held 1 s
        for _ in range(env.T):
            a = ppo.model.actor(ppo.obs_rms.normalize(obs, ppo.cfg.obs_clip))
            lb = getattr(env, "left_box", torch.zeros_like(fell)).clone()
            if loco:   # read before the step: a world that finishes is reset inside it
                p0 = env.dist_walked / env.cmd_dist.clamp(min=1e-3)
                s0 = env.rem.abs() < 0.3
                c0 = getattr(env, "ep_clear", torch.zeros_like(fell)).clone()
                d0 = getattr(env, "ep_down", torch.zeros_like(fell)).clone()
                r0 = getattr(env, "reach_paid", torch.zeros_like(fell)).clone()
            obs, _, done, timeout = env.step(a)
            live = ~seen
            if loco:
                pace = torch.where(live & done, p0.clamp(max=1.5), pace)
                stop = torch.where(live & done, s0 & ~env.fell_now, stop)
                clear = torch.where(live & done, c0, clear)
                down = torch.where(live & done, d0 & ~env.fell_now, down)
                reach = torch.where(live & done, r0 & ~env.fell_now, reach)
            fell |= live & env.fell_now
            left_box |= live & lb
            hit |= live & getattr(env, "done_hit", torch.zeros_like(fell))
            absorbed |= live & getattr(env, "done_absorbed", ~env.fell_now & (env.com_err <= 0.08))
            up |= live & getattr(env, "done_up", torch.zeros_like(fell))
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
        if "min_down" in chk:       # ... and stepped down off its far side, still on his feet (2026-11-03)
            r[f"{tag}_down"] = down.float().mean().item()
        if "min_reach" in chk:      # ... and got past the podium to the last waypoint, still on his feet
            r[f"{tag}_reach"] = reach.float().mean().item()
        if "min_up" in chk:         # get-up (G1): standing on the soles alone within the 10 s
            r[f"{tag}_up"] = up.float().mean().item()
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
    ap.add_argument("--field", default=None, help="one brain per player: L4 in self-play AND against each frozen "
                    "<character>:<checkpoint .pt> (comma-separated) from both corners; graduates as <char>_all when every seat passes")
    ap.add_argument("--out", default=None, help="graduated brain's name in models/brains (default <char>_<level>[_vs_<opponent>]): a challenger keeps the champions")
    ap.add_argument("--reach-start", type=float, default=None, help="strike target from easy (0) to the real head (1); overrides the level's start")
    ap.add_argument("--imit-start", type=float, default=None, help="reference-slap imitation weight at the start; overrides the level's (0: no reference slap)")
    ap.add_argument("--keep-going", action="store_true", help="after graduating (ONNX exported), keep training; best.pt keeps the best exam")
    ap.add_argument("--obs-project", type=int, default=0,
                    help="prune the observation to N channels INSIDE the network's input layer (a fixed projection, "
                         "baked into the exported ONNX: the brain I/O stays the same 136 floats and the C++/JS "
                         "engines need no change)")
    ap.add_argument("--physics", type=int, default=None, choices=(1, 2, 3, 4),
                    help="the physics version's models: 1 shipped (models/), n = models/v<n>/ (2 the realism batch, 3 the face, "
                         "4 the v3 bodies under rules 2: training/rules.json); default SLAP_PHYSICS or 1")
    ap.add_argument("--allow-config-change", action="store_true",
                    help="resume although the checkpoint's card / model / physics / level config differ (item 49)")
    ap.add_argument("--supervise", type=int, default=0, help="crash guard: resume a crashed run from latest.pt up to N times (see the docstring)")
    a = ap.parse_args()
    if a.physics is not None:   # before any env is built: every env reads its models from constants.models_dir()
        os.environ["SLAP_PHYSICS"] = str(a.physics)
    a.config_changes = check_config(a.resume, a.char, a.level, a.allow_config_change) if a.resume else []   # before anything is written
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
    field = [tuple(x.split(":", 1)) for x in a.field.split(",")] if a.field else None
    vs = f"_vs_{opp[0]}" if opp else ""   # brains from a mixed pairing never overwrite the self-play graduates
    out_name = a.out or (f"{a.char}_all" if field else f"{a.char}_{a.level}{vs}")
    if physics() >= 2 and not a.out:   # a physics-2 brain never overwrites a shipped (physics 1) one in models/brains
        out_name += f"_v{physics()}"
    env_cls = ArenaEnv if arena else (LocomotionEnv if loco else GetUpEnv if LEVELS[a.level].get("model") == "getup" else SlapEnv)
    env = (Field(a.char, field, a.level, num_envs=a.envs) if field
           else VsFrozen(a.char, *opp, a.level, num_envs=a.envs) if opp else env_cls(a.char, a.level, num_envs=a.envs))
    if field:
        print(f"field: seats {env.names} ({env.N} agents)")
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
    if a.resume and obs_dim_of(a.resume) < OBS:   # AGENTS.md section 3: appended inputs start with zero first-layer weights
        n0 = obs_dim_of(a.resume)
        z = max(float(ppo.model.actor[0].weight[:, n0:].abs().max()), float(ppo.model.critic[0].weight[:, n0:].abs().max()))
        print(f"interface {interface_of(n0)} -> {INTERFACE}: inputs {n0}-{OBS - 1} added with zero weights (max |w| {z:g}); "
              f"the action low-pass is on from here", flush=True)
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
    if loco:   # the obstacle course's step height (S3): from a sill to the full step as the clear rate comes
        env.step_scale = float(extra["step_scale"]) if extra.get("level") == a.level and "step_scale" in extra else float(L.get("step_scale_start", 1.0))
        # the slope (S5, item 45): the tilt's share of slope_deg, from 0 as the no-fall rate holds
        env.slope_scale = float(extra["slope_scale"]) if extra.get("level") == a.level and "slope_scale" in extra else float(L.get("slope_scale_start", 1.0))
    curr = lambda: {"push_scale": env.push_scale, "hit_v": env.hit_v, "reach": env.reach, "slap_j": env.slap_j, "imit": env.imit, "lr": ppo.cfg.lr,
                    **({"step_scale": env.step_scale, "slope_scale": env.slope_scale} if loco else {})}
    target = L["reward_target"]
    recent = {r: deque(maxlen=100) for r in roles}
    obs, t0, steps, it, last_exam, graduated, nan = env.observe(), time.time(), 0, 0, -1e9, None, False
    last_video, best_exam, last_save = time.time(), -1.0, time.time()
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
            if rec and "clear" in rec[0]:      # the obstacle course: cleared the step, reached the last waypoint
                m[f"{tag}_clear"] = mean([e["clear"] for e in rec])
                m[f"{tag}_reach"] = mean([e["reach"] for e in rec])
                m[f"{tag}_waypoints"] = mean([e["waypoints"] for e in rec])
                m[f"{tag}_down"] = mean([e.get("down", False) for e in rec])
            if r == 1:
                m["strike_hit"] = mean([e["hit"] for e in rec])
                # the curricula's signal: the speed bar made, palm or not (rules 1: the same as strike_hit; rules 2: the
                # exam's hit also needs the palm - envs/slap.py fast_ok)
                m["strike_fast"] = mean([e.get("fast", e["hit"]) for e in rec])
                if any("palm" in e for e in rec):
                    m["strike_palm"] = sum(e.get("palm", False) for e in rec) / max(1, sum(e["speed"] > 0 for e in rec))   # per landed strike
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
        if 1 in recent and len(recent[1]) == 100 and m["strike_fast"] > 0.5:   # first move the target out, then raise the bar
            if getattr(env, "imit", 0) > 0.5:   # clean hits coming: lean less on the reference slap, but keep its single
                env.imit = max(0.5, round(env.imit - 0.02, 2))   # swing (floor 0.5; at 0.2-0.3 he drifted into flailing)
            if env.reach < 1.0:
                env.reach = min(1.0, round(env.reach + 0.05, 2))
            else:
                env.hit_v = min(float(L["hit_speed"]), round(env.hit_v + 0.25, 2))
        if -1 in recent and "slap_impulse" in L and len(recent[-1]) == 100 and m["absorb_ok"] > 0.7:
            env.slap_j = min(float(L["slap_impulse"]), round(env.slap_j + 0.25, 2))
        if loco and "guard_clear" in m and len(recent[0]) == 100 and env.step_scale < 1.0 and m["guard_clear"] > 0.6 \
                and m.get("guard_down", 0.0) > 0.5:   # the step grows once he climbs and comes down off the sill
            env.step_scale = min(1.0, round(env.step_scale + 0.01, 2))   # 0.05 per iteration hit the full step in 20 (smoke)
        if loco and "slope_scale_start" in L and len(recent[0]) == 100 and env.slope_scale < 1.0 and m["guard_fall"] < 0.10:
            env.slope_scale = min(1.0, round(env.slope_scale + 0.02, 2))   # +0.1 deg of tilt per good iteration
        if 0 in recent and recent[0] and "up" in recent[0][0]:
            m["getup_up"] = mean([e["up"] for e in recent[0]])
        if it % 10 == 0:   # the CUDA 719 watch: the buffers' fill, blow-ups and the GPU (one host sync per 10 iterations)
            seat = env.seats[0] if hasattr(env, "seats") else getattr(env, "env", env)
            m.update({f"buf_{k}": v for k, v in buffer_health(seat.dw).items()})
            m["blowups"] = int(seat.blowups) if hasattr(seat, "blowups") else 0
            if it % 50 == 0:
                m.update({f"gpu_{k}": v for k, v in gpu_status().items()})
        for k, v in {**stats, **m, **curr()}.items():
            tb.add_scalar(k, v, steps)
        tele.write(json.dumps({"it": it, "steps": steps, "min": round((time.time() - t0) / 60, 2), "episodes": len(eps),
                               **m, **curr(), **stats, "pelvis_z_w0": env.qpos[0, 2].item()}) + "\n")
        tele.flush()
        if it % 10 == 0:
            line = " | ".join(f"{k} {v:.2f}" for k, v in m.items() if not k.endswith("episode_s") and not k.startswith(("buf_", "gpu_")))
            print(f"it {it:5d} {steps / 1e6:7.1f}M {(time.time() - t0) / 60:5.1f} min | {line} | push {env.push_scale:.2f} "
                  f"bar {env.hit_v:.2f} reach {env.reach:.2f} imit {env.imit:.2f} slap {env.slap_j:.2f} | std {stats['action_std']:.3f} lr {stats['lr']:.1e}", flush=True)
        if it % 50 == 0 or time.time() - last_save > 180:   # every 3 min at most: what a crash can cost (--supervise)
            last_save = time.time()
            ppo.save(str(run / "latest.pt"), {"steps": steps, "level": a.level, **curr(), **tags(a.char, a.level)})
        if time.time() - last_video > 600:   # checkpoint video every 10 min (CPU MuJoCo, pushes off)
            last_video = time.time()
            gif = run / "videos" / f"it{it:05d}_{(time.time() - t0) / 60:04.0f}min.gif"
            z = video(cpu_policy(ppo), gif, a.char, role=roles[0])
            print(f"video {gif.name}: pelvis at {z:.2f} m after 6 s", flush=True)
        ready = full and all(m[f"{TAG[r]}_return"] >= target[r] for r in roles) \
            and (0 not in roles or not L.get("pushes") or env.push_scale >= L.get("max_push", 1.0)) \
            and (1 not in roles or (env.hit_v >= float(L["hit_speed"]) and env.reach >= 1.0)) \
            and (-1 not in roles or env.slap_j >= float(L.get("slap_impulse", 0.0))) \
            and (not loco or (getattr(env, "step_scale", 1.0) >= 1.0 and getattr(env, "slope_scale", 1.0) >= 1.0))
        exam_cd = min(600, max(45, a.minutes * 10))
        if ready and time.time() - last_exam > exam_cd:   # up to 10 min of training between exams, scaled for shorter runs:
            ok, res = (field_exam(ppo, a.char, a.level, field) if field else arena_exam(ppo, a.char, a.level, opp)) if arena else exam(ppo, a.char, a.level)   # an exam takes 2-3 min (100 worlds, 30 s guard,
            last_exam = time.time()                                          # mostly CPU); at 120 s from its start, L2 did
            # 8 exams and 10 iterations in 23 min (2026-10-22)
            print(f"exam {a.level}: {res} -> {'PASS' if ok else 'not yet'}", flush=True)
            for k, v in res.items():
                tb.add_scalar("exam_" + k, v, steps)
            score = res.get("score", min(res.values()))
            if score > best_exam:   # the best exam so far, kept for a deadline that ends before graduation
                best_exam = score
                ppo.save(str(run / "best.pt"), {"steps": steps, "level": a.level, **curr(), "exam": res, **tags(a.char, a.level)})
            if ok and not graduated:
                graduated = res
                out = HERE / "models" / "brains" / f"{out_name}.onnx"
                export_onnx(ppo, str(out), OBS, fixed_batch=True, meta={"interface": INTERFACE, "physics": physics(), "level": a.level})
                print(f"GRADUATED -> {out}", flush=True)
                if not a.keep_going:
                    break

    ppo.save(str(run / "latest.pt"), {"steps": steps, "level": a.level, **curr(), **tags(a.char, a.level)})
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
        try:   # the HTML report needs matplotlib; a venv without it must not turn a finished run into a failed one
            from report import write_report
            print("report:", write_report(run))
        except ImportError as e:
            print(f"report skipped ({e}): uv pip install matplotlib")
    print(json.dumps(meta))


def supervise():
    """--supervise N: run this command as a child; when it dies with a non-zero code (a GPU fault kills the CUDA
    context, so it cannot be caught in-process) and left a latest.pt, wait for the GPU to cool and resume it, up to
    N times, inside the original --minutes. The child is the same command without --supervise."""
    argv = sys.argv[1:]
    i = argv.index("--supervise")
    tries = int(argv[i + 1])
    argv = argv[:i] + argv[i + 2:]
    def opt(name, default=None):
        return argv[argv.index(name) + 1] if name in argv else default
    def put(name, value):
        nonlocal argv
        if name in argv:
            argv[argv.index(name) + 1] = value
        else:
            argv += [name, value]
    if opt("--run") is None:   # the child must write to a known run directory to be resumed from it
        put("--run", f"{opt('--char', 'grandpa')}_{opt('--level', 'L0')}_{time.strftime('%y%m%d_%H%M')}")
    run, end = RUNS / opt("--run"), time.time() + float(opt("--minutes", 5.0)) * 60
    for attempt in range(tries + 1):
        code = subprocess.run([sys.executable, "-u", str(Path(__file__).resolve())] + argv, cwd=HERE).returncode
        if code == 0 or not (run / "latest.pt").exists() or time.time() > end - 120 or attempt == tries:
            return code
        with open(run / "crash.log", "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} exit code {code}: resuming from latest.pt ({attempt + 1}/{tries})\n")
        print(f"supervise: exit code {code}, resuming {run.name} from latest.pt ({attempt + 1}/{tries})", flush=True)
        cooldown()
        put("--resume", str(run / "latest.pt"))
        put("--minutes", f"{(end - time.time()) / 60:.1f}")
    return code


if __name__ == "__main__":
    if "--supervise" in sys.argv and int(sys.argv[sys.argv.index("--supervise") + 1]) > 0:
        sys.exit(supervise())
    try:
        main()
    except (Exception, KeyboardInterrupt):   # housekeeping's "failed run" marker (AGENTS.md)
        import traceback
        run = next((sys.argv[i + 1] for i, x in enumerate(sys.argv) if x == "--run"), None)
        if run and (RUNS / run).is_dir():
            (RUNS / run / "crash.log").write_text(traceback.format_exc())
        raise
