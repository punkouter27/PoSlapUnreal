"""Each character's voice pack: HAPPY, ANGRY, HURT, INSULT x 5 sounds x 5 levels (1 weakest .. 5 strongest).

    training/.venv/Scripts/python.exe Scripts/voice_pack.py [--card <name>] [--report]
    (then, in Unreal: UnrealEditor-Cmd.exe PoSlapUnreal.uproject -run=pythonscript -script="<abs>/Scripts/voice_import.py" -unattended)

Writes Content/Art/Exports/Audio/Voice/<card>/<EMOTION>_<sound>_<level>.wav (32 kHz mono 16-bit) for every character
card in training/characters/, 100 per card. A file that does not exist yet gets a synthesised placeholder in the
card's voice (broadcast_audio.py's glottal pulses through vowel formants: the same voices as Grunt_<card> and
Breath_<card>). A real recording takes a placeholder's place by being saved over it under the same name:
manifest.json keeps the hash of every placeholder written, and a file whose hash no longer matches is a recording
and is never touched again. Placeholders are deterministic (seeded per file), so a rerun rewrites the same bytes.

The four emotions (a sound = one of five takes; the level scales loudness, pitch, length, strain and syllables):
HAPPY: laughs and cheers - "ha-ha", "he-he", "ho-ho", a closed-mouth chuckle, a rising "yeah!".
ANGRY: a growl into a shout, a nasal "hmph", a roar, a barked "hey!", a strained "nngh-ah".
HURT: "ow" (a to u), "oof", "ugh", a sharp "ah!", a moan; a sob in the voice at levels 4-5.
INSULT: a taunt with no words yet (speech-shaped babble): the playground "na-na-na-NA-na", a jeering question, a
statement with the last word punched, a mocking laugh-taunt, fast trash talk.
Loudness is baked in (level 1 peaks at 0.30, level 5 at 0.94), so the game plays every file at one gain.
"""
import argparse
import hashlib
import json
import sys
import wave
import zlib
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly

sys.path.insert(0, str(Path(__file__).resolve().parent))
import broadcast_audio as ba   # noqa: E402  (the synth: glottal pulses, formants, filters; its rng is reseeded per file)
from broadcast_audio import SR, band, glottal, high, low, reson, smooth_noise, t, vocal   # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CARDS = ROOT / "training" / "characters"
OUT = ROOT / "Content" / "Art" / "Exports" / "Audio" / "Voice"
MANIFEST = OUT / "manifest.json"
EMOTIONS = ("HAPPY", "ANGRY", "HURT", "INSULT")
SOUNDS, LEVELS, RATE = 5, 5, 32000

# per card: f0 (Hz), formant scale, pressed (0..1); broadcast_audio's three plus Mom (a woman's voice, a little lower
# and rounder than Kim's). A card missing here gets one from its height (see voice_of).
VOICES = dict(ba.VOICES, mom=(196.0, 1.14, 0.55),
              tong=(330.0, 1.32, 0.45))   # Tong (2026-11-09): the baby costume, a high, bright, unstrained voice


def cards():
    return sorted(p.stem for p in CARDS.glob("*.json") if not p.stem.endswith(("_skeleton", "_fingerprint")))


def voice_of(card):
    if card in VOICES:
        return VOICES[card]
    c = json.loads((CARDS / f"{card}.json").read_text())
    h = float(c.get("height_m", 1.75))
    f0 = float(np.clip(110.0 * (1.75 / h) ** 2, 80.0, 230.0))   # a shorter body: a higher voice (a guess until it is cast)
    return f0, float(np.clip(1.0 + (f0 - 110.0) / 650.0, 0.92, 1.2)), 0.6


# ---- one syllable ----------------------------------------------------------------------------------------------------

