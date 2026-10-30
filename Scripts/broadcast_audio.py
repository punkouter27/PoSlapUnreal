"""Broadcast sounds, synthesised (no recordings, no licences): Content/Art/Exports/Audio/*.wav, 44.1 kHz mono 16-bit.

    training/.venv/Scripts/python.exe Scripts/broadcast_audio.py      (then Scripts/broadcast_fx.py imports them)

Slap, Slap1-3: a broadband crack over a short flesh thump (four takes; ASlapHUD picks one and filters it by palm speed).
Thud: a body landing (stumble, KO). Squeak0-1: a rubber sole skidding; Scrape: a sole dragged. Step: a sole on the floor
(a footstep, played from MuJoCo's own contact). Breath: two pants that loop
(louder and faster as stamina drains). CrowdReact: an "ooh" swelling into a roar. CrowdNear: the groan of a near miss
(a slap that landed but did not stagger). CrowdLoop: 12 s of arena murmur that
loops seamlessly. Bell: three strikes of a boxing bell (inharmonic partials).
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


def slap(crack_hi=9000, thump_hz=110, wet=0.6):
    s = t(0.4)
    crack = band(rng.standard_normal(len(s)), 1500, crack_hi) * np.exp(-s / 0.012)
    body = band(rng.standard_normal(len(s)), 300, 1500) * np.exp(-s / 0.035)
    thump = np.sin(2 * np.pi * thump_hz * s) * np.exp(-s / 0.045)
    return 1.0 * crack + wet * body + 0.5 * thump


def thud():
    s = t(0.6)
    drop = np.sin(2 * np.pi * (70 - 25 * s) * s) * np.exp(-s / 0.09)   # the pitch sags as the body settles
    flesh = band(rng.standard_normal(len(s)), 120, 700) * np.exp(-s / 0.05)
    return drop + 0.5 * flesh


def squeak(f0):
    s = t(0.22)
    f = f0 * (1 + 0.35 * np.sin(2 * np.pi * 9 * s))                    # the sole sticks and slips
    ph = 2 * np.pi * np.cumsum(f) / SR
    tone = np.sin(ph) + 0.4 * np.sin(2 * ph) + 0.2 * np.sin(3 * ph)
    env = np.clip(s / 0.015, 0, 1) * np.clip((0.22 - s) / 0.06, 0, 1)
    return (tone + 0.3 * band(rng.standard_normal(len(s)), 2000, 6000)) * env


def scrape():
    s = t(0.35)
    grit = band(rng.standard_normal(len(s)), 400, 4000) * (0.6 + 0.4 * (rng.random(len(s)) > 0.7))
    return grit * np.sin(np.pi * s / 0.35) ** 2


def breath():
    s = t(1.6)                                                           # two pants, in and out, loops cleanly
    env = np.sin(np.pi * ((s % 0.8) / 0.8)) ** 2 * np.where((s % 0.8) < 0.3, 0.5, 1.0)
    return band(pink(len(s)), 300, 2500) * env


def crowd_react():
    s = t(3.0)
    env = np.clip(s / 0.25, 0, 1) * np.exp(-np.clip(s - 0.25, 0, None) / 1.2)
    ooh = band(pink(len(s)), 250, 700) * np.clip(1 - s / 0.8, 0, 1)        # the gasp first
    roar = band(pink(len(s)), 200, 3500) * np.clip((s - 0.15) / 0.5, 0, 1)
    return (ooh + roar) * env


def crowd_near():
    """The near miss: a gasp that sags into a groan. No roar - the slap landed but nobody was moved."""
    s = t(2.4)
    env = np.clip(s / 0.12, 0, 1) * np.exp(-np.clip(s - 0.12, 0, None) / 0.9)
    gasp = band(pink(len(s)), 230, 640) * np.clip(1 - s / 0.65, 0, 1)
    groan = band(pink(len(s)), 170, 1100) * np.clip((s - 0.08) / 0.35, 0, 1) * (1.0 - 0.45 * np.clip((s - 0.5) / 1.4, 0, 1))
    return (gasp + groan) * env


def step():
    """A sole on the floor: a dull knock with a little grit under it."""
    s = t(0.2)
    knock = np.sin(2 * np.pi * (150 - 70 * s) * s) * np.exp(-s / 0.028)
    grit = band(rng.standard_normal(len(s)), 700, 6000) * np.exp(-s / 0.018) * 0.4
    return knock + grit


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
    for i, (hi, hz, wet) in enumerate(((7000, 95, 0.8), (11000, 125, 0.5), (8500, 140, 0.7)), 1):
        save(f"Slap{i}", slap(hi, hz, wet))
    save("Thud", thud())
    save("Squeak0", squeak(1100))
    save("Squeak1", squeak(1500))
    save("Scrape", scrape())
    save("Breath", breath())
    save("CrowdReact", crowd_react())
    save("CrowdNear", crowd_near())
    save("Step", step())
    save("CrowdLoop", crowd_loop())
    save("Bell", bell())
