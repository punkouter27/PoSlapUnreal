"""Unattended training queue: ladder stages back to back inside a time budget.

    .venv/Scripts/python.exe overnight.py --hours 10
    .venv/Scripts/python.exe overnight.py --hours 16 --char kim --queue kim_queue.json   (a second athlete's own queue)

Stages come from runs/overnight_queue.json, re-read before every stage (a stage can be appended while one runs):
    [{"level": "L2", "minutes": 360, "keep_going": false, "resume": "<optional .pt>"}, ...]
Each stage resumes from the previous stage's brain (its best.pt, else latest.pt) unless it names one. A mixed pairing
alternates two athletes in one queue: {"level": "L4", "char": "kim", "vs": "grandpa", ...} trains Kim against Grandpa's
latest brain in this queue (else models/brains/grandpa_final.pt), frozen. A stage that
crashes is retried (up to 5 times) from its own latest.pt, each time after the GPU has cooled under 75 C (gpuwatch.py:
every CUDA 719 of 2026-10-30 hit a card running at its thermal limit). A stage that ends without graduating stops the queue (the ladder;
"no_gate": true on a stage lets the queue go on after it). Milestones go to runs/overnight.jsonl; every stage keeps train.py's
TensorBoard, telemetry, checkpoints, videos and report. At the end: models/brains/<char>_final.pt + .onnx per athlete
(<char>_final_vs_<opponent> from a mixed pairing, so the self-play finals stay) and
runs/overnight_summary.html.

Restarts (2026-11-04: the v3 queue died at 12:29 when Windows logged the user off, exit 0x40010004 / 0xC000026B):
the queue file is the state. Every stage records the run it started ("run", "started") before it trains, so a new
driver on the same queue rebuilds each athlete's latest brain from the stages already done, and with --resume
carries a started-but-unfinished stage on from its runs/<run>/latest.pt for the minutes it has left (summed from the
run's telemetry). A lock file (runs/<queue>.lock) keeps a second driver off a queue that one is already running.
    .venv/Scripts/python.exe overnight.py --resume --hours 16 --queue retrain_v4_queue.json
Unattended restarts after a logoff or reboot: training/queue_task.ps1 registers it as a Task Scheduler task.
Other policies in the same queue (2026-11-04): a stage whose level is not a match level (G1 get-up, S2-S5 walking)
never becomes the athlete's carried or final match brain ("carry": false is its default); "resume": "@stage:<n>" starts
a stage from the brain of the queue's stage n (1-based), e.g. the slope stage from the podium-course stage.
"""
import argparse, json, os, subprocess, sys, time
from pathlib import Path


from gpuwatch import cooldown, gpu_status
from levels import LEVELS
from ppo import PPO, PPOConfig, export_onnx, obs_dim_of

HERE = Path(__file__).parent
RUNS = HERE / "runs"
QUEUE, LOG = RUNS / "overnight_queue.json", RUNS / "overnight.jsonl"


def log(**k):
    k = {"t": time.strftime("%Y-%m-%d %H:%M:%S"), **k}
    with LOG.open("a") as f:
        f.write(json.dumps(k) + "\n")
    print(json.dumps(k), flush=True)


def brain(run: Path):
    for name in ("best.pt", "latest.pt"):
        if (run / name).exists():
            return run / name
    return None


def train(stage, run, minutes, resume, char):
    cmd = [sys.executable, "-u", "train.py", "--char", char, "--level", stage["level"], "--minutes", f"{minutes:.1f}",
           "--envs", str(stage.get("envs", 8192)), "--run", run.name]
    if resume:
        cmd += ["--resume", str(resume)]
    cmd += stage.get("args", [])
    if stage.get("keep_going"):
        cmd.append("--keep-going")
    with open(RUNS / f"{run.name}.log", "a") as out:
        code = subprocess.run(cmd, cwd=HERE, stdout=out, stderr=subprocess.STDOUT).returncode
    if code and run.is_dir():   # housekeeping's "failed run" marker (AGENTS.md); a native crash leaves no traceback
        with open(run / "crash.log", "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} exit code {code}\n")
    return code


