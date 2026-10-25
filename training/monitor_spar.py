"""Monitor the unattended spar queue: milestones, reward/loss curves, checkpoints."""
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
RUNS = HERE / "runs"
QUEUE = RUNS / "spar3h2_queue.json"
LOG = RUNS / "spar3h2_queue.jsonl"


def read_jsonl(p, limit=None):
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out[-limit:] if limit else out


def main():
    print("=" * 78)
    print("QUEUE STATE  %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 78)
    if LOG.exists():
        for e in read_jsonl(LOG):
            if e.get("event") in ("start", "stage", "milestone", "crash",
                                  "stopped", "final", "final_skipped", "end"):
                print("  %s  %s" % (e.get("t", "")[11:], json.dumps(
                    {k: v for k, v in e.items() if k not in ("t", "event")})))

    print("\n" + "=" * 78)
    print("RUNS")
    print("=" * 78)
    q = json.loads(QUEUE.read_text()) if QUEUE.exists() else []
    for stage in q:
        run = stage.get("run")
        if not run:
            print("  %-42s PENDING (%s)" % (stage.get("char", "?") + " " +
                                            stage.get("level", "?"), stage.get("note", "")[:40]))
            continue
        d = RUNS / run
        tele = read_jsonl(d / "telemetry.jsonl")
        meta_p = d / "meta.json"
        meta = json.loads(meta_p.read_text()) if meta_p.exists() else {}
        ckpts = sorted(p.name for p in d.glob("*.pt"))
        print("  %-42s it=%-5s steps=%-11s best_exam=%-8s ckpt=%s"
              % (run, meta.get("iterations", len(tele)), meta.get("steps", "?"),
                 meta.get("best_exam", "?"), ",".join(ckpts) or "-"))
        if tele:
            f, l = tele[0], tele[-1]
            print("      reward  strike %7.1f -> %7.1f   absorb %7.1f -> %7.1f"
                  % (f.get("strike_return", 0), l.get("strike_return", 0),
                     f.get("absorb_return", 0), l.get("absorb_return", 0)))
            print("      loss    policy %8.4f -> %8.4f  value %8.2f -> %8.2f  ent %5.3f -> %5.3f"
                  % (f.get("policy_loss", 0), l.get("policy_loss", 0),
                     f.get("value_loss", 0), l.get("value_loss", 0),
                     f.get("entropy", 0), l.get("entropy", 0)))
            print("      hit %.2f -> %.2f   absorb_ok %.2f -> %.2f   ko %.2f"
                  % (f.get("strike_hit", 0), l.get("strike_hit", 0),
                     f.get("absorb_ok", 0), l.get("absorb_ok", 0),
                     l.get("strike_ko", 0)))
        if meta.get("graduated"):
            print("      GRADUATED: %s" % json.dumps(meta["graduated"]))

    print("\n" + "=" * 78)
    print("FINAL WEIGHTS (models/brains)")
    print("=" * 78)
    for p in sorted((HERE / "models" / "brains").glob("*final*")):
        print("  %-34s %8.1f KB  %s" % (p.name, p.stat().st_size / 1024,
                                        time.strftime("%m-%d %H:%M",
                                                      time.localtime(p.stat().st_mtime))))

    print("\n" + "=" * 78)
    print("ACTIVE")
    print("=" * 78)
    import subprocess
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance Win32_Process -Filter \"Name like '%python%'\" | "
             "Where-Object { $_.CommandLine -like '*train.py*' } | "
             "ForEach-Object { $_.CommandLine }) -join \"`n\""],
            capture_output=True, text=True, timeout=60).stdout
        print("  " + (out.strip() or "(no train.py running)"))
    except Exception as e:
        print("  ? %s" % e)


if __name__ == "__main__":
    main()
