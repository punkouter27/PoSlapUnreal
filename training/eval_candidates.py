"""Pick the final weights by a fair head-to-head, not by the last stage.

The session's exams each used a different opponent, so their scores are not comparable
("0.834 against a weak Kim" vs "0.794 against a strong Kim"). This re-examines every candidate
checkpoint of the session against ONE fixed opponent per athlete (the other athlete's last-stage
brain), with the exam's own fixed seed, so the numbers line up.

    .venv/Scripts/python.exe eval_candidates.py spar3h2_queue
"""
import json
import sys
import time
from pathlib import Path

import torch

import train as T
from brain_cpu import OBS
from ppo import PPO, PPOConfig

HERE = Path(__file__).parent
RUNS = HERE / "runs"
TRIALS = 13         # paired trials per candidate (one exam is +-0.12 on the same checkpoint)


def main():
    name = sys.argv[1] if len(sys.argv) > 1 else "spar3h2_queue"
    queue = json.loads((RUNS / (name + ".json")).read_text())

    cands, finals = {}, {}
    for s in queue:
        run = s.get("run")
        if not run:
            continue
        c = s.get("char")
        p = RUNS / run / "best.pt"
        if p.exists():
            cands.setdefault(c, []).append((run, p))
        finals[c] = p
    opp_for = {"kim": ("grandpa", finals.get("grandpa")),
               "grandpa": ("kim", finals.get("kim"))}

    results = {}
    for char, lst in cands.items():
        opp_char, opp_ck = opp_for[char]
        if not opp_ck or not opp_ck.exists():
            print("no opponent for %s" % char)
            continue
        print("=" * 78)
        print("%s candidates, all vs the same opponent (%s, last stage), %d paired trials"
              % (char, opp_char, TRIALS))
        print("=" * 78)
        env = T.VsFrozen(char, opp_char, str(opp_ck), "L4", num_envs=100, seed=12345)
        T.ppo_stream = env.stream
        for run, path in lst:
            ppo = PPO(OBS, env.A, env.N, "cuda", PPOConfig(init_std=0.25, lr_min=1e-4))
            extra = ppo.load(str(path), widen=True)
            ppo.cfg.lr = float(extra.get("lr", ppo.cfg.lr_min))
            scores, details = [], []
            for trial in range(TRIALS):
                # arena.py seeds the global RNG once, in __init__, and reset() draws from it after
                # that: one exam is a random draw, and GPU physics is not bit-reproducible, so a
                # single trial moves the same checkpoint by +-0.12. Re-seed before every trial and
                # give every candidate the SAME trial seeds -> a paired comparison.
                torch.manual_seed(9000 + trial)
                ok, res = T.arena_exam(ppo, char, "L4", (opp_char, str(opp_ck)))
                scores.append(res["score"])
                details.append(res)
            mean = sum(scores) / len(scores)
            med = sorted(scores)[len(scores) // 2]
            spread = (max(scores) - min(scores)) / 2
            meta_p = RUNS / run / "meta.json"
            meta = json.loads(meta_p.read_text()) if meta_p.exists() else {}
            results[(char, run)] = {"mean": mean, "median": med, "spread": spread,
                                    "scores": scores,
                                    "strikes": sum(d["strikes"] for d in details) / len(details),
                                    "ko_rate": sum(d["ko_rate"] for d in details) / len(details),
                                    "self_fall": sum(d["self_fall"] for d in details) / len(details),
                                    "train_strike_return": meta.get("strike_return"),
                                    "train_absorb_return": meta.get("absorb_return"),
                                    "train_hit": meta.get("strike_hit"),
                                    "train_absorb_ok": meta.get("absorb_ok")}
            print("  %-42s mean %.4f  median %.4f (+-%.4f)  strikes %.2f  ko %.3f  self_fall %.2f"
                  "   [train: strike %.0f, absorb %.0f, hit %.2f, absorb_ok %.2f]"
                  % (run, mean, med, spread, results[(char, run)]["strikes"],
                     results[(char, run)]["ko_rate"], results[(char, run)]["self_fall"],
                     meta.get("strike_return", 0), meta.get("absorb_return", 0),
                     meta.get("strike_hit", 0), meta.get("absorb_ok", 0)))
        del env

    print("\n" + "=" * 78)
    print("WINNER per athlete (paired trials, one fixed opponent)")
    print("=" * 78)
    winners = {}
    for char in cands:
        scored = [(results[(c, r)]["median"], r) for (c, r) in results if c == char]
        if not scored:
            continue
        scored.sort(reverse=True)
        for sc, run in scored:
            mark = "  <-- ship" if run == scored[0][1] else ""
            print("  %-10s %-42s median %.4f%s" % (char, run, sc, mark))
        winners[char] = scored[0][1]
    out = RUNS / (name + "_winner.json")
    out.write_text(json.dumps(winners, indent=1))
    print("\nwritten: %s -> %s" % (out, winners))


if __name__ == "__main__":
    main()