def trained_minutes(run: Path) -> float:
    """Minutes a run has trained, over every process that wrote to it (each restart's telemetry counts from 0 again)."""
    tele = run / "telemetry.jsonl"
    if not tele.exists():
        return 0.0
    total, last = 0.0, 0.0
    for line in tele.read_text().splitlines():
        try:
            m = float(json.loads(line).get("min", 0.0))
        except ValueError:
            continue
        if m < last:   # a new process started on this run
            total += last
        last = m
    return total + last


def alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    except FileNotFoundError:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def carries(stage):
    """A match-ladder stage hands its brain on (and makes the final); a get-up or walking stage trains another policy."""
    return stage.get("carry", LEVELS.get(stage["level"], {}).get("model") not in ("getup", "locomotion"))


def resume_of(stage, queue, carry, char):
    r = stage.get("resume")
    if r and r.startswith("@stage:"):
        src = queue[int(r.split(":")[1]) - 1]
        return brain(RUNS / src["run"]) if src.get("run") else None
    return r or carry.get(char)


def physics_suffix(stage):
    args = stage.get("args", [])
    return f"_v{args[args.index('--physics') + 1]}" if "--physics" in args and args[args.index("--physics") + 1] != "1" else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=10.0)
    ap.add_argument("--char", default="grandpa")
    ap.add_argument("--queue", default=None, help="queue file in runs/ (default overnight_queue.json); its log and summary are named after it")
    ap.add_argument("--resume", action="store_true", help="carry a started-but-unfinished stage on from its latest.pt (after a logoff/reboot)")
    a = ap.parse_args()
    global QUEUE, LOG
    summary = RUNS / "overnight_summary.html"
    if a.queue:
        QUEUE = RUNS / a.queue
        LOG, summary = QUEUE.with_suffix(".jsonl"), QUEUE.with_suffix(".html")
    lock = QUEUE.with_suffix(".lock")
    if lock.exists():
        try:
            pid = int(json.loads(lock.read_text()).get("pid", 0))
        except ValueError:
            pid = 0
        if alive(pid) and pid != os.getpid():
            print(f"{QUEUE.name} is already being run by PID {pid} ({lock.name}); not starting a second driver")
            return
    lock.write_text(json.dumps({"pid": os.getpid(), "started": time.strftime("%Y-%m-%d %H:%M:%S")}))
    try:
        run_queue(a, summary)
    finally:
        lock.unlink(missing_ok=True)


