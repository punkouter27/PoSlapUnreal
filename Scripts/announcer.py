"""The ring announcer: every line the game calls out, as placeholder WAVs until real recordings exist.

    training/.venv/Scripts/python.exe Scripts/announcer.py        (Windows: uses the built-in speech engine)
    (then Scripts/voice_import.py imports them to /Game/Audio/Announcer/S_A_<key>)

Writes Content/Art/Exports/Audio/Announcer/<key>.wav (32 kHz mono). Each line is spoken by Windows' own speech engine
(System.Speech, the "Microsoft David" voice, SSML for the announcer's slow, lifted delivery), then put through an arena
PA: the horn's band (250 Hz - 6.5 kHz), a little drive, the far stands' slapback and broadcast_audio.py's hall.
A recording saved over a file under the same name is kept (manifest.json, as voice_pack.py).

Lines: general ones (round calls, knockdown, knockout, the final bell, a draw, "one more and it's over") and, per
character card in training/characters/, the introductions (red / blue corner), the winner by each way of winning, the
danger call ("<name> is in trouble!") and the tournament champion. ASlapHUD::Announce plays them by key, one at a time.
"""
import hashlib
import json
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly

sys.path.insert(0, str(Path(__file__).resolve().parent))
import broadcast_audio as ba   # noqa: E402
from broadcast_audio import SR, band, at   # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CARDS = ROOT / "training" / "characters"
OUT = ROOT / "Content" / "Art" / "Exports" / "Audio" / "Announcer"
MANIFEST = OUT / "manifest.json"
RATE = 32000
VOICE = "Microsoft David Desktop"


def say(text, rate="-15%", pitch="-2st", volume="loud", emph="strong"):
    return (f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-US">'
            f'<prosody rate="{rate}" pitch="{pitch}" volume="{volume}"><emphasis level="{emph}">{text}</emphasis></prosody></speak>')


def lines():
    out = {
        "welcome": say("Ladies and gentlemen... welcome to Po Slap!", rate="-20%"),
        "round1": say("Round one!", rate="-25%"),
        "round2": say("Round two!", rate="-25%"),
        "round3": say("Final round!", rate="-25%"),
        "knockdown": say("Knockdown!", rate="-20%", pitch="+1st"),
        "knockout": say("Knock out! It's all over!", rate="-20%", pitch="+2st"),
        "finalbell": say("That's the final bell! We go to the judges' scorecards.", rate="-12%"),
        "draw": say("After three rounds... this one is a draw!", rate="-15%"),
        "onemore": say("One more, and it's over!", rate="-10%", pitch="+1st"),
    }
    ways = {"ko": "by knock out", "ud": "by unanimous decision", "sd": "by split decision", "md": "by majority decision", "dec": "on the judges' scorecards"}
    for p in sorted(CARDS.glob("*.json")):
        if p.stem.endswith(("_skeleton", "_fingerprint")):
            continue
        card = json.loads(p.read_text())
        key, name = card["name"], card.get("display", card["name"]).title()
        out[f"red_{key}"] = say(f"In the red corner... {name}!", rate="-22%")
        out[f"blue_{key}"] = say(f"And in the blue corner... {name}!", rate="-22%")
        out[f"trouble_{key}"] = say(f"{name} is in trouble!", rate="-8%", pitch="+1st")
        out[f"champion_{key}"] = say(f"And the new tournament champion... {name}!", rate="-20%")
        for w, words in ways.items():
            out[f"win_{w}_{key}"] = say(f"Your winner, {words}... {name}!", rate="-18%")
    return out


def speak_all(ssml):
    """Every line through System.Speech in one PowerShell call; returns {key: raw wav path}."""
    tmp = Path(tempfile.mkdtemp(prefix="announcer_"))
    (tmp / "lines.json").write_text(json.dumps(ssml), encoding="utf-8")
    ps = f"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.SelectVoice('{VOICE}')
$lines = Get-Content -Raw -Encoding UTF8 '{tmp / "lines.json"}' | ConvertFrom-Json
foreach ($p in $lines.PSObject.Properties) {{
  $fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(44100, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
  $s.SetOutputToWaveFile((Join-Path '{tmp}' ($p.Name + '.wav')), $fmt)
  $s.SpeakSsml($p.Value)
}}
$s.SetOutputToNull()
"""
    subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps], check=True)
    return {k: tmp / f"{k}.wav" for k in ssml}


def pa(x):
    """The arena PA: the horn's band, a little drive, the far stands' slapback, the hall."""
    x = band(x, 250, 6500, 2) + 0.4 * band(x, 1800, 4000, 2)   # the horn's honk
    x = np.tanh(2.0 * x / (np.abs(x).max() + 1e-9)) / np.tanh(2.0)
    n = len(x) + int(1.6 * SR)
    y = np.zeros(n)
    at(y, n, 0.0, x)
    at(y, n, 0.13, band(x, 400, 3000, 2), 0.28)                 # the far stands
    at(y, n, 0.27, band(x, 500, 2500, 2), 0.12)
    y = ba.hall(y, rt60=1.8, wet=0.22)
    k = int(0.02 * SR)
    y[-k:] *= np.linspace(1, 0, k)
    return 0.9 * y / (np.abs(y).max() + 1e-9)


def read(path):
    with wave.open(str(path), "rb") as w:
        x = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(float) / 32767
        return x if w.getframerate() == SR else resample_poly(x, SR, w.getframerate())


def main():
    ba.rng = np.random.default_rng(11)
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {"files": {}}
    files = manifest.setdefault("files", {})
    ssml = lines()
    todo = {}
    for key, text in ssml.items():
        path, known = OUT / f"{key}.wav", files.get(f"{key}.wav")
        if path.exists() and (not known or not known.get("placeholder") or known.get("sha1") != hashlib.sha1(path.read_bytes()).hexdigest()):
            files[f"{key}.wav"] = {"placeholder": False}   # a recording: kept
            continue
        todo[key] = text
    raw = speak_all(todo) if todo else {}
    for key, src in raw.items():
        y = resample_poly(pa(read(src)), 320, 441)
        y = 0.9 * y / (np.abs(y).max() + 1e-9)
        path = OUT / f"{key}.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(RATE)
            w.writeframes((y * 32767).astype("<i2").tobytes())
        files[f"{key}.wav"] = {"placeholder": True, "sha1": hashlib.sha1(path.read_bytes()).hexdigest(), "text": text}
    manifest["files"] = dict(sorted(files.items()))
    MANIFEST.write_text(json.dumps(manifest, indent=1) + "\n")
    print(f"{len(raw)} placeholders written, {len(ssml) - len(todo)} recordings kept, {len(ssml)} lines")


if __name__ == "__main__":
    main()
