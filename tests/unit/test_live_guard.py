"""Live (paid) tests must stay skipped unless SOCIETYSIM_LIVE=1, even with keys set."""

from pathlib import Path

TESTS = Path(__file__).resolve().parents[1]

TEST_FILE = """
import os
import pytest

@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("HF_TOKEN"), reason="HF_TOKEN is required")
def test_would_spend():
    raise AssertionError("live test ran without opt-in")

def test_plain():
    pass
"""


def _run(pytester, monkeypatch, live):
    # Fake values only: the inner test never calls anything, it just raises if it runs.
    monkeypatch.setenv("HF_TOKEN", "fake-token-not-real")
    monkeypatch.setenv("TYPESAFE_API_KEY", "fake-key-not-real")
    monkeypatch.delenv("SOCIETYSIM_LIVE", raising=False)
    if live is not None:
        monkeypatch.setenv("SOCIETYSIM_LIVE", live)
    monkeypatch.syspath_prepend(str(TESTS))
    pytester.makepyfile(TEST_FILE)
    pytester.makeini("[pytest]\nmarkers =\n    live: paid API test\n")
    return pytester.runpytest_inprocess("-p", "conftest", "-p", "no:cacheprovider")


def test_live_skipped_when_keys_set_but_not_opted_in(pytester, monkeypatch):
    result = _run(pytester, monkeypatch, None)
    result.assert_outcomes(passed=1, skipped=1)


def test_live_skipped_when_flag_is_not_one(pytester, monkeypatch):
    result = _run(pytester, monkeypatch, "0")
    result.assert_outcomes(passed=1, skipped=1)


def test_live_runs_when_opted_in(pytester, monkeypatch):
    result = _run(pytester, monkeypatch, "1")
    result.assert_outcomes(passed=1, failed=1)


def test_paid_tests_are_marked_live():
    for rel, name in [
        ("integration/test_cli_play.py", "test_real_hf_selfplay"),
        ("unit/test_jev.py", "test_real_jev_on_fixture"),
    ]:
        lines = (TESTS / rel).read_text(encoding="utf-8").splitlines()
        idx = next(i for i, ln in enumerate(lines) if f"def {name}(" in ln)
        assert "@pytest.mark.live" in lines[max(0, idx - 3) : idx], name


def test_marker_registered(pytestconfig):
    assert any(m.startswith("live:") for m in pytestconfig.getini("markers"))
