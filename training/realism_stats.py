"""Realism readout over match logs: how human the athletes move, from match.py's "strike", "head", "realism" and
"turn_end" events (Logs/Gameplay/<YYMMDD>/match_*.jsonl; Unreal's *_ue.jsonl carry no readout).

    .venv/Scripts/python.exe realism_stats.py [YYMMDD ...]      # default: today

The numbers to tune the realism options against (2026-10-26): muscle ramp, reaction delay, force-velocity, shoulder cone.
"""
import json, sys, time
from pathlib import Path

import numpy as np

LOGS = Path(__file__).resolve().parents[1] / "Logs" / "Gameplay"
CONCUSSION_G, CONCUSSION_RAD_S2 = 80.0, 5000.0   # rough thresholds from helmet-sensor studies


def load(days):
    ev = []
    for day in days:
        for f in sorted((LOGS / day).glob("match_*.jsonl")):
            if not f.name.endswith("_ue.jsonl"):
                ev += [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
    return ev


def stats(ev):
    by = lambda t: [e for e in ev if e["type"] == t]
    strikes, heads, rz, ends = by("strike"), by("head"), by("realism"), by("turn_end")
    med = lambda xs: float(np.median(xs)) if xs else float("nan")
    chain = [r["chain_ms"] for r in rz if r.get("chain_ms") is not None]
    out = {
        "turns": len(ends),
        "strikes": len(strikes),
        "hand_speed_median_m_s": med([s["speed"] for s in strikes]),
        "hand_speed_p90_m_s": float(np.percentile([s["speed"] for s in strikes], 90)) if strikes else float("nan"),
        "head_peak_g_median": med([h["peak_g"] for h in heads]),
        "head_peak_rad_s2_median": med([h["peak_rad_s2"] for h in heads]),
        "head_over_concussion_share": float(np.mean([h["peak_g"] > CONCUSSION_G or h["peak_rad_s2"] > CONCUSSION_RAD_S2
                                                     for h in heads])) if heads else float("nan"),
        "chain_trunk_leads_shoulder_share": float(np.mean([c > 0 for c in chain])) if chain else float("nan"),
        "chain_lead_median_ms": med(chain),
        "action_jerk_mean": float(np.mean([r["jerk"] for r in rz])) if rz else float("nan"),
        "foot_slide_median_m": med([r["slide_m"] for r in rz]),
        "stagger_share": float(np.mean([e["stagger"] for e in ends if e.get("touch") is not None] or [np.nan])),
        "ko_share": float(np.mean([e["ko"] for e in ends if e.get("touch") is not None] or [np.nan])),
    }
    return out


def main():
    days = sys.argv[1:] or [time.strftime("%y%m%d")]
    ev = load(days)
    if not any(e["type"] == "realism" for e in ev):
        sys.exit(f"no realism events in {', '.join(days)}: play matches with match.py first")
    for k, v in stats(ev).items():
        print(f"{k:34s} {v:.3f}" if isinstance(v, float) else f"{k:34s} {v}")


if __name__ == "__main__":
    main()
