"""voices.json (scripts/voice_manifest.py): every card x 5 emotions x 5 levels x 5 takes, the real names, a voice profile
per card, flags that match the folders, and the staged copy identical to the source. Reads only; writes nothing."""

import json
import re
import sys

import pytest
from conftest import ROOT

sys.path.insert(0, str(ROOT / "scripts"))
import voice_manifest as vm

EMOTIONS = ["angry", "happy", "hurt", "insult", "celebrate"]
VOICE_KEYS = {"pitchHz", "formantScale", "breath", "rasp", "vibratoHz", "vibratoDepth", "tempo", "timbre"}


@pytest.fixture(scope="module")
def doc():
    return json.loads(vm.VOICES_JSON.read_text(encoding="utf-8"))


def test_staged_copy_is_identical(doc):
    assert vm.VOICES_STAGED.read_bytes() == vm.VOICES_JSON.read_bytes()


def test_every_card_every_cell(doc):
    assert doc["schema"] == "po-voice-manifest/1"
    assert doc["emotions"] == EMOTIONS and doc["levels"] == [1, 2, 3, 4, 5] and doc["fallback"] == "procedural"
    assert [c["id"] for c in doc["characters"]] == vm.cards()
    for c in doc["characters"]:
        assert sorted(c["clips"]) == sorted(EMOTIONS)
        for emotion, levels in c["clips"].items():
            assert [lv["level"] for lv in levels] == [1, 2, 3, 4, 5]
            for lv in levels:
                assert [x["take"] for x in lv["takes"]] == [1, 2, 3, 4, 5]
                for x in lv["takes"]:
                    up = emotion.upper()
                    assert x["asset"] == f"S_V_{c['id']}_{up}_{x['take']}_{lv['level']}"
                    assert x["file"] == f"{c['id']}/{up}_{x['take']}_{lv['level']}.wav"
                    assert re.fullmatch(r"S_V_[a-z0-9_]+_[A-Z]+_[1-5]_[1-5]", x["asset"])


def test_flags_match_the_folders(doc):
    for c in doc["characters"]:
        for levels in c["clips"].values():
            for lv in levels:
                for x in lv["takes"]:
                    # a generated placeholder is not a recording: never exists, so the game synthesises the line
                    assert (x["exists"] or x["placeholder"]) == (vm.OUT / x["file"]).exists(), x["file"]
                    assert not (x["exists"] and x["placeholder"]), x["file"]
                    assert x["imported"] == (vm.ASSETS / c["id"] / f"{x['asset']}.uasset").exists(), x["asset"]


def test_every_card_has_its_own_voice(doc):
    voices = [c["voice"] for c in doc["characters"]]
    for v in voices:
        assert VOICE_KEYS <= set(v)
    assert len({(v["pitchHz"], v["formantScale"], v["rasp"]) for v in voices}) == len(voices)


def test_manifest_is_current(doc):
    """A rebuild from the folders gives the committed file (run scripts/voice_manifest.py after adding a WAV)."""
    fresh = vm.build(doc)
    assert {**doc, "generated": None} == fresh
    assert str(vm.VOICES_JSON.relative_to(ROOT)).replace("\\", "/") == doc["root"] + "/voices.json"
