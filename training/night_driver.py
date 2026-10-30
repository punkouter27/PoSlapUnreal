"""Unattended multi-athlete training loop with match-exam gates (2026-10-30, the 10 h night queue).

    .venv/Scripts/python.exe night_driver.py --hours 10 --queue night1030_queue.json

Like overnight.py (whose train() and brain() it reuses) the stages come from runs/<queue>.json, re-read before every
stage, each one a train.py run on the GPU.  What this driver adds:

  * a MATCH gate for mixed pairings ("vs"): after the stage, its best.pt is exported to models/brains/<char>_cand_vs_<vs>.onnx
    and played through `matches` jittered full matches against the opponent's current brain in match.py (the referee the
    game uses, with the eye-contact servo), on the CPU in a side thread while the next stage trains.  The candidate passes
    when no match blows up, the athlete lands >= `min_land` of its strikes, self-falls on <= `max_self_fall` of them and
    its KO rate per landed strike sits in the L4 band.  2026-10-29: train.py's own arena exam read 97 % strikes while
    match.py landed 5 %, so the arena exam alone is not the gate any more;
  * the opponent of a later stage is the latest candidate that PASSED its match gate (else the shipped final), so each
    athlete keeps meeting the best version of the other;
  * a failed gate inserts one retry round (`retry_minutes`) for the same pairing right after the stage, ahead of the rest
    of the queue (the urgent policy first); each stage retries at most once;
  * the ladder for a single athlete: a "needs" stage (e.g. Matt L3 needs L2) is skipped when the earlier stage did not
    graduate;
  * at the end the best passing candidate per pairing becomes models/brains/<char>_final_vs_<vs>.pt/.onnx (the previous
    files are kept in runs/<queue>_backup/), and runs/<queue>_results.json carries every exam for the report
    (night_report.py).
"""
import argparse, json, os, shutil, sys, threading, time
from multiprocessing import Pool
from pathlib import Path

HERE = Path(__file__).parent
RUNS = HERE / "runs"
BRAINS = HERE / "models" / "brains"
KO_BAND = (0.05, 0.50)        # per landed strike: wider than the arena exam's 0.10-0.40 (40 matches are a small sample)


def log(path, **k):
    k = {"t": time.strftime("%Y-%m-%d %H:%M:%S"), **k}
    with path.open("a") as f:
        f.write(json.dumps(k) + "\n")
    print(json.dumps(k), flush=True)


def export_candidate(pt, onnx):
    import torch
    from brain_cpu import OBS
    from ppo import PPO, PPOConfig, export_onnx
    ppo = PPO(OBS, 30, 8, "cuda", PPOConfig())
    extra = ppo.load(str(pt), widen=True)
    export_onnx(ppo, str(onnx), OBS, fixed_batch=True)
    pt_out = Path(onnx).with_suffix(".pt")
    ppo.save(str(pt_out), extra)
    del ppo
    torch.cuda.empty_cache()
    return pt_out


def match_exam(red_onnx, red_char, blue_onnx, blue_char, matches=40, procs=12):
    """Jittered full matches (match.py, seed = match index): per-athlete strikes, landed, self-falls, KOs; blow-ups."""
    from match import EXAM_JITTER, _exam_one
    args = [(str(red_onnx), i, None, red_char, blue_char, EXAM_JITTER, str(blue_onnx)) for i in range(matches)]
    with Pool(min(procs, matches)) as p:
        out = p.map(_exam_one, args)
    names = (Path(red_onnx).stem, Path(blue_onnx).stem)
    res = {"matches": matches, "blowups": sum(r["how"] == "blow-up" for r, _, _ in out),
           "decisions": {}, "sides": {}}
    for r, _, _ in out:
        res["decisions"][r["how"]] = res["decisions"].get(r["how"], 0) + 1
    for corner, name, char in zip(("red", "blue"), names, (red_char, blue_char)):   # a turn's striker is its corner
        ts = [t for _, turns, _ in out for t in turns if t["striker"] == corner]
        landed = [t for t in ts if t["touch"] is not None]
        side = {"char": char, "brain": name, "turns": len(ts), "landed": len(landed),
                "land_rate": len(landed) / max(1, len(ts)),
                "self_fall": sum(any(w == "knocked down" for _, w in t["fouls"]) for t in ts) / max(1, len(ts)),
                "ko_per_landed": sum(t["ko"] for t in landed) / max(1, len(landed)),
                "speed": float(sum(t["speed"] for t in landed) / max(1, len(landed))),
                "wins": sum(r["winner"] in (name, corner) for r, _, _ in out)}
        res["sides"][char if names[0] != names[1] else f"{char}_{name}"] = side
    return res


