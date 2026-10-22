"""KO clips: records the game window into a rolling 20 s buffer and, after every KO in Unreal's match log, cuts the 10 s
around it (8 s before, 2 s after) into Saved/Clips/KO_<YYMMDD_HHMMSS>.mp4 at the window's own 9:16 size, ready for
Shorts / TikTok. Video only: the game's sound is not captured.

    training/.venv/Scripts/python.exe Scripts/clipper.py [--on ko,strike] [--fps 30]

ffmpeg (gdigrab) writes 2 s MPEG-TS segments in a ring of 10 (Saved/Clips/buffer); a clip is those segments joined
without re-encoding. --on strike also clips every touch (4 s before, 2 s after).
"""
import argparse, json, subprocess, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLIPS = ROOT / "Saved" / "Clips"
BUF = CLIPS / "buffer"
SEG = 2.0
WINDOW = {"ko": (8.0, 2.0), "strike": (4.0, 2.0)}   # seconds before, after


RECT_PS = r"""
Add-Type @'
using System; using System.Runtime.InteropServices;
public struct R { public int L, T, Rr, B; }
public struct P { public int X, Y; }
public static class W {
  [DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h, out R r);
  [DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr h, ref P p); }
'@
$g = Get-Process UnrealEditor -ErrorAction SilentlyContinue | Where-Object MainWindowTitle | Select-Object -First 1
if ($g) { $r = New-Object R; $p = New-Object P
  [W]::GetClientRect($g.MainWindowHandle, [ref]$r) | Out-Null; [W]::ClientToScreen($g.MainWindowHandle, [ref]$p) | Out-Null
  "$($p.X) $($p.Y) $($r.Rr) $($r.B)" }
"""


def game_window():
    """The game's client area on screen (x, y, w, h). The window's own DC is black (D3D), so the composed desktop is
    grabbed at these coordinates: keep the window visible and on top while recording. Logical (DPI-scaled) pixels,
    like ffmpeg's: neither process is DPI-aware, so both see the same virtual desktop."""
    out = subprocess.run(["powershell", "-NoProfile", "-Command", RECT_PS], capture_output=True, text=True).stdout.split()
    return tuple(int(v) for v in out) if len(out) == 4 and int(out[2]) > 0 else None


def newest_log():
    day = ROOT / "Logs" / "Gameplay" / time.strftime("%y%m%d")
    logs = sorted(day.glob("*_ue.jsonl"), key=lambda p: p.stat().st_mtime) if day.is_dir() else []
    return logs[-1] if logs else None


def cut(kind, at):
    before, after = WINDOW[kind]
    segs = sorted(BUF.glob("seg*.ts"), key=lambda p: p.stat().st_mtime)[:-1]   # the newest is still being written
    keep = [p for p in segs if at - before <= p.stat().st_mtime <= at + after + SEG]
    if not keep:
        return None
    out = CLIPS / f"{kind.upper()}_{time.strftime('%y%m%d_%H%M%S', time.localtime(at))}.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", "concat:" + "|".join(str(p) for p in keep),
                    "-c", "copy", str(out)], check=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--on", default="ko", help="ko and/or strike, comma-separated")
    ap.add_argument("--fps", type=int, default=30)
    a = ap.parse_args()
    kinds = set(a.on.split(","))
    BUF.mkdir(parents=True, exist_ok=True)
    print("clipper: waiting for the game window")
    while not (rect := game_window()):
        time.sleep(1.0)
    x, y, w, h = rect
    rec = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-f", "gdigrab", "-framerate", str(a.fps), "-offset_x", str(x),
                            "-offset_y", str(y), "-video_size", f"{w // 2 * 2}x{h // 2 * 2}", "-i", "desktop",
                            "-c:v", "libx264", "-preset", "veryfast",
                            "-pix_fmt", "yuv420p", "-g", str(a.fps), "-f", "segment", "-segment_time", str(SEG),
                            "-segment_wrap", "10", "-reset_timestamps", "1", str(BUF / "seg%02d.ts")])
    print(f"clipper: recording {w}x{h} at ({x}, {y}), clipping on {sorted(kinds)}")
    log = newest_log()
    pos = log.stat().st_size if log else 0
    pending = []   # (kind, wall time of the event)
    try:
        while rec.poll() is None:
            newest = newest_log()
            if newest and newest != log:
                log, pos = newest, 0
            if log:
                with open(log, encoding="utf-8") as f:
                    f.seek(pos)
                    for raw in f:
                        if not raw.endswith("\n"):
                            break
                        pos += len(raw.encode("utf-8"))
                        e = json.loads(raw)
                        if "ko" in kinds and e["type"] == "result" and e["how"] == "KO":
                            pending.append(("ko", time.time()))
                        elif "strike" in kinds and e["type"] == "strike":
                            pending.append(("strike", time.time()))
            for job in [j for j in pending if time.time() > j[1] + WINDOW[j[0]][1] + SEG + 0.5]:
                pending.remove(job)
                print("clip:", cut(*job), flush=True)
            time.sleep(0.2)
    finally:
        rec.terminate()


if __name__ == "__main__":
    main()
