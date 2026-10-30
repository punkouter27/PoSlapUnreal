"""Broadcast sounds, synthesised (no recordings, no licences): Content/Art/Exports/Audio/*.wav, 44.1 kHz mono 16-bit,
plus 32 kHz copies in Web/assets/audio/ for the browser.

    training/.venv/Scripts/python.exe Scripts/broadcast_audio.py      (then Scripts/broadcast_fx.py imports them)

Each sound is a small physical model, not filtered noise:
Slap, Slap1-3: palm and fingers landing a few ms apart (a flam of clicks), the air trapped between palm and cheek
ringing as it is squeezed out (a damped Helmholtz pop, 1.5-2.6 kHz), the skin's crack, the cheek's flesh and the skull
under it (ASlapHUD picks a take and pitches it by strike mass). Thud: a body landing on the padded mat in sequence -
knees, hip, shoulder, head - each a sagging low mode, the pad's "pomf" and the clothes. Squeak0-1: rubber stick-slip
(a sawtooth whose period is the stick time) through the sole's resonances. Scrape: random stick-slip events dragged
across the mat. Step: heel, then the ball of the foot. Breath: an old man's two pants (turbulence through the mouth's
formants, a voiced edge on the exhale) that loop. CrowdReact: ~70 synthesised voices (glottal pulses through vowel
formants) going "ooh" and swelling into a cheer, with clap-by-clap applause. CrowdNear: the "ooh" sagging into a groan.
CrowdLoop: 12 s of murmur - voices babbling vowels, scattered claps, a whistle, a cough - that loops seamlessly.
Bell: three hammer strikes on a dome bell (inharmonic partials as detuned pairs that beat).
The crowd beds carry a little baked hall; everything struck stays dry (Unreal's RV_Arena / RV_Ringside add the room).
"""
import wave
from pathlib import Path

import numpy as np
from scipy.signal import butter, lfilter, resample_poly, sosfilt

SR = 44100
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "Content" / "Art" / "Exports" / "Audio"
WEB = ROOT / "Web" / "assets" / "audio"
rng = np.random.default_rng(7)


def t(seconds):
    return np.arange(int(seconds * SR)) / SR


def band(x, lo, hi, order=4):
    return sosfilt(butter(order, [lo, hi], btype="band", fs=SR, output="sos"), x)


def low(x, hz, order=2):
    return sosfilt(butter(order, hz, btype="low", fs=SR, output="sos"), x)


def high(x, hz, order=2):
    return sosfilt(butter(order, hz, btype="high", fs=SR, output="sos"), x)


def pink(n):   # 1/f noise: white noise shaped in the frequency domain
    f = np.fft.rfftfreq(n, 1 / SR)
    spec = np.fft.rfft(rng.standard_normal(n)) / np.sqrt(np.maximum(f, 20.0))
    return np.fft.irfft(spec, n)


def reson(x, f, bw):
    """A two-pole resonator at f Hz, bandwidth bw Hz, unity gain at f (a formant, a body mode)."""
    r = np.exp(-np.pi * bw / SR)
    w = 2 * np.pi * f / SR
    return lfilter([(1 - r * r) * np.sin(w)], [1, -2 * r * np.cos(w), r * r], x)


def mode(s, f, decay, chirp=0.0):
    """A struck mode: a sine at f Hz (sagging by `chirp` Hz/s) dying away over `decay` s."""
    return np.sin(2 * np.pi * np.cumsum(np.maximum(f + chirp * s, 10)) / SR) * np.exp(-s / decay)


def at(x, n, sec, y, gain=1.0):
    """Mix y into x starting at `sec` seconds."""
    k = int(sec * SR)
    if k < n:
        m = min(len(y), n - k)
        x[k:k + m] += gain * y[:m]
    return x


def smooth_noise(n, hz):
    """A slowly wandering control signal (-1..1), corner frequency hz."""
    return np.tanh(2 * low(rng.standard_normal(n), hz, 1) / np.sqrt(hz / SR))


def save(name, x, loop=False):
    if not loop:                                                                   # no click where the file ends
        k = int(0.015 * SR)
        x[-k:] *= np.linspace(1, 0, k)
    x = 0.9 * x / np.abs(x).max()
    OUT.mkdir(parents=True, exist_ok=True)
    WEB.mkdir(parents=True, exist_ok=True)
    for path, rate, y in ((OUT / f"{name}.wav", SR, x), (WEB / f"{name}.wav", 32000, resample_poly(x, 320, 441))):
        y = 0.9 * y / np.abs(y).max()
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes((y * 32767).astype("<i2").tobytes())
    print(f"{name}.wav {len(x) / SR:.2f} s")


