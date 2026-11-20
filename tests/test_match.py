"""A short seeded match on the shipped brains through the Python reference engine (training/match.py)."""

import pytest

pytest.importorskip("onnxruntime")

import match  # noqa: E402  (training/ is on pythonpath, see pyproject.toml)

RED = "models/brains/grandpa_final_vs_kim.onnx"
BLUE = "models/brains/kim_final_vs_grandpa.onnx"


@pytest.fixture(scope="module")
def played(monkeypatch_module):
    monkeypatch_module.chdir(match.HERE)
    return [
        match.play(RED, 0, log=False, blue=BLUE, character="grandpa", blue_character="kim", seed=5) for _ in range(2)
    ]


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


def test_match_finishes_without_a_blowup(played):
    res, turns, _ = played[0]
    assert res["type"] == "result"
    assert res["how"] != "blow-up"
    assert res["winner"] in ("red", "blue", "draw", None)
    assert turns, "no strike was played"


def test_match_is_deterministic(played):
    """The same seed plays the same match: what parity with Unreal and the browser relies on."""
    (r1, t1, _), (r2, t2, _) = played
    assert r1 == r2
    assert t1 == t2