def syllable(dur, f0a, f0b, va, vb, scale, press=0.3, breath=0.2, onset=None, growl=0.0, sob=0.0, curve=1.0):
    """One voiced syllable: the pitch glides f0a -> f0b (curve > 1: holds high, then drops), the vowel va -> vb;
    `press` buzzes the pulse (strain), `growl` adds a rough 30-55 Hz flutter, `sob` a 7 Hz break in the voice.
    onset: None, "h" (aspirated), "n" (nasal), "p" / "t" / "k" (a plosive burst), "s" (hiss), "f" (a lip hiss)."""
    rng = ba.rng
    s = t(dur)
    n = len(s)
    u = (s / dur) ** curve
    track = (f0a + (f0b - f0a) * u) * (1 + 0.015 * smooth_noise(n, 70) * (1 + 2 * press))   # jitter grows with strain
    src = glottal(track) * (1 + (0.1 + 0.2 * press) * smooth_noise(n, 50))              # shimmer
    peak = max(np.abs(src).max(), 1e-9)
    src = np.tanh(src * (1 + 5 * press) / peak) * peak / (1 + 2 * press)                # pressed: a buzzier pulse
    if growl > 0:
        src *= 1 - growl * (0.5 + 0.5 * np.sin(2 * np.pi * np.cumsum(rng.uniform(30, 55) + 8 * smooth_noise(n, 20)) / SR))
    src = src + breath * high(rng.standard_normal(n), 900) * 0.25 * peak
    mix = np.clip((s - 0.25 * dur) / (0.6 * dur), 0, 1)
    out = (1 - mix) * vocal(src, va, scale) + mix * vocal(src, vb, scale)
    env = np.clip(s / 0.015, 0, 1) * np.clip((dur - s) / min(0.06, 0.4 * dur), 0, 1)
    if sob > 0:
        env = env * (1 - sob * (0.5 + 0.5 * np.sin(2 * np.pi * 7 * s)))
    out = out * env
    lvl = np.abs(out).max() + 1e-9
    pre = None
    if onset == "h":
        k = int(0.05 * SR)
        pre = vocal(rng.standard_normal(k), va, scale) * np.sin(np.linspace(0, np.pi / 2, k)) * 0.35
    elif onset == "n":
        k = int(0.06 * SR)
        pre = reson(glottal(np.full(k, f0a)), 250 * scale, 80) * np.clip(np.arange(k) / (0.01 * SR), 0, 1) * 0.8
    elif onset in ("p", "t", "k"):
        lo, hi = {"p": (400, 1500), "t": (3000, 7000), "k": (1500, 3500)}[onset]
        k = int(0.035 * SR)
        burst = band(rng.standard_normal(k), lo, hi) * np.exp(-np.arange(k) / (0.006 * SR))
        pre = np.concatenate([np.zeros(int(0.02 * SR)), burst * 1.5])
    elif onset in ("s", "f"):
        k = int(0.09 * SR)
        lo, hi = (4000, 9000) if onset == "s" else (1500, 6000)
        pre = band(rng.standard_normal(k), lo, hi) * np.sin(np.linspace(0, np.pi, k)) ** 2 * 0.5
    if pre is None:
        return out
    pre = pre / (np.abs(pre).max() + 1e-9) * lvl * (0.5 if onset in ("h", "s", "f") else 0.8)
    return np.concatenate([pre, out])


def chain(parts, gaps):
    """Syllables in a row: parts[i] then gaps[i] s of silence (a negative gap overlaps them)."""
    total = sum(len(p) for p in parts) + int(SR * sum(max(g, 0) for g in gaps)) + SR // 10
    x = np.zeros(total)
    k = 0
    for p, g in zip(parts, gaps):
        x[k:k + len(p)] += p[:total - k]
        k = max(0, k + len(p) + int(g * SR))
    return np.trim_zeros(x, "b")


def huff(dur, scale, vowel="h", gain=0.6):
    """Breath forced out (a "hmph", the air after a laugh): turbulence through the mouth's formants."""
    s = t(dur)
    return vocal(ba.pink(len(s)), vowel, scale) * np.clip(s / 0.01, 0, 1) * np.exp(-s / (0.35 * dur)) * gain


# ---- the four emotions -----------------------------------------------------------------------------------------------
# k: the level as 0..1 (level 1 = 0, level 5 = 1); v: the take (0..4)