# ---- struck flesh ---------------------------------------------------------------------------------------------------

def slap(pop_hz=1900, crack=1.0, flesh_hz=210, skull_hz=110, fingers=4, spread=0.0035):
    s = t(0.4)
    n = len(s)
    x = np.zeros(n)
    # the hand does not land at once: the palm heel and the fingers arrive over a few ms (the flam that makes it a slap)
    lands = np.sort(rng.uniform(0, spread, fingers + 1))
    for i, ti in enumerate(lands):
        click = high(rng.standard_normal(int(0.0012 * SR)) * np.exp(-np.arange(int(0.0012 * SR)) / (0.00025 * SR)), 900)
        at(x, n, 0.002 + ti, click, (1.0 if i == 0 else rng.uniform(0.35, 0.8)) * crack)
    # the air pocket between palm and cheek, squeezed out: a short Helmholtz ring, the slap's "pitch"
    pop = reson(np.exp(-s / 0.0008) * rng.standard_normal(n), pop_hz * rng.uniform(0.95, 1.05), pop_hz / 6) * 3.0
    pop += 0.5 * reson(np.exp(-s / 0.0008) * rng.standard_normal(n), pop_hz * 1.9, pop_hz / 4)
    at(x, n, 0.002 + lands[-1] * 0.6, pop * np.exp(-s / 0.006))
    # the skin's crack: bright, very short
    at(x, n, 0.002, band(rng.standard_normal(n), 2500, 11000) * np.exp(-s / 0.007) * 0.6 * crack)
    # the cheek's flesh and the skull under it
    flesh = band(rng.standard_normal(n), 250, 1400) * np.exp(-s / 0.022)
    at(x, n, 0.0025, 0.55 * flesh + 0.45 * mode(s, flesh_hz, 0.03, -150) + 0.3 * mode(s, skull_hz, 0.05, -60))
    # an early reflection off the podium and the striker's own body (~1 m of path)
    x += 0.18 * low(np.concatenate([np.zeros(int(0.0029 * SR)), x[:n - int(0.0029 * SR)]]), 5000)
    return x


def thud():
    """A body landing on the padded mat: knees, hip, shoulder, head, each heavier part lower and later."""
    s = t(0.9)
    n = len(s)
    x = np.zeros(n)
    for when, amp, hz, dec in ((0.0, 0.7, 95, 0.06), (0.055, 1.0, 68, 0.09), (0.13, 0.75, 80, 0.07), (0.21, 0.4, 120, 0.05)):
        pomf = low(rng.standard_normal(n), 500, 2) * np.exp(-s / 0.035)       # the pad's foam squeezing out air
        hit = mode(s, hz, dec, -40) + 0.6 * pomf + 0.25 * mode(s, hz * 2.3, dec * 0.5)
        at(x, n, when, low(hit, 1800), amp)
    rustle = band(rng.standard_normal(n), 1200, 6000) * np.clip(s / 0.03, 0, 1) * np.exp(-s / 0.18)   # clothes
    return x + 0.08 * rustle


def step():
    """A shoe on the padded mat: the heel, then the ball of the foot 60-80 ms later; a hint of rubber grip."""
    s = t(0.3)
    n = len(s)
    x = np.zeros(n)
    for when, amp, hz in ((0.0, 1.0, 140), (rng.uniform(0.06, 0.08), 0.55, 175)):
        knock = mode(s, hz, 0.022, -300) + 0.5 * low(rng.standard_normal(n), 900) * np.exp(-s / 0.015)
        at(x, n, when, knock, amp)
    grip = band(rng.standard_normal(n), 1500, 5000) * np.exp(-s / 0.01)
    return x + 0.12 * grip


# ---- rubber ----------------------------------------------------------------------------------------------------------

def squeak(f0):
    """Stick-slip: the sole sticks, tension builds, it slips (a sawtooth whose period is the stick time); the shoe's
    resonances colour it. The rate wobbles as the load shifts, and the sole sometimes catches and lets go."""
    s = t(0.24)
    n = len(s)
    f = f0 * (1 + 0.18 * np.sin(2 * np.pi * 7 * s) + 0.06 * smooth_noise(n, 40))
    ph = np.cumsum(f) / SR
    saw = (ph % 1.0) - 0.5
    grip = np.clip(0.8 + 0.3 * smooth_noise(n, 25), 0, 1)                        # stutters: catches and slips
    tone = reson(saw, 2300, 400) + 0.7 * reson(saw, 4400, 700) + 0.4 * high(saw, 600)
    env = np.clip(s / 0.012, 0, 1) * np.clip((0.24 - s) / 0.05, 0, 1)
    return (tone * grip + 0.05 * band(rng.standard_normal(n), 3000, 8000)) * env


