# 30-Minute AI Realism Training Session — 3-Stage Progress Report

> **Date:** `2026-10-25`  
> **Queue:** `training/runs/realism30m_queue.json` ([HTML Report](../../training/runs/realism30m_queue_report.html))  
> **Total Steps Trained:** `45,613,056` (`45.6M` agent steps across 5 stages, `0` NaNs, `0` crashes)  
> **Exported Brains:** [grandpa_final_vs_kim.onnx](../../training/models/brains/grandpa_final_vs_kim.onnx), [kim_final_vs_grandpa.onnx](../../training/models/brains/kim_final_vs_grandpa.onnx)

---

## 1. Session Stage Ladder (`S1 -> S2 -> S3 -> L4`)

| # | Run Directory | Athlete | Stage / Milestone | Duration | Agent Steps | Peak / Best Exam | NaNs |
|---|---|---|---|---|---|---|---|
| **1** | `grandpa_S1_night1_1917` | `grandpa` | **Stage 1 (`S1`)**: Stand & 150–300 N Shove Recovery | `3.9 min` | `0.79M` | `100%` upright in training | `False` |
| **2** | `grandpa_S2_night2_1921` | `grandpa` | **Stage 2 (`S2`)**: Steady Natural Walk (`1.0–1.4 m/s`) | `4.2 min` | `9.04M` | **`93.0%`** (`0.930` over 100 worlds) | `False` |
| **3** | `grandpa_S3_night3_1926` | `grandpa` | **Stage 3 (`S3`)**: Jog (`2.0–3.2 m/s`), Yaw Turn, Stop & $\pm 8^\circ$ Slopes | `4.0 min` | `13.96M` | `+59.9` reward gain | `False` |
| **4** | `grandpa_L4_vs_kim_night4_1930` | `grandpa` vs `kim` | **Stage 4 (`L4`)**: Mixed-Roster Combat Arena (Grandpa) | `10.1 min` | `10.62M` | `0.360` (`85.5%` absorb, `78.2%` hit) | `False` |
| **5** | `kim_L4_vs_grandpa_night5_1941` | `kim` vs `grandpa` | **Stage 5 (`L4`)**: Mixed-Roster Combat Arena (Kim) | `8.9 min` | `11.21M` | **`0.731`** (`91.8%` absorb, `80.8%` hit) | `False` |

---

## 2. Three-Stage Skill Comparison (AGENTS.md Rule 2)

Per `AGENTS.md` Section 2 (*Tracking Progress*), the table below compares **Beginner (first 10% of training steps)**, **Intermediate (45–55% of training steps)**, and **Advanced (final 10% of training steps)** on **Mean Reward** and **Success Rate**:

### A. Session-Wide 3-Stage Summary (Across All 45.6M Steps)

| Role / Metric | Beginner (0–10%) Mean Reward | Beginner (0–10%) Success Rate | Intermediate (45–55%) Mean Reward | Intermediate (45–55%) Success Rate | Advanced (90–100%) Mean Reward | Advanced (90–100%) Success Rate | Net Reward Gain |
|---|---|---|---|---|---|---|---|
| **Strike** (*clean palm hits*) | `-24.2` | `18.2%` | `+17.0` | `25.4%` | **`+164.4`** | **`79.6%`** | **`+188.6`** (`+61.4%` hit rate) |
| **Absorb** (*slaps absorbed upright*) | `+261.0` | `76.9%` | `+351.7` | `65.9%` | **`+1,365.0`** | **`91.9%`** | **`+1,104.0`** (`+15.0%` absorb rate) |
| **Locomotion (`S2` Walk)** (*kept feet & gait*) | `+79.1` | `60.0%` | `+1,333.1` | `65.3%` | **`+2,015.3`** | **`93.0%` (exam)** | **`+1,936.2`** |

### B. Per-Stage 3-Stage Breakdown

| Stage Run | Role | Beginner (0–10%) Reward / Success | Intermediate (45–55%) Reward / Success | Advanced (90–100%) Reward / Success | Stage Gain |
|---|---|---|---|---|---|
| `grandpa_S1_night1_1917` (`S1` Shove Recovery) | `guard` | `0.0` / `100.0%` | `0.0` / `100.0%` | `0.0` / `100.0%` | `+0.0` (`100%` upright) |
| `grandpa_S2_night2_1921` (`S2` Steady Walk) | `guard` | `79.1` / `60.0%` | `1,333.1` / `65.3%` | `2,015.3` / `93.0%` (exam) | **`+1,936.2`** |
| `grandpa_S3_night3_1926` (`S3` Jog/Turn/Slope) | `guard` | `109.5` / `12.5%` | `142.6` / `—` | `169.5` / `—` | **`+59.9`** |
| `grandpa_L4_vs_kim_night4_1930` (`L4` Grandpa) | `strike` | `-96.5` / `0.0%` | `154.3` / `78.2%` | `140.7` / `72.7%` | **`+237.3`** |
| `grandpa_L4_vs_kim_night4_1930` (`L4` Grandpa) | `absorb` | `186.7` / `68.5%` | `529.8` / `84.3%` | `572.4` / `85.5%` | **`+385.7`** |
| `kim_L4_vs_grandpa_night5_1941` (`L4` Kim) | `strike` | `-49.3` / `0.0%` | `162.5` / `80.8%` | `171.7` / `80.5%` | **`+221.0`** |
| `kim_L4_vs_grandpa_night5_1941` (`L4` Kim) | `absorb` | `187.9` / `54.8%` | `1,360.2` / `91.8%` | `1,319.3` / `90.2%` | **`+1,131.4`** |

---

## 3. Post-Training Match & 100-Round Reliability Verification

- **Post-Training Match (`match.py play`):**
  - Round 1 Turn 1 (`red` Grandpa strikes): `touch=3.945 s`, `part='hand_r'`, `palm=True`, `speed=10.84 m/s`, `fouls=[]`, `end='recovered'`.
  - Round 1 Turn 2 (`blue` Kim strikes): `touch=4.335 s`, `part='hand_r'`, `palm=True`, `speed=16.39 m/s`, `fouls=[]`, `ko=True`, `end='KO'`.
  - Notice that pre-session `out of the box` footwork fouls on Grandpa's strike were **eliminated (`fouls=[]`)** and both athletes landed clean `hand_r` palm strikes (`palm=True`).
- **Gameplay Telemetry Logged:** Saved to `Logs/Gameplay/261025/match_195231_0_20100.jsonl`.

