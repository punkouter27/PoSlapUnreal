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

## Rebuilding from a fresh clone

1. **URLab** (not in git): `git clone --recurse-submodules https://github.com/URLab-Sim/UnrealRoboticsLab Plugins/UnrealRoboticsLab`,
   then `git -C Plugins/UnrealRoboticsLab am ../urlab_poslap.patch` (MuJoCo pinned to 3.10.0 + UE 5.8 fixes).
2. **URLab natives**: `third_party/build_all.ps1`. If CoACD fails with "file RENAME failed … Access is denied" (antivirus on the
   Boost extract), configure CoACD by hand with `-DFETCHCONTENT_SOURCE_DIR_BOOST=<extracted boost dir>` and quote every `-D…` argument.
3. **Python**: `uv venv training/.venv --python 3.11`, `uv pip install -r training/requirements.txt` (+ torch cu128, trimesh scipy networkx Pillow for URLab's importer).
4. **Body**: `training/.venv/Scripts/python.exe training/build_body.py training/characters/grandpa.json`.
5. **Unreal**: build `PoSlapUnrealEditor`, then `UnrealEditor-Cmd … -run=pythonscript -script=Scripts/build_scene.py`.
6. **Checks**: `UnrealEditor … -game -SlapMode=Replay -SlapShot=10`, then
   `training/replay_check.py compare training/replays/<model>_5s.json Saved/URLab/replay_ue.json --mjb Saved/URLab/scene_compiled.mjb`.

Matching rules learned the hard way (keep them): URLab drops a child model's `<option>` (USlapController sets it from the
fingerprint), applies `<default>` to free joints (write the root as an explicit `type="free"` joint), and stores floats
(write MJCF numbers float32-exact, radians, explicit pos/quat/size).