def scrape():
    """A sole dragged across the mat: hundreds of tiny stick-slip releases, plus the grit."""
    s = t(0.4)
    n = len(s)
    rate = 350 + 250 * np.sin(np.pi * s / 0.4)                                   # faster mid-drag
    events = (rng.random(n) < rate / SR) * rng.uniform(0.3, 1.0, n)
    ticks = reson(events, 1600, 900) + 0.6 * reson(events, 3200, 1400)
    grit = band(rng.standard_normal(n), 400, 4000) * 0.25
    return (ticks + grit) * np.sin(np.pi * s / 0.4) ** 2


# ---- voices ----------------------------------------------------------------------------------------------------------

VOWELS = {   # formants (Hz) and bandwidths for an adult male; women and boys are scaled up
    "u": ((320, 80), (870, 100), (2250, 140)),
    "o": ((540, 90), (850, 100), (2400, 140)),
    "a": ((730, 110), (1150, 120), (2500, 160)),
    "e": ((530, 90), (1850, 120), (2500, 160)),
    "h": ((700, 300), (1300, 350), (2600, 400)),
}


def glottal(f0):
    """The glottal flow derivative for a pitch track f0 (Hz per sample): a Rosenberg pulse, differentiated."""
    ph = np.cumsum(f0) / SR % 1.0
    open_ = 0.6
    flow = np.where(ph < open_, np.sin(np.pi * ph / open_) ** 2, 0.0)
    return np.diff(flow, prepend=0) * SR / np.maximum(f0, 50) / 6


def vocal(src, vowel, scale=1.0):
    return sum(g * reson(src, f * scale, bw * scale) for g, (f, bw) in zip((1.0, 0.6, 0.25), VOWELS[vowel]))


def voice(n, f0, vowels, mix, breath=0.15, scale=1.0, vibrato=5.0):
    """One voice: f0 track (array), vowels (two names) crossfaded by `mix` (0 = first, 1 = second)."""
    f = f0 * (1 + 0.012 * np.sin(2 * np.pi * vibrato * np.arange(n) / SR + rng.uniform(0, 6)) + 0.01 * smooth_noise(n, 15))
    src = glottal(f) + breath * high(rng.standard_normal(n), 800) * 0.3
    a, b = vocal(src, vowels[0], scale), vocal(src, vowels[1], scale)
    out = high((1 - mix) * a + mix * b, 160)
    return out + 1.2 * band(out, 1800, 4000, 2)                                    # the voice's presence (the singer's formant)


def person():
    """A random spectator: f0, formant scale, distance gain and the treble the distance takes away."""
    female = rng.random() < 0.4
    f0 = rng.uniform(170, 240) if female else rng.uniform(95, 145)
    scale = rng.uniform(1.12, 1.22) if female else rng.uniform(0.94, 1.04)
    dist = rng.uniform(4, 30)                                                     # metres from the mic
    return f0, scale, 4.0 / dist, max(1500.0, 9000.0 * 6 / dist)


def clap_track(n, start, stop, rate, gain):
    """One person applauding from `start` to `stop` s: each clap a short bandpassed burst (the hands' air pocket)."""
    x = np.zeros(n)
    hz = rng.uniform(900, 2400)
    clap = reson(rng.standard_normal(int(0.03 * SR)) * np.exp(-np.arange(int(0.03 * SR)) / (rng.uniform(0.004, 0.008) * SR)), hz, hz / 3)
    k = start + rng.uniform(0, 1 / rate)
    while k < stop:
        at(x, n, k, clap, gain * rng.uniform(0.6, 1.0))
        k += 1 / rate * rng.uniform(0.85, 1.15)
    return x


