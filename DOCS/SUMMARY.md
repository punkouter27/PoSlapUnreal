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
