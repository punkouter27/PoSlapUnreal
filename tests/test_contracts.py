"""The CPU gates CI runs: cards / models / staging, the generated shared constants, asset naming."""

from conftest import ROOT, TRAINING


def test_cards_models_and_staging(run_script):
    """verify_cards.py: every card builds (into a temp folder) to the committed models, staging and brains line up."""
    res = run_script(TRAINING / "verify_cards.py")
    assert res.returncode == 0, res.stdout[-4000:] + res.stderr[-2000:]
    assert "ALL CARD / MODEL / STAGING CHECKS PASSED." in res.stdout


def test_generated_constants_are_current(run_script):
    """SlapShared.gen.h and Web/shared.gen.js are what gen_constants.py writes from constants.py + rules.json."""
    res = run_script(TRAINING / "gen_constants.py", "--check")
    assert res.returncode == 0, res.stdout + res.stderr


def test_three_engines_read_the_same_numbers(run_script):
    res = run_script(TRAINING / "verify_constants.py")
    assert res.returncode == 0, res.stdout[-4000:] + res.stderr[-2000:]


def test_asset_naming(run_script):
    res = run_script(ROOT / "scripts" / "check_naming.py")
    assert res.returncode == 0, res.stdout[-4000:] + res.stderr[-2000:]