def hall(x, rt60=1.6, wet=0.25):
    """A baked hall for the crowd beds: exponentially decaying noise, the treble dying faster. The bed is first
    voiced like a crowd mic (the chest boom off, the 1-4 kHz consonants up)."""
    x = high(x, 220) + 0.9 * band(x, 1000, 4000, 2)
    m = int(rt60 * SR)
    s = np.arange(m) / SR
    ir = rng.standard_normal(m) * np.exp(-6.9 * s / rt60)
    ir = low(ir, 2500) * 0.7 + ir * np.exp(-6.9 * s / (rt60 * 0.35)) * 0.3
    ir[: int(0.02 * SR)] = 0
    ir /= np.sqrt(np.sum(ir ** 2))
    y = np.fft.irfft(np.fft.rfft(x, len(x) + m) * np.fft.rfft(ir, len(x) + m))[: len(x)]
    return (1 - wet) * x + wet * y * np.abs(x).max() / max(np.abs(y).max(), 1e-9)


def crowd_react():
    """~70 voices: a gasp of "ooh" rising, swelling into a cheer ("ah"/"yeah"), with applause underneath."""
    s = t(3.0)
    n = len(s)
    x = np.zeros(n)
    for _ in range(70):
        f0, scale, g, lp = person()
        onset = rng.uniform(0.0, 0.18)
        rise = np.clip((s - onset) / rng.uniform(0.4, 0.8), 0, 1)
        track = f0 * (1 + 0.5 * rise - 0.25 * np.clip(s - 1.5, 0, None))           # the pitch climbs with the cheer
        mix = np.clip((s - onset - rng.uniform(0.3, 0.6)) / 0.3, 0, 1)
        v = voice(n, track, ("u", rng.choice(["a", "e"])), mix, breath=0.4, scale=scale)
        env = np.clip((s - onset) / 0.08, 0, 1) * np.exp(-np.clip(s - onset - 0.5, 0, None) / rng.uniform(0.8, 1.6))
        x += low(v * env, lp) * g
    claps = sum(clap_track(n, rng.uniform(0.4, 0.8), rng.uniform(1.8, 2.9), rng.uniform(3.5, 6), rng.uniform(0.1, 0.4)) for _ in range(40))
    x = x / np.abs(x).max() + 0.3 * claps / max(np.abs(claps).max(), 1e-9) * np.clip((3.0 - s) / 0.6, 0, 1)
    return hall(x) * np.clip((3.0 - s) / 0.3, 0, 1)


def crowd_near():
    """The near miss: the "ooh" whose pitch sags into a groan ("oh"), nobody cheering."""
    s = t(2.4)
    n = len(s)
    x = np.zeros(n)
    for _ in range(55):
        f0, scale, g, lp = person()
        onset = rng.uniform(0.0, 0.12)
        track = f0 * (1 + 0.25 * np.clip((s - onset) / 0.25, 0, 1) - 0.4 * np.clip((s - onset - 0.3) / 1.2, 0, 1))
        mix = np.clip((s - onset - 0.35) / 0.5, 0, 1)
        v = voice(n, track, ("u", "o"), mix, breath=0.5, scale=scale)
        env = np.clip((s - onset) / 0.06, 0, 1) * np.exp(-np.clip(s - onset - 0.3, 0, None) / rng.uniform(0.6, 1.1))
        x += low(v * env, lp) * g
    return hall(x) * np.clip((2.4 - s) / 0.3, 0, 1)


def crowd_loop():
    """12 s of murmur: voices chatting in random vowel strings, scattered claps, a whistle, a cough. Loops seamlessly."""
    n = int(12 * SR)
    N = n + SR
    s = np.arange(N) / SR
    x = np.zeros(N)
    for _ in range(90):
        f0, scale, g, lp = person()
        talk = (smooth_noise(N, 3) > rng.uniform(-0.2, 0.4)).astype(float)         # talking / listening
        syll = np.clip(0.5 + 0.8 * np.sin(2 * np.pi * rng.uniform(3, 5.5) * s + rng.uniform(0, 6)), 0, 1)   # syllables
        track = f0 * (1 + 0.12 * smooth_noise(N, 2))
        v1, v2 = rng.choice(list("uoae"), 2, replace=False)
        mix = 0.5 + 0.5 * smooth_noise(N, 4)
        v = voice(N, track, (v1, v2), mix, breath=0.3, scale=scale)
        x += low(v * low(talk * syll, 30, 1), lp) * g * 0.7
    x /= np.abs(x).max()
    claps = np.zeros(N)
    for _ in range(14):                                                            # a few people clapping now and then
        a = rng.uniform(0, 11)
        claps += clap_track(N, a, a + rng.uniform(0.8, 2.5), rng.uniform(3, 5), rng.uniform(0.2, 0.5))
    whistle = np.zeros(N)
    a = 6.2
    w = t(0.9)
    whistle_one = np.sin(2 * np.pi * np.cumsum(2400 + 500 * np.sin(np.pi * w / 0.9)) / SR) * np.sin(np.pi * w / 0.9) ** 2
    at(whistle, N, a, low(whistle_one, 4000), 1.0)
    cough = np.zeros(N)
    c = t(0.25)
    for k, when in enumerate((2.7, 2.95)):
        burst = band(rng.standard_normal(len(c)), 250, 2500) * np.clip(c / 0.01, 0, 1) * np.exp(-c / 0.07)
        at(cough, N, when, vocal(burst, "a") * 0.6, 1.0 - 0.3 * k)
    y = x + 0.25 * claps / max(np.abs(claps).max(), 1e-9) + 0.04 * whistle + 0.08 * cough / max(np.abs(cough).max(), 1e-9)
    y = hall(y + 0.8 * band(y, 600, 2500, 2), rt60=1.8, wet=0.35)                  # chatter: the vowels' second formants
    out = y[:n].copy()
    fade = np.linspace(0, 1, SR)   # the second after the end fades into the start: the loop point is seamless
    out[:SR] = out[:SR] * fade + y[n:] * (1 - fade)
    return out