def happy(f0, sc, press, k, v):
    rng = ba.rng
    lift = 1.1 + 0.55 * k
    if v == 4:   # a cheer: "yeah!", rising, longer and higher with the level
        d = 0.3 + 0.45 * k
        return chain([syllable(d, f0 * lift * 0.9, f0 * lift * (1.15 + 0.3 * k), "e", "a", sc, 0.2 + 0.5 * k * press, 0.2, "h", curve=0.6)], [0])
    vowel, onset, low_ = {0: ("a", "h", 1.0), 1: ("e", "h", 1.12), 2: ("o", "h", 0.8), 3: ("u", "n", 0.95)}[v]
    count = 2 + round(4 * k) - (v == 3)
    parts, gaps = [], []
    for i in range(count):
        d = rng.uniform(0.09, 0.13) * (1.2 if v == 3 else 1.0)
        f = f0 * lift * low_ * (1 - 0.04 * i) * rng.uniform(0.97, 1.03)
        voice_ = vowel if not (v == 3 and k > 0.5 and i == count - 1) else "a"   # a chuckle that breaks into a laugh
        parts.append(syllable(d, f * 1.05, f * 0.92, voice_, voice_, sc, 0.15 + 0.4 * k * press, 0.35, onset if i else ("h" if v != 3 else "n")))
        gaps.append(rng.uniform(0.04, 0.07))
    if k > 0.6:   # out of breath after a big laugh
        parts.append(huff(0.25, sc, gain=0.4))
        gaps.append(0)
    return chain(parts, gaps)


def angry(f0, sc, press, k, v):
    rng = ba.rng
    lift = 0.9 + 0.65 * k
    strain = min(1.0, 0.5 + 0.5 * k + 0.2 * press)
    if v == 1:   # "hmph": a nasal huff; from level 3 a short shout after it
        parts = [chain([syllable(0.12, f0 * 0.95, f0 * 0.85, "u", "u", sc, 0.4, 0.4, "n"), huff(0.18 + 0.1 * k, sc, gain=0.8)], [-0.04])]
        if k >= 0.5:
            parts.append(syllable(0.25 + 0.2 * k, f0 * lift * 1.1, f0 * lift * 0.85, "a", "o", sc, strain, 0.25, "t", growl=0.3 * k))
        return chain(parts, [0.08, 0])
    if v == 3:   # a barked "hey!"
        return syllable(0.18 + 0.25 * k, f0 * lift * 1.2, f0 * lift * 0.95, "e", "e", sc, strain, 0.2, "h", growl=0.2 * k, curve=2.0)
    if v == 4:   # "nngh-ah": strained through the nose, then out
        a = syllable(0.18 + 0.1 * k, f0 * 0.95, f0 * 1.05, "u", "u", sc, strain, 0.15, "n", growl=0.25)
        b = syllable(0.2 + 0.35 * k, f0 * lift * 1.1, f0 * lift * 0.8, "a", "a", sc, strain, 0.3, None, growl=0.15 + 0.3 * k, curve=1.8)
        return chain([a, b], [0.02, 0])
    if v == 2:   # a roar: one long "rraaah"
        return syllable(0.35 + 0.65 * k, f0 * lift, f0 * lift * 0.75, "o", "a", sc, strain, 0.3, "h", growl=0.35 + 0.45 * k, curve=2.5)
    # v == 0: a growl into a shout ("grr - AH!")
    g = syllable(0.15 + 0.2 * k, f0 * 0.8, f0 * 0.85, "u", "o", sc, strain, 0.2, "k", growl=0.7)
    s_ = syllable(0.2 + 0.4 * k, f0 * lift * 1.15, f0 * lift * 0.8, "a", "a", sc, strain, 0.25, None, growl=0.2 + 0.3 * k, curve=2.0)
    return chain([g, s_], [rng.uniform(0.02, 0.06), 0])


