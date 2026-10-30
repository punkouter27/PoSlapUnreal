# PoSlap — Project Summary

Power Slap spectator sim: two AI athletes take turns slapping at a podium; 3 rounds, KO on any non-sole floor contact, else 10-point-must decision.

- **Physics:** MuJoCo only (via URLab in Unreal). Chaos is off for athletes, podium, floor, props. Unreal only draws.
- **Brain:** trained from scratch in MuJoCo (Warp, GPU), exported to ONNX, run in Unreal via NNE. One brain, role flag (strike/defend).
- **Athletes:** one rigged mesh per character + a character card (height, mass, strength, speed, flexibility). v1: Grandpa for both players.
- **Engine:** UE 5.8, Windows. Unreal MCP on `127.0.0.1:8000/mcp` (editor must be open).

| Doc | What |
|---|---|
| [../BLUEPRINT.md](../BLUEPRINT.md) | Body, coordinates, timing, skill ladder, matching check (approved) |
| [../tasks.md](../tasks.md) | Build checklist, phase status |
| [../AGENTS.md](../AGENTS.md) | Agent working rules |

Layout: `training/` (Python: rig → MJCF → envs → PPO), `Content/Art/Raw` (original .blend), `Content/Art/Exports` (GLB/FBX), `Plugins/UnrealRoboticsLab` (URLab, Phase B).