def gate(res, char, min_land=0.85, max_self_fall=0.06):
    s = res["sides"].get(char)
    if s is None or res["blowups"]:
        return False, "blow-up" if res["blowups"] else "no side"
    why = []
    if s["land_rate"] < min_land:
        why.append(f"lands {s['land_rate']:.0%} < {min_land:.0%}")
    if s["self_fall"] > max_self_fall:
        why.append(f"self-falls {s['self_fall']:.0%} > {max_self_fall:.0%}")
    if not KO_BAND[0] <= s["ko_per_landed"] <= KO_BAND[1]:
        why.append(f"KO {s['ko_per_landed']:.0%} outside {KO_BAND}")
    return not why, "; ".join(why) or "pass"


def score(res, char):
    s = res["sides"].get(char)
    if s is None:
        return -1.0
    ko_ok = KO_BAND[0] <= s["ko_per_landed"] <= KO_BAND[1]
    return s["land_rate"] * (1 - s["self_fall"]) * (1.0 if ko_ok else 0.5) * (0.0 if res["blowups"] else 1.0)


def main():
    import overnight   # train(), brain(): the same subprocess launch as the overnight queue
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=10.0)
    ap.add_argument("--queue", default="night1030_queue.json")
    ap.add_argument("--matches", type=int, default=40)
    ap.add_argument("--procs", type=int, default=12)
    a = ap.parse_args()
    queue_p = RUNS / a.queue
    logp, results_p = queue_p.with_suffix(".jsonl"), queue_p.with_name(queue_p.stem + "_results.json")
    backup = RUNS / (queue_p.stem + "_backup")
    overnight.RUNS = RUNS
    t_start = time.time()
    deadline = t_start + a.hours * 3600
    results = json.loads(results_p.read_text()) if results_p.exists() else {"exams": [], "finals": []}
    save_results = lambda: results_p.write_text(json.dumps(results, indent=1))
    log(logp, event="start", hours=a.hours, queue=a.queue)

    # the brains in play: per athlete, the (pt, onnx) of its best match-gated candidate per opponent; else the shipped final
    best_cand = {}      # (char, vs) -> {"pt", "onnx", "score", "res", "run"}
    carry = {}          # char -> latest best.pt / latest.pt of its last stage (the resume for the next one)
    graduated = {}      # (char, level) -> bool
    pending = {}        # (char, vs) -> thread of a running match exam
    qlock = threading.Lock()   # the exam thread and the stage loop both rewrite the queue file

    def edit_queue(fn):
        with qlock:
            q = json.loads(queue_p.read_text())
            fn(q)
            queue_p.write_text(json.dumps(q, indent=1))

    def opp_brain(vs, learner):
        """The frozen opponent for `learner`: vs's best passing candidate against the learner, else its shipped final."""
        c = best_cand.get((vs, learner))
        if c and c["passed"]:
            return c["pt"], c["onnx"]
        pt = BRAINS / f"{vs}_final_vs_{learner}.pt"
        if not pt.exists():
            pt = BRAINS / f"{vs}_final.pt"
        return pt, pt.with_suffix(".onnx")

    def run_exam(stage, run):
        char, vs = stage["char"], stage["vs"]
        pt = overnight.brain(run)
        if pt is None:
            log(logp, event="exam_skipped", run=run.name, reason="no checkpoint")
            return
        cand = BRAINS / f"{char}_cand_vs_{vs}.onnx"
        cand_pt = export_candidate(pt, cand)
        _, opp_onnx = opp_brain(vs, char)
        pair = (char, vs) if (HERE / "models" / f"{char}_vs_{vs}_arena.xml").exists() else (vs, char)
        red, blue = ((cand, opp_onnx) if pair[0] == char else (opp_onnx, cand))
        t0 = time.time()
        try:
            res = match_exam(red, pair[0], blue, pair[1], a.matches, a.procs)
        except Exception as e:   # the exam must never take the queue down
            log(logp, event="exam_error", run=run.name, error=repr(e))
            return
        passed, why = gate(res, char)
        sc = score(res, char)
        entry = {"run": run.name, "char": char, "vs": vs, "candidate": str(cand), "opponent": str(opp_onnx),
                 "passed": passed, "why": why, "score": sc, "minutes": round((time.time() - t0) / 60, 1), **res}
        results["exams"].append(entry)
        save_results()
        log(logp, event="match_exam", run=run.name, char=char, vs=vs, passed=passed, why=why, score=round(sc, 3),
            side=res["sides"].get(char), blowups=res["blowups"], exam_min=entry["minutes"])
        prev = best_cand.get((char, vs))
        if prev is None or sc > prev["score"]:
            keep = BRAINS / f"{char}_cand_vs_{vs}_{run.name}.onnx"   # every candidate kept by run (the final picks one)
            shutil.copy(cand, keep); shutil.copy(cand_pt, keep.with_suffix(".pt"))
            best_cand[(char, vs)] = {"pt": keep.with_suffix(".pt"), "onnx": keep, "score": sc, "res": res,
                                     "run": run.name, "passed": passed}
        # the retry: one more round for the same pairing, ahead of everything else
        def edit(q):
            me = next((s for s in q if s.get("run") == run.name), None)
            if me is None:
                return
            if not passed and not me.get("retried") and me.get("retry_minutes", 0) > 0:
                me["retried"] = True
                retry = {k: v for k, v in me.items() if k not in ("done", "run", "code", "graduated", "best_exam", "retried",
                                                                  "match_exam", "resume", "steps", "train_minutes", "sid")}
                retry.update(minutes=me["retry_minutes"], retry_minutes=0, note=f"retry of {run.name}: {why}")
                q.insert(q.index(me) + 1, retry)
                log(logp, event="retry_queued", run=run.name, minutes=me["retry_minutes"], why=why)
            me["match_exam"] = {"passed": passed, "why": why, "score": round(sc, 3), "side": res["sides"].get(char)}
        edit_queue(edit)

    n = 0
    while time.time() < deadline - 180:
        with qlock:
            queue = json.loads(queue_p.read_text())
        todo = [s for s in queue if not s.get("done") and not s.get("sid")]   # a sid marks the stage being run
        if not todo:
            break
        stage = todo[0]
        char, vs, level = stage["char"], stage.get("vs"), stage["level"]
        # the ladder: a stage that needs an earlier graduation is skipped when that did not happen
        need = stage.get("needs")
        if need and not graduated.get((char, need)):
            reason = f"{char} {need} did not graduate"
            edit_queue(lambda q: next(s for s in q if not s.get("done") and not s.get("sid")).update(done=True, skipped=reason))
            log(logp, event="skipped", char=char, level=level, reason=reason)
            continue
        # a mixed stage waits for the opponent's pending match exam (its result decides which brain to train against)
        if vs and (vs, char) in pending:
            log(logp, event="waiting_exam", char=char, vs=vs)
            pending.pop((vs, char)).join()
        if (char, vs) in pending:   # ... and for its own previous round's exam (the retry decision)
            pending.pop((char, vs)).join()
            continue   # re-read the queue: a retry may have been inserted
        minutes = min(stage["minutes"], (deadline - time.time()) / 60 - 2)
        if minutes < 8:
            log(logp, event="out_of_time", char=char, level=level, minutes=round(minutes, 1))
            break
        n += 1
        run = RUNS / f"{char}_{level}{'_vs_' + vs if vs else ''}_n{n}_{time.strftime('%H%M')}"
        sid = run.name   # the stage's identity in the file: a retry inserted ahead of it must not take its result
        edit_queue(lambda q: next(s for s in q if not s.get("done") and not s.get("sid")).update(sid=sid))
        resume = stage.get("resume") or carry.get(char)
        if vs:
            opp_pt, _ = opp_brain(vs, char)
            stage = {**stage, "args": stage.get("args", []) + ["--opponent", f"{vs}:{opp_pt}"]}
        log(logp, event="stage", n=n, char=char, level=level, vs=vs, run=run.name, minutes=round(minutes, 1),
            resume=str(resume), opponent=str(opp_pt) if vs else None)
        end = time.time() + minutes * 60
        code = overnight.train(stage, run, minutes, resume, char)
        for attempt in range(4):   # a crash resumes from where it got to; a crash before the first checkpoint (a file
            if code == 0 or time.time() > end - 300:   # saved mid-import by another agent, 23:52) retries from the start
                break
            log(logp, event="crash", run=run.name, code=code, attempt=attempt + 1)
            time.sleep(45)
            latest = run / "latest.pt"
            code = overnight.train(stage, run, (end - time.time()) / 60, latest if latest.exists() else resume, char)
        meta = json.loads((run / "meta.json").read_text()) if (run / "meta.json").exists() else {}
        carry[char] = overnight.brain(run) or carry.get(char)
        graduated[(char, level)] = bool(meta.get("graduated"))
        done_fields = dict(done=True, run=run.name, code=code, graduated=meta.get("graduated"), best_exam=meta.get("best_exam"),
                           steps=meta.get("steps"), train_minutes=meta.get("minutes"))
        edit_queue(lambda q: next(s for s in q if s.get("sid") == sid).update(done_fields))
        me = {**stage, **done_fields}
        log(logp, event="milestone", char=char, level=level, vs=vs, run=run.name, code=code, graduated=meta.get("graduated"),
            best_exam=meta.get("best_exam"), steps=meta.get("steps"))
        if vs and stage.get("gate", "match") == "match":
            th = threading.Thread(target=run_exam, args=(dict(me), run), daemon=False)
            th.start()
            pending[(char, vs)] = th

    for th in list(pending.values()):
        th.join()

    # finals: the best passing candidate per pairing replaces the shipped final (previous files backed up)
    backup.mkdir(exist_ok=True)
    for (char, vs), c in best_cand.items():
        final = BRAINS / f"{char}_final_vs_{vs}"
        if not c["passed"]:
            log(logp, event="final_kept", char=char, vs=vs, reason=f"no candidate passed the match gate (best {c['score']:.3f} from {c['run']})")
            results["finals"].append({"char": char, "vs": vs, "promoted": False, "best_run": c["run"], "score": c["score"]})
            continue
        for ext in (".pt", ".onnx"):
            if final.with_suffix(ext).exists():
                shutil.copy(final.with_suffix(ext), backup / final.with_suffix(ext).name)
            shutil.copy(c["pt" if ext == ".pt" else "onnx"], final.with_suffix(ext))
        log(logp, event="final", char=char, vs=vs, from_run=c["run"], score=round(c["score"], 3), pt=str(final.with_suffix(".pt")))
        results["finals"].append({"char": char, "vs": vs, "promoted": True, "best_run": c["run"], "score": c["score"]})
    save_results()
    log(logp, event="end", hours=round((time.time() - t_start) / 3600, 2))


if __name__ == "__main__":
    main()
