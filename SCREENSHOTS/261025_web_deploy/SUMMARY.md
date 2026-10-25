# Web version → Azure Static Web Apps (261025)

Visual summary of the UI state around the build-date stamp fix.

| Shot | State |
|---|---|
| `web_local_before_stamp.png` | Local build at http://localhost:8080/Web/, watermark bottom-right showed **261023** (stale). Sim running: round ticker, matchup nav, judges' cards, playback toolbar all normal. |
| `web_local_after_stamp.png` | Same page after stamping **261025** (today, per AGENTS.md). Reload clean, Grandpa vs Kim matchup loading and fighting (hand speeds, head-snap feed, judge cards populating). No other visuals changed. |

Deployment: GitHub Actions (`.github/workflows/deploy-web.yml`) builds `Web/dist` and uploads it to the
`poslap-arena` Static Web App in RG `PoSlapUnreal` on every push to master touching `Web/**`,
`training/models/**` or `training/characters/**`.
