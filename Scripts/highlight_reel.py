"""Render a highlight reel: super slow motion in which every frame is one real MuJoCo step.

    training/.venv/Scripts/python.exe Scripts/highlight_reel.py                 the newest reel, 1080 x 1920
    training/.venv/Scripts/python.exe Scripts/highlight_reel.py --4k            2160 x 3840
    training/.venv/Scripts/python.exe Scripts/highlight_reel.py <file.reel> --from 0.4 --to 1.5 --tier 2

The game saves a reel 3 s after every knockout and after any slap of 9 m/s or more (ASlapHUD::ReelWatch ->
Saved/PoSlap/reels/*.reel + .json: the last 6 s of physics steps, every body's pose per 5 ms step). This script plays one
back in Unreal off screen (-SlapReel, SlapHUDReel.cpp): one recorded step per rendered frame at a fixed 5 ms frame time,
each frame written as a PNG, then ffmpeg encodes them at 60 fps -> Saved/Clips/<reel>.mp4. 200 steps per second shown at
60 frames per second is 3.3x slow motion with no interpolated poses.

Display only: the reel is a recording; nothing here steps or changes the physics. Needs ffmpeg on the PATH and a GPU
(the editor binary renders it). Local only: nothing is uploaded.
"""
import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REELS = ROOT / "Saved" / "PoSlap" / "reels"
CLIPS = ROOT / "Saved" / "Clips"
EDITOR = Path(r"C:\Program Files\Epic Games\UE_5.8\Engine\Binaries\Win64\UnrealEditor.exe")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("reel", nargs="?", help="a .reel file (default: the newest in Saved/PoSlap/reels)")
    ap.add_argument("--4k", dest="uhd", action="store_true", help="2160 x 3840 instead of 1080 x 1920")
    ap.add_argument("--from", dest="start", type=float, default=0.6, help="seconds before the touch (default 0.6)")
    ap.add_argument("--to", dest="end", type=float, default=2.4, help="seconds after the touch (default 2.4)")
    ap.add_argument("--fps", type=int, default=60, help="the video's frame rate (default 60: 3.3x slow motion)")
    ap.add_argument("--tier", type=int, default=3, help="quality tier (3 = CINEMA: Lumen; 2 = HIGH)")
    ap.add_argument("--editor", default=str(EDITOR))
    ap.add_argument("--keep-frames", action="store_true")
    a = ap.parse_args()

    reels = sorted(REELS.glob("*.reel"))
    reel = Path(a.reel).resolve() if a.reel else (reels[-1] if reels else None)
    if reel is None or not reel.is_file():
        sys.exit(f"no reel: play a match to a knockout first (reels land in {REELS})")
    if not shutil.which("ffmpeg"):
        sys.exit("ffmpeg is not on the PATH")
    meta_file = reel.with_suffix(".json")
    meta = json.loads(meta_file.read_text()) if meta_file.is_file() else {}
    level = meta.get("level", "/Game/Levels/L_Arena_grandpa_vs_kim")
    w, h = (2160, 3840) if a.uhd else (1080, 1920)
    frames = CLIPS / f"{reel.stem}_frames"
    shutil.rmtree(frames, ignore_errors=True)
    frames.mkdir(parents=True)
    out = CLIPS / f"{reel.stem}_{h}p.mp4"

    cmd = [a.editor, str(ROOT / "PoSlapUnreal.uproject"), level, "-game", "-RenderOffscreen", f"-ResX={w}", f"-ResY={h}",
           "-ForceRes", f"-SlapReel={reel}", f"-SlapReelOut={frames}", f"-SlapReelFrom={a.start}", f"-SlapReelTo={a.end}",
           f"-SlapTier={a.tier}", "-nosplash", "-nosound", "-log"]
    print(f"rendering {reel.name} ({meta.get('why', '?')}, {meta.get('speed', '?')} m/s) at {w} x {h}, tier {a.tier} ...", flush=True)
    t0 = time.time()
    subprocess.run(cmd, check=False, timeout=3600)
    pngs = sorted(frames.glob("frame_*.png"))
    if len(pngs) < 10:
        sys.exit(f"only {len(pngs)} frames were written: see Saved/Logs/PoSlapUnreal.log (LogSlap: reel ...)")
    print(f"{len(pngs)} frames in {time.time() - t0:.0f} s; encoding ...", flush=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-framerate", str(a.fps), "-i", str(frames / "frame_%05d.png"),
                    "-c:v", "libx264", "-crf", "16", "-preset", "slow", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)],
                   check=True)
    if not a.keep_frames:
        shutil.rmtree(frames, ignore_errors=True)
    step = float(meta.get("timestep", 0.005))
    print(f"{out}  ({len(pngs) / a.fps:.1f} s of video for {len(pngs) * step:.2f} s of match: {1.0 / step / a.fps:.1f}x slow motion)")


if __name__ == "__main__":
    main()