def run_queue(a, summary):
    deadline = time.time() + a.hours * 3600
    log(event="start", hours=a.hours, resume=a.resume)
    carry, done, vs_of = {}, [], {}   # per athlete: its latest brain, the opponent of its last stage
    for s in json.loads(QUEUE.read_text()):   # a restarted driver: each athlete's latest brain from the stages already done
        if s.get("done") and s.get("run") and (RUNS / s["run"]).is_dir() and carries(s):
            c = s.get("char", a.char)
            carry[c] = brain(RUNS / s["run"]) or carry.get(c)
            vs_of[c] = (f"_vs_{s['vs']}" if s.get("vs") else "") + physics_suffix(s)
    if carry:
        log(event="carry", brains={c: str(p) for c, p in carry.items()})
    while time.time() < deadline - 120:
        queue = json.loads(QUEUE.read_text())
        todo = [s for s in queue if not s.get("done")]
        if not todo:
            break
        stage = todo[0]
        idx = queue.index(stage)
        char, vs = stage.get("char", a.char), stage.get("vs")
        keeps = carries(stage)
        if keeps:
            vs_of[char] = (f"_vs_{vs}" if vs else "") + physics_suffix(stage)   # a physics-2/3 final never replaces a shipped one
        prior = RUNS / stage["run"] if stage.get("run") else None
        cont = a.resume and prior is not None and (prior / "latest.pt").exists()
        left = stage["minutes"] - (trained_minutes(prior) if cont else 0.0)
        if cont and left < 3:   # it had all but finished: close it as it is
            cont, left = False, 0.0
        minutes = min(max(left, 0.0), (deadline - time.time()) / 60 - 1)
        if cont:
            run, resume = prior, prior / "latest.pt"
            log(event="resume_stage", run=run.name, minutes_left=round(minutes, 1))
        else:
            tag = (f"_vs_{vs}" if vs else "") + physics_suffix(stage)
            run = RUNS / f"{char}_{stage['level']}{tag}_night{idx + 1}_{time.strftime('%H%M')}"
            resume = resume_of(stage, queue, carry, char)
        stage.update(run=run.name, started=time.strftime("%Y-%m-%d %H:%M:%S"))   # the restart point, before it trains
        QUEUE.write_text(json.dumps(queue, indent=1))
        if vs:
            opp = carry.get(vs) or stage.get("opponent") or HERE / "models" / "brains" / f"{vs}_final.pt"   # "opponent": seed the first spar
            if not carry.get(vs) and not stage.get("opponent") and "_v" in vs_of[char]:
                log(event="warning", note=f"{vs} has no brain on this physics in the queue: sparring the shipped {opp.name}")
            stage = {**stage, "args": stage.get("args", []) + ["--opponent", f"{vs}:{opp}"]}
        log(event="stage", level=stage["level"], run=run.name, minutes=round(minutes, 1), resume=str(resume))
        end = time.time() + minutes * 60
        code = train(stage, run, minutes, resume, char) if minutes >= 1 else 0
        for _ in range(5):   # retries from where it got to (a crash, or a trainer restarted to pick up a fix)
            if code == 0 or not (run / "latest.pt").exists() or time.time() > end - 300:
                break
            log(event="crash", run=run.name, code=code, gpu=gpu_status())
            cooldown(log=lambda s: log(event="cooldown", note=s))
            code = train(stage, run, (end - time.time()) / 60, run / "latest.pt", char)
        meta = json.loads((run / "meta.json").read_text()) if (run / "meta.json").exists() else {}
        if keeps:
            carry[char] = brain(run) or carry.get(char)
        queue = json.loads(QUEUE.read_text())   # re-read: stages may have been appended meanwhile
        stage = queue[idx]   # (the copy above carries the --opponent; the file keeps the stage)
        stage.update(done=True, run=run.name, code=code, graduated=meta.get("graduated"), best_exam=meta.get("best_exam"))
        QUEUE.write_text(json.dumps(queue, indent=1))
        done.append(stage)
        log(event="milestone", level=stage["level"], run=run.name, code=code, graduated=meta.get("graduated"),
            best_exam=meta.get("best_exam"), episode_return=meta.get("episode_return"), steps=meta.get("steps"))
        if not meta.get("graduated") and not stage.get("no_gate"):   # the ladder: no level before the last one graduates
            log(event="stopped", level=stage["level"], run=run.name, reason="not graduated; later stages wait")
            break

    for char, c in carry.items():   # final weights per athlete: its last stage's best brain
        if c.name != "best.pt":   # a stage that never sat an exam must not replace a tested final brain
            log(event="final_skipped", from_=str(c), reason="no exam taken (no best.pt)")   # (2026-10-23: 25 iterations
            continue                                                                         # overwrote the 17:05 L3 brain)
        n = obs_dim_of(str(c))   # its own width: an interface-3 brain must not be widened into the low-pass rule
        ppo = PPO(n, 30, 8, "cuda", PPOConfig())
        extra = ppo.load(str(c))
        final = HERE / "models" / "brains" / f"{char}_final{vs_of[char]}"
        ppo.save(str(final.with_suffix(".pt")), extra)   # with its curricula and rate: a resume from it carries on
        export_onnx(ppo, str(final.with_suffix(".onnx")), n, fixed_batch=True,
                    meta={k: extra[k] for k in ("interface", "physics", "level") if k in extra})
        log(event="final", from_=str(c), pt=str(final.with_suffix(".pt")), onnx=str(final.with_suffix(".onnx")))

    rows = "".join(f"<tr><td>{s['level']}</td><td>{s.get('run')}</td><td>{s.get('graduated') or 'no'}</td>"
                   f"<td>{s.get('best_exam')}</td><td><a href='{s.get('run')}/report.html'>report</a></td></tr>" for s in done)
    summary.write_text(
        f"<!doctype html><meta charset='utf-8'><title>Overnight training</title><style>body{{font:15px system-ui;margin:24px}}"
        f"td,th{{padding:4px 10px;border-bottom:1px solid #ccc}}</style><h1>Overnight training</h1><table><tr><th>Level</th>"
        f"<th>Run</th><th>Graduated</th><th>Best exam</th><th></th></tr>{rows}</table>", encoding="utf-8")
    log(event="end")


if __name__ == "__main__":
    main()