def breath():
    """Two pants of an out-of-breath old man, in and out, 1.6 s; the loop point sits in the silence between."""
    s = t(1.6)
    n = len(s)
    x = np.zeros(n)
    for k, start in enumerate((0.0, 0.8)):
        varn = 1.0 + 0.08 * (k - 0.5)
        tin = t(0.3 * varn)
        inhale = vocal(pink(len(tin)), "h") * np.sin(np.pi * tin / tin[-1]) ** 2 * 0.45
        inhale += 0.03 * reson(rng.standard_normal(len(tin)), 3200, 500) * np.sin(np.pi * tin / tin[-1]) ** 2   # the teeth
        at(x, n, start + 0.02, inhale)
        tex = t(0.42 * varn)
        env = np.clip(tex / 0.03, 0, 1) * np.exp(-tex / 0.16) * np.clip((tex[-1] - tex) / 0.05, 0, 1)
        exhale = vocal(pink(len(tex)), "h")
        grunt = vocal(glottal(np.full(len(tex), 105 * varn) * (1 - 0.15 * tex)), "a") * np.exp(-tex / 0.05) * 0.25   # a voiced edge
        at(x, n, start + 0.36, (exhale + grunt) * env)
    return high(x, 150) * np.clip(np.minimum(s, 1.6 - s) / 0.01, 0, 1)


def bell():
    """A boxing bell: a brass dome hit by a hammer three times. Each partial is a pair of modes a few Hz apart (the
    dome is not perfectly round), so the ring beats; the hammer adds a click."""
    s = t(2.6)
    one = np.zeros(len(s))
    for r, a, d, beat in ((1.0, 1.0, 1.1, 1.3), (2.32, 0.55, 0.7, 2.1), (4.25, 0.35, 0.35, 3.4), (6.63, 0.2, 0.18, 4.6), (9.38, 0.12, 0.1, 6.0)):
        f = 830 * r
        one += a * (np.sin(2 * np.pi * f * s) + 0.8 * np.sin(2 * np.pi * (f + beat) * s + 1.0)) * np.exp(-s / d)
    click = high(rng.standard_normal(len(s)), 2500) * np.exp(-s / 0.0015) * 2.5
    one = one + click
    x = np.zeros(len(s))
    for k, when in enumerate((0.0, 0.33, 0.66)):
        at(x, len(s), when, one, 1.0 - 0.1 * k)
    return x


if __name__ == "__main__":
    save("Slap", slap())
    for i, kw in enumerate((dict(pop_hz=1600, flesh_hz=190, skull_hz=100, crack=0.8, spread=0.005),   # a heavier, cupped palm
                            dict(pop_hz=2500, flesh_hz=240, skull_hz=125, crack=1.2, spread=0.002),   # a flat, stinging one
                            dict(pop_hz=2050, flesh_hz=220, skull_hz=115, crack=1.0, fingers=3)), 1):
        save(f"Slap{i}", slap(**kw))
    save("Thud", thud())
    save("Squeak0", squeak(1100))
    save("Squeak1", squeak(1500))
    save("Scrape", scrape())
    save("Breath", breath(), loop=True)
    save("CrowdReact", crowd_react())
    save("CrowdNear", crowd_near())
    save("Step", step())
    save("CrowdLoop", crowd_loop(), loop=True)
    save("Bell", bell())
