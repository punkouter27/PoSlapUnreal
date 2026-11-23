"""voices.json: the manifest of every voice clip the game expects (schema po-voice-manifest/1).

    training/.venv/Scripts/python.exe scripts/voice_manifest.py        (or: scripts/voice_pack.py --manifest)

Every card in training/characters x 5 emotions x 5 levels x 5 takes, with the real names: the source WAV
Content/Art/Exports/Audio/Voice/<card>/<EMOTION>_<take>_<level>.wav and the asset it is imported as,
/Game/Project/Core/Audio/Voice/<card>/S_V_<card>_<EMOTION>_<take>_<level> (scripts/voice_import.py). Per take: whether
the WAV exists, whether it is a voice_pack.py placeholder, whether it is imported. Per card: the procedural voice profile
the game synthesises missing clips with (USlapVoiceWave, Source/PoSlapUnreal/SlapVoiceSynth.cpp); a profile already in the
file is kept, so it can be tuned by hand. The flags are written here, never by hand.

Written twice with the same bytes: Content/Art/Exports/Audio/Voice/voices.json (next to the WAVs) and
Content/Data/voices.json (staged as NonUFS, the copy a packaged build reads). No numpy needed.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
ROOT = Path(__file__).resolve().parents[1]
CARDS = ROOT / "training" / "characters"
OUT = ROOT / "Content" / "Art" / "Exports" / "Audio" / "Voice"
MANIFEST = OUT / "manifest.json"  # voice_pack.py's placeholder hashes
VOICES_JSON = OUT / "voices.json"
VOICES_STAGED = ROOT / "Content" / "Data" / "voices.json"
ASSETS = ROOT / "Content" / "Project" / "Core" / "Audio" / "Voice"
EMOTIONS = ("ANGRY", "HAPPY", "HURT", "INSULT", "CELEBRATE")
LEVELS = TAKES = 5

# per card: the procedural voice beyond f0 / formant scale / strain (those come from voice_pack.voice_of)
PROFILES = dict(
    grandpa=dict(breath=0.30, rasp=0.55, vibratoHz=4.6, vibratoDepth=0.025, tempo=0.9, timbre="old-male"),
    kim=dict(breath=0.20, rasp=0.15, vibratoHz=5.6, vibratoDepth=0.020, tempo=1.1, timbre="adult-female"),
    matt=dict(breath=0.18, rasp=0.45, vibratoHz=5.0, vibratoDepth=0.015, tempo=1.0, timbre="adult-male-deep"),
    mom=dict(breath=0.28, rasp=0.20, vibratoHz=5.3, vibratoDepth=0.025, tempo=0.95, timbre="adult-female-warm"),
    tong=dict(breath=0.12, rasp=0.05, vibratoHz=6.5, vibratoDepth=0.030, tempo=1.25, timbre="child"),
)


def cards():
    return sorted(p.stem for p in CARDS.glob("*.json") if not p.stem.endswith(("_skeleton", "_fingerprint")))


def guess_voice(card):
    """A voice from the card's height (voice_pack.voice_of's rule for an uncast card): f0, formant scale, strain."""
    h = float(json.loads((CARDS / f"{card}.json").read_text()).get("height_m", 1.75))
    f0 = min(max(110.0 * (1.75 / h) ** 2, 80.0), 230.0)
    return f0, min(max(1.0 + (f0 - 110.0) / 650.0, 0.92), 1.2), 0.6


def cast_voice():
    """voice_pack.voice_of (the cast voices: Grandpa, Kim, Matt, Mom, Tong) when numpy / scipy are there, else the guess."""
    try:
        from voice_pack import voice_of

        return voice_of
    except ImportError:
        return guess_voice


def voice_profile(card, voice_of=None):
    f0, scale, press = (voice_of or cast_voice())(card)
    extra = PROFILES.get(card) or dict(
        breath=0.22,
        rasp=round(0.5 * press, 2),
        vibratoHz=5.2,
        vibratoDepth=0.02,
        tempo=1.0,
        timbre="adult-male" if f0 < 150 else "adult-female" if f0 < 260 else "child",
    )
    return dict(pitchHz=round(float(f0), 1), formantScale=round(float(scale), 3), press=round(float(press), 2), **extra)


def build(previous=None, voice_of=None):
    """The manifest as the folders are now (`generated` left None)."""
    kept = {c.get("id"): c.get("voice") for c in (previous or {}).get("characters", [])}
    placeholders = json.loads(MANIFEST.read_text()).get("files", {}) if MANIFEST.exists() else {}
    characters = []
    for card in cards():
        name = json.loads((CARDS / f"{card}.json").read_text()).get("name", card)
        clips = {}
        for emotion in EMOTIONS:
            levels = []
            for level in range(1, LEVELS + 1):
                takes = []
                for take in range(1, TAKES + 1):
                    rel = f"{card}/{emotion}_{take}_{level}.wav"
                    asset = f"S_V_{card}_{emotion}_{take}_{level}"
                    exists = (OUT / rel).exists()
                    takes.append(
                        dict(
                            take=take,
                            asset=asset,
                            file=rel,
                            exists=exists,
                            placeholder=exists and bool(placeholders.get(rel, {}).get("placeholder")),
                            imported=(ASSETS / card / f"{asset}.uasset").exists(),
                        )
                    )
                levels.append(
                    dict(level=level, file=takes[0]["file"], exists=any(x["exists"] for x in takes), takes=takes)
                )
            clips[emotion.lower()] = levels
        voice = kept.get(card) or voice_profile(card, voice_of)
        characters.append(dict(id=card, name=str(name).upper(), voice=voice, clips=clips))
    return dict(
        schema="po-voice-manifest/1",
        game="PoSlapUnreal",
        generated=None,
        root="Content/Art/Exports/Audio/Voice",
        assetRoot="/Game/Project/Core/Audio/Voice",
        layout="file <card>/<EMOTION>_<take 1-5>_<level 1-5>.wav -> asset S_V_<card>_<EMOTION>_<take>_<level>",
        emotions=[e.lower() for e in EMOTIONS],
        levels=list(range(1, LEVELS + 1)),
        takes=list(range(1, TAKES + 1)),
        fallback="procedural",
        characters=characters,
    )


def write(voice_of=None):
    """Rewrite both copies; the timestamp moves only when the content changes. Prints the summary."""
    previous = json.loads(VOICES_JSON.read_text()) if VOICES_JSON.exists() else None
    doc = build(previous, voice_of)
    same = previous is not None and {**previous, "generated": None} == doc
    doc["generated"] = previous["generated"] if same else datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    text = json.dumps(doc, indent=1) + "\n"
    for path in (VOICES_JSON, VOICES_STAGED):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    print(summary(doc))
    return doc


def summary(doc):
    takes = [x for c in doc["characters"] for lv in c["clips"].values() for clip in lv for x in clip["takes"]]
    have = [x for x in takes if x["exists"]]
    recorded = sum(not x["placeholder"] for x in have)
    missing = []
    for c in doc["characters"]:
        for emotion, lv in c["clips"].items():
            n = sum(not x["exists"] for clip in lv for x in clip["takes"])
            if n:
                missing.append(f"{c['id']}/{emotion} x{n}")
    return (
        f"voices.json: {len(have)} of {len(takes)} clips have a WAV ({recorded} recordings, {len(have) - recorded}"
        f" placeholders), {sum(x['imported'] for x in takes)} imported; missing (synthesised in-game):"
        f" {', '.join(missing) or 'none'}"
    )


if __name__ == "__main__":
    write()
    sys.exit(0)