Broadcast sidecars (run beside the game, `training/.venv/Scripts/python.exe`): `Scripts/commentator.py` (voice + HUD line; Claude when credentials exist),
`Scripts/clipper.py` (KO clips to `Saved/Clips`), `Scripts/stream.ps1` (Pixel Streaming at http://localhost/),
`Scripts/chat.py <twitch channel>|--demo` (chat hype -> HUD + crowd), `Web/live.py` (second-screen relay: MENU › VIEW ›
Second screen, viewers at `Web/index.html?live=ws://<host>:8765`; check `node Web/live_check.mjs`), `training/scouting.py`
(Elo + scouting -> `training/models/league.json`). Brains per corner:
`-SlapRedBrain=<.onnx> -SlapBlueBrain=<.onnx>`; tournaments: `training/tournament.py` (roster `training/roster.json`).

## Rebuilding from a fresh clone

1. **URLab** (not in git): `git clone --recurse-submodules https://github.com/URLab-Sim/UnrealRoboticsLab Plugins/UnrealRoboticsLab`,
   then `git -C Plugins/UnrealRoboticsLab am ../urlab_poslap.patch` (MuJoCo pinned to 3.10.0 + UE 5.8 fixes).
2. **URLab natives**: `third_party/build_all.ps1`. If CoACD fails with "file RENAME failed … Access is denied" (antivirus on the
   Boost extract), configure CoACD by hand with `-DFETCHCONTENT_SOURCE_DIR_BOOST=<extracted boost dir>` and quote every `-D…` argument.
3. **Python**: `uv venv training/.venv --python 3.11`, `uv pip install -r training/requirements.txt` (+ torch cu128, trimesh scipy networkx Pillow for URLab's importer).
4. **Body**: `training/.venv/Scripts/python.exe training/build_body.py training/characters/grandpa.json`.
5. **Unreal**: build `PoSlapUnrealEditor`, then `UnrealEditor-Cmd … -run=pythonscript -script=Scripts/build_scene.py`.
   Arena art only: `training/.venv/Scripts/python.exe Scripts/arena_textures.py`, export `Content/Art/Raw/Arena_v2.blend` → `Exports/Arena.glb`,
   then `-script=Scripts/arena_env.py` (re-imports the env, lights, haze, grade into the level). Brain only: `Scripts/set_brain.py`.
6. **Checks**: `UnrealEditor … -game -SlapMode=Replay -SlapShot=10`, then
   `training/replay_check.py compare training/replays/<model>_5s.json Saved/URLab/replay_ue.json --mjb Saved/URLab/scene_compiled.mjb`.
   Re-record the stream after a model change: `training/replay_check.py record training/models/<red>_vs_<blue>_arena.xml`.

Matching rules learned the hard way (keep them): URLab drops a child model's `<option>` (USlapController sets it from the
fingerprint), applies `<default>` to free joints (write the root as an explicit `type="free"` joint), and stores floats
(write MJCF numbers float32-exact, radians, explicit pos/quat/size). Exact math both sides: stand pose read in radians from the
fingerprint, `std::atan2/cos/sin` (not FMath). Live check = Python on URLab's `.mjb` vs Unreal: bit-identical (0.000 cm / 5 s).
**The Hill cap (G4) is physics, not brain** — every path that steps the model applies it, including the replay
(`ApplyHill` walks `HillMotors`, built per path from the fingerprints). Skipping it on one side only made the replay
diverge 1182 mm over 5 s (2026-10-29). **A matching gate is only meaningful in a stable regime**: the recorded stream's
swing is 0.12 rad (`replay_check.AMP`) because at 0.25 both athletes fall, and a fall amplifies float residues into
centimetres — the XML and URLab's own compiled model drift 85 mm apart with identical maths on both sides.
More (2026-10-24): `build_scene.py` wipes+saves the LEVEL before deleting/importing the arena Blueprint — URLab refuses to
import over a BP the open level still instantiates and `delete_asset` fails on a referenced asset. Every arena's
`DefenderRole` must be **-1** for L3+ brains (a level built with 0 feeds the defender different inputs and he collapses in
seconds; `Scripts/set_defender_role.py` fixes them all). All arena XMLs carry `iterations="20"` (the e73efa1 audit) —
rebuild with `build_body.py` after builder changes, then reimport the levels.
Eye contact (2026-10-24, user: "the person getting slapped should look his opponent in the eye before the slap";
then "after the slapper slaps he should stay standing up and balanced and await to see if his opponent falls"):
`match.py` GAZE/WATCH constants and `USlapController::ApplyGaze` run IDENTICAL maths. The gaze bias lives in
WORLD SPACE (great-circle walk, converted to the chest frame's ZYX yaw/pitch only at application) — because the
chest WHIPS during a follow-through and a chest-relative bias chases a spinning target. Roles flip at the
touch: the defender eye-locks until the slap LANDS (then he braces), and the striker WATCHES the verdict —
continuing through the inter-turn hold (display-only, so event parity is untouched). The striker's body stays
fully brain-commanded (see the QA trap below). Observations are untouched, no retraining.
Kill switches: `SLAP_EYE_CONTACT=0` (Python), `-SlapEyeContact=0` (Unreal). Five traps encoded in the code:
anchor on the HEAD CENTRES (the eye line — the chest pivot adds ~11° at 0.85 m), aim in the CHEST frame (waist
ball ≠ pelvis), bias in WORLD space (torso whip), `neck_nod` clipping at its range end when heights differ,
and the pose-hold ban below.
verified: defender eye-lock 4–6° median (baseline 62°), L5 compare PASS 28/28.
QA trap: NEVER impose a post-slap POSE on the striker — freezing joints after the touch fights the brain's own
balance and multiplies self-knockdowns (tested: melt-to-stand, release-pose, spring-compensated, arm-only — all
far worse than baseline). The post-touch servo watches (gaze only); the brain keeps the body.
Anti-fall reward economics (2026-10-25): a striker's self-fall must cost MORE than his hit pays (hit was +146,
fall −30 → profitable tumbles; now −150) and the recovery window must PAYOUT for staying upright (was flat zeros).
Result: 0 self-downs over 10 matches, hits and speeds intact.
Adding an athlete (roster, 16 slots): rigged FBX -> `blender -b --python Scripts/rig_mixamo.py -- <fbx> <name> <height>` -> card `training/characters/<name>.json`
(`"slot"`, stats, `style`: stance_deg + slap) -> `build_body.py` (+ `--blue` per pairing) -> `slap_demo.py search --char <name>` -> own ladder
(`overnight.py --char <name> --queue <name>_queue.json`) -> pairing level (`build_scene.py` with SLAP_RED/SLAP_BLUE; from Git Bash set
`MSYS2_ENV_CONV_EXCL=SLAP_` or "/Game/..." values get rewritten to C:/Program Files/Git/...). Open a level: `UnrealEditor … /Game/Levels/L_Arena_<red>_vs_<blue> -game`.
UI check: `-SlapUiTest` (synthetic clicks through the menu sheet + one match loop, logs PASS/FAIL); `-SlapMenu=<0-2>` opens the sheet for screenshots.
Live check: `-SlapRecord=Saved/URLab/live_ue.json`, then `replay_check.py live models/brains/<brain>.onnx Saved/URLab/live_ue.json`.
