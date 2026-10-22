"""Broadcast sounds, synthesised (no recordings, no licences): Content/Art/Exports/Audio/*.wav, 44.1 kHz mono 16-bit.

    training/.venv/Scripts/python.exe Scripts/broadcast_audio.py      (then Scripts/broadcast_fx.py imports them)

Slap: a broadband crack over a short flesh thump. CrowdReact: an "ooh" swelling into a roar. CrowdLoop: 12 s of arena
murmur that loops seamlessly. Bell: three strikes of a boxing bell (inharmonic partials).
"""
import wave
from pathlib import Path

import numpy as np
from scipy.signal import butter, sosfilt

SR = 44100
OUT = Path(__file__).resolve().parents[1] / "Content" / "Art" / "Exports" / "Audio"
rng = np.random.default_rng(7)


def t(seconds):
    return np.arange(int(seconds * SR)) / SR


def band(x, lo, hi):
    return sosfilt(butter(4, [lo, hi], btype="band", fs=SR, output="sos"), x)


def pink(n):   # 1/f noise: white noise shaped in the frequency domain
    f = np.fft.rfftfreq(n, 1 / SR)
    spec = np.fft.rfft(rng.standard_normal(n)) / np.sqrt(np.maximum(f, 20.0))
    return np.fft.irfft(spec, n)


def save(name, x):
    x = 0.9 * x / np.abs(x).max()
    OUT.mkdir(parents=True, exist_ok=True)
    with wave.open(str(OUT / f"{name}.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((x * 32767).astype("<i2").tobytes())
    print(f"{name}.wav {len(x) / SR:.2f} s")


def slap():
    s = t(0.4)
    crack = band(rng.standard_normal(len(s)), 1500, 9000) * np.exp(-s / 0.012)
    body = band(rng.standard_normal(len(s)), 300, 1500) * np.exp(-s / 0.035)
    thump = np.sin(2 * np.pi * 110 * s) * np.exp(-s / 0.045)
    return 1.0 * crack + 0.6 * body + 0.5 * thump


def crowd_react():
    s = t(3.0)
    env = np.clip(s / 0.25, 0, 1) * np.exp(-np.clip(s - 0.25, 0, None) / 1.2)
    ooh = band(pink(len(s)), 250, 700) * np.clip(1 - s / 0.8, 0, 1)        # the gasp first
    roar = band(pink(len(s)), 200, 3500) * np.clip((s - 0.15) / 0.5, 0, 1)
    return (ooh + roar) * env


def crowd_loop():
    n = int(12 * SR)
    s = np.arange(n + SR) / SR
    swell = 1 + 0.25 * np.sin(2 * np.pi * s / 6) + 0.15 * np.sin(2 * np.pi * s / 2.4 + 1)   # whole periods in 12 s
    y = band(pink(n + SR), 180, 2500) * swell
    x = y[:n].copy()
    fade = np.linspace(0, 1, SR)   # the second after the end fades into the start: the loop point is seamless
    x[:SR] = x[:SR] * fade + y[n:] * (1 - fade)
    return x


def bell():
    s = t(2.4)
    one = sum(a * np.sin(2 * np.pi * 880 * r * s) * np.exp(-s / d)
              for r, a, d in ((1.0, 1.0, 0.9), (2.76, 0.5, 0.5), (5.4, 0.3, 0.25), (8.93, 0.2, 0.12)))
    x = np.zeros(len(s))
    for at in (0.0, 0.35, 0.7):
        k = int(at * SR)
        x[k:] += one[:len(x) - k]
    return x


if __name__ == "__main__":
    save("Slap", slap())
    save("CrowdReact", crowd_react())
    save("CrowdLoop", crowd_loop())
    save("Bell", bell())