def hurt(f0, sc, press, k, v):
    jump = 1.25 + 0.95 * k   # the pitch jumps up with the pain, then falls
    sob = 0.5 if k >= 0.75 else 0.0
    d = 0.18 + 0.6 * k
    if v == 0:   # "ow": a to u
        return syllable(d, f0 * jump, f0 * 0.85, "a", "u", sc, 0.3 + 0.5 * k, 0.3, None, sob=sob, curve=1.5)
    if v == 1:   # "oof": the air knocked out
        return chain([syllable(0.1 + 0.15 * k, f0 * jump * 0.85, f0 * 0.8, "u", "u", sc, 0.5, 0.4, "h"), huff(0.12 + 0.2 * k, sc, "u", 0.9)], [-0.02, 0])
    if v == 2:   # "ugh": pressed, into breath
        return chain([syllable(0.12 + 0.3 * k, f0 * jump * 0.8, f0 * 0.75, "a", "o", sc, 0.6 + 0.4 * k, 0.3, None, growl=0.3), huff(0.15, sc, gain=0.5)], [-0.03, 0])
    if v == 3:   # a sharp "ah!"
        return syllable(0.12 + 0.45 * k, f0 * jump * 1.1, f0 * 0.9, "a", "a", sc, 0.4 + 0.5 * k, 0.25, "h", sob=sob, curve=3.0)
    # v == 4: a moan ("nnngh - ah"), long and wavering
    a = syllable(0.15 + 0.2 * k, f0 * 1.05, f0 * 1.15, "u", "u", sc, 0.5, 0.2, "n")
    b = syllable(d, f0 * jump * 0.9, f0 * 0.7, "o", "a", sc, 0.4 + 0.4 * k, 0.35, None, sob=max(sob, 0.3))
    return chain([a, b], [0.0, 0])


TAUNT = (1.0, 0.84, 1.12, 1.0, 0.84)   # "na-na-na-NA-na": sol, mi, la, sol, mi


def insult(f0, sc, press, k, v):
    rng = ba.rng
    lift = 1.0 + 0.45 * k
    strain = 0.15 + 0.6 * k * (0.5 + 0.5 * press)
    cons, vows = ("p", "t", "k", "n", "s", "h", None), ("a", "e", "o", "u")
    if v == 0:   # the playground taunt, sung; louder and pushed harder up the levels
        parts = [syllable(0.16 if i != 3 else 0.26, f0 * lift * m, f0 * lift * m * 0.98, "a", "a", sc, strain, 0.15, "n") for i, m in enumerate(TAUNT)]
        return chain(parts, [0.03] * 5)
    count = (3 + round(3 * k)) if v != 4 else (6 + round(4 * k))
    rate = 0.13 if v != 4 else 0.08
    parts, gaps = [], []
    for i in range(count):
        last = i == count - 1
        u = i / max(count - 1, 1)
        if v == 1:     # a jeering question: down, then up at the end ("you wanna GO?")
            m = 1.0 - 0.15 * u + (0.45 if last else 0.0)
        elif v == 2:   # a statement, the last word punched ("you're DONE")
            m = 1.0 - 0.2 * u + (0.3 if last else 0.0)
        elif v == 3:   # a mocking laugh-taunt: alternating high/low
            m = 1.15 if i % 2 else 0.9
        else:          # fast trash talk: a flat, rattling line
            m = 1.0 + 0.08 * rng.standard_normal()
        a = rng.choice(vows)
        d = rate * rng.uniform(0.85, 1.2) * (1.8 if last and v in (1, 2) else 1.0)
        f = f0 * lift * m
        bend = 1.12 if last and v == 1 else 0.95
        parts.append(syllable(d, f, f * bend, a, rng.choice(vows) if d > 0.15 else a, sc,
                              strain + (0.25 if last and v == 2 else 0.0), 0.2, rng.choice(cons), curve=1.0 if not last else 0.7))
        gaps.append(rng.uniform(0.015, 0.05))
    return chain(parts, gaps)


MAKERS = dict(HAPPY=happy, ANGRY=angry, HURT=hurt, INSULT=insult)


