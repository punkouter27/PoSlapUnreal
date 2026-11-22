"""PoSlapUnreal Workload: the real training/train.py main() (own PPO on MuJoCo Warp), as the current main training run.

Run by serve.py with the training venv:  training/.venv/Scripts/python.exe benchmark/workload.py
The current main run is the retrain_v5 queue (training/runs/retrain_v5_queue.json, driven by overnight.py): every
stage trains 8192 worlds on physics 4. Its stage in progress, matt L3 from scratch (no --resume, so no checkpoint is
needed), is the Workload. One training iteration = ppo.cfg.steps_per_env Steps of all worlds, then ppo.update():
the hook below times the time between updates as Sim time and the update itself as Learn time.

Safety: train.py is imported (not run as a script) so its housekeeping (prunes runs older than 24 h) and its
TensorBoard launch are switched off; the run writes only to training/runs/_benchmark (gitignored, wiped each time);
the config-snapshot copy train.py drops in docs/<YYYYMMDD>/config_snapshots/ is removed again. The Meter ends the
process after the measured window, so no checkpoint, report or brain is ever exported.
"""

from __future__ import annotations

import dataclasses
import os
import shutil
import sys
import time
from importlib import metadata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import core  # noqa: E402  (first, so core.T_PROCESS covers torch/Warp import, env build and kernel compile)

ROOT = Path(__file__).resolve().parents[1]
TRAINING = ROOT / "training"
RUN = "_benchmark"
CHAR, LEVEL, ENVS, PHYSICS = "matt", "L3", 8192, "4"
# Same settings as the main training run's current stage (retrain_v5 queue). Change these and the Workload ID changes.
ARGS = ["--char", CHAR, "--level", LEVEL, "--envs", str(ENVS), "--physics", PHYSICS, "--minutes", "600", "--run", RUN]


def version(pkg: str) -> str | None:
    try:
        return metadata.version(pkg)
    except metadata.PackageNotFoundError:
        return None


def main() -> None:
    meter: core.Meter | None = None
    snapshot_copy: Path | None = None
    try:
        os.chdir(TRAINING)
        sys.path.insert(0, str(TRAINING))
        import torch
        import train
        from ppo import PPO

        train.housekeeping = lambda keep=(): None  # never prune the user's runs
        train.tensorboard = lambda: None  # no TensorBoard server left behind
        write_snapshot = train.write_config_snapshot

        def write_snapshot_(run, args):
            nonlocal snapshot_copy
            day = TRAINING.parent / "docs" / time.strftime("%Y%m%d") / "config_snapshots"
            existed = day.exists()
            write_snapshot(run, args)
            snapshot_copy = day / f"{run.name}.json"
            snapshot_copy.unlink(missing_ok=True)  # the docs copy is for real runs only
            if not existed:
                for d in (day, day.parent):
                    try:
                        d.rmdir()  # only if empty
                    except OSError:
                        break

        train.write_config_snapshot = write_snapshot_

        init, update = PPO.__init__, PPO.update
        last = {"t": None}

        def init_(self, obs_dim, act_dim, n, device, cfg, *a, **k):
            nonlocal meter
            init(self, obs_dim, act_dim, n, device, cfg, *a, **k)
            from constants import physics

            hidden = [m.out_features for m in self.model.actor if isinstance(m, torch.nn.Linear)][:-1]
            resolved = {
                "ppo": dataclasses.asdict(cfg),
                "level": train.LEVELS[LEVEL],
                "hashes": train.config_hashes(CHAR, LEVEL),
                "obs_dim": obs_dim,
                "act_dim": act_dim,
                "agents": n,
            }
            workload = dict(
                repo="PoSlapUnreal",
                trainer=f"own PPO (training/ppo.py), mujoco-warp {version('mujoco-warp')}",
                script="training/train.py",
                args=" ".join(ARGS),
                mirrors="training/runs/retrain_v5_queue.json stage 'L3 matt' (overnight.py train())",
                num_envs=ENVS,
                agents=n,
                horizon=cfg.steps_per_env,
                net=hidden,
                physics=physics(),
                config_hash=core.workload_id(resolved),
            )
            versions = dict(
                python=sys.version.split()[0],
                torch=version("torch"),
                mujoco=version("mujoco"),
                mujoco_warp=version("mujoco-warp"),
                warp=version("warp-lang"),
            )
            meter = core.Meter(workload, versions)

        def update_(self, *a, **k):
            torch.cuda.synchronize()
            t_start = time.perf_counter()
            out = update(self, *a, **k)
            torch.cuda.synchronize()
            t_end = time.perf_counter()
            sim = t_start - last["t"] if last["t"] is not None else 0.0
            last["t"] = t_end
            meter.block(sim, t_end - t_start, self.cfg.steps_per_env)
            return out

        PPO.__init__, PPO.update = init_, update_
        shutil.rmtree(train.RUNS / RUN, ignore_errors=True)
        sys.argv = ["train.py", *ARGS]
        train.main()
        raise RuntimeError("training ended before the measured window")
    except BaseException as e:  # noqa: BLE001 - every failure becomes a failed row, never a silent miss
        if snapshot_copy is not None:
            snapshot_copy.unlink(missing_ok=True)
        if meter is None:
            meter = core.Meter({"repo": "PoSlapUnreal", "args": " ".join(ARGS)}, {"python": sys.version.split()[0]})
        meter.finish(ok=False, error=core.fail_text(e))


if __name__ == "__main__":
    main()