def render(card, emotion, sound, level):
    """One placeholder (1-based sound and level), as int16 bytes at RATE."""
    seed = zlib.crc32(f"{card}/{emotion}/{sound}/{level}".encode())
    ba.rng = np.random.default_rng(seed)   # the synth's functions read the module's rng
    f0, sc, press = voice_of(card)
    k = (level - 1) / (LEVELS - 1)
    x = MAKERS[emotion](f0, sc, press, k, sound - 1)
    x = high(x, 80) + 0.6 * k * band(x, 1800, 4000, 2)                      # a shout's presence
    if k > 0.5:                                                            # a strong one clips the throat a little
        x = np.tanh(x * (1 + 2 * (k - 0.5)) / (np.abs(x).max() + 1e-9))
    x = low(x, 11000)
    tail = int(0.012 * SR)
    x[-tail:] *= np.linspace(1, 0, tail)
    y = resample_poly(x, 320, 441)
    y = (0.30 + 0.16 * (level - 1)) * y / (np.abs(y).max() + 1e-9)        # the level's loudness, baked in
    return (y * 32767).astype("<i2").tobytes()


def write_wav(path, frames):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(frames)


def sha1(path):
    return hashlib.sha1(path.read_bytes()).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--card", action="append", help="only these cards (default: every card in training/characters)")
    ap.add_argument("--report", action="store_true", help="a listening page in SCREENSHOTS/<YYMMDD>_voice_pack/")
    a = ap.parse_args()
    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {"files": {}}
    files = manifest.setdefault("files", {})
    summary = {}
    for card in a.card or cards():
        made = kept = 0
        for emotion in EMOTIONS:
            for sound in range(1, SOUNDS + 1):
                for level in range(1, LEVELS + 1):
                    rel = f"{card}/{emotion}_{sound}_{level}.wav"
                    path = OUT / rel
                    known = files.get(rel)
                    if path.exists() and (not known or not known.get("placeholder") or known.get("sha1") != sha1(path)):
                        files[rel] = {"placeholder": False}   # a recording: never overwritten
                        kept += 1
                        continue
                    write_wav(path, render(card, emotion, sound, level))
                    files[rel] = {"placeholder": True, "sha1": sha1(path)}
                    made += 1
        summary[card] = (made, kept)
        print(f"{card:10s} {made:3d} placeholders, {kept:3d} recordings  (f0 {voice_of(card)[0]:.0f} Hz)")
    manifest["layout"] = "<card>/<EMOTION>_<sound 1-5>_<level 1-5>.wav; level 1 weakest, 5 strongest"
    manifest["emotions"] = list(EMOTIONS)
    manifest["files"] = dict(sorted(files.items()))
    MANIFEST.write_text(json.dumps(manifest, indent=1) + "\n")
    if a.report:
        report(summary)


def report(summary):
    """A listening page: one row per card x emotion x sound, five players (the levels) per row."""
    import shutil
    import time
    dest = ROOT / "SCREENSHOTS" / f"{time.strftime('%y%m%d')}_voice_pack"
    (dest / "audio").mkdir(parents=True, exist_ok=True)
    rows = []
    for card in summary:
        rows.append(f"<h2>{card} <small>f0 {voice_of(card)[0]:.0f} Hz</small></h2><table><tr><th></th>"
                    + "".join(f"<th>L{lv}</th>" for lv in range(1, LEVELS + 1)) + "</tr>")
        for emotion in EMOTIONS:
            for sound in range(1, SOUNDS + 1):
                cells = []
                for level in range(1, LEVELS + 1):
                    name = f"{card}_{emotion}_{sound}_{level}.wav"
                    shutil.copyfile(OUT / card / f"{emotion}_{sound}_{level}.wav", dest / "audio" / name)
                    cells.append(f"<td><audio controls preload=none src='audio/{name}'></audio></td>")
                rows.append(f"<tr><th>{emotion} {sound}</th>{''.join(cells)}</tr>")
        rows.append("</table>")
    (dest / "index.html").write_text(
        "<!doctype html><meta charset='utf-8'><title>Voice pack</title><style>body{font:14px system-ui;margin:20px;background:#111;color:#eee}"
        "td,th{padding:2px 6px;text-align:left}audio{height:28px;width:150px}small{color:#999}</style>"
        "<h1>Voice pack placeholders</h1><p>HAPPY, ANGRY, HURT, INSULT &times; 5 sounds &times; 5 levels (1 weakest, 5 strongest). "
        "Synthesised by Scripts/voice_pack.py; a recording saved over a file replaces it.</p>" + "".join(rows), encoding="utf-8")
    print("report:", dest / "index.html")


if __name__ == "__main__":
    main()
