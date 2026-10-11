"""Live (paid) tests must stay skipped unless SOCIETYSIM_LIVE=1, even with keys set."""

import os
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


def _decorators(lines, idx):
    """The decorator lines directly above line `idx` (a def)."""
    start = idx
    while start > 0 and lines[start - 1].lstrip().startswith("@"):
        start -= 1
    return [ln.strip() for ln in lines[start:idx]]


def test_paid_tests_are_marked_live():
    for rel, name in [
        ("integration/test_cli_play.py", "test_real_hf_selfplay"),
        ("unit/test_jev.py", "test_real_jev_on_fixture"),
    ]:
        lines = (TESTS / rel).read_text(encoding="utf-8").splitlines()
        idx = next(i for i, ln in enumerate(lines) if f"def {name}(" in ln)
        assert "@pytest.mark.live" in _decorators(lines, idx), name


def test_real_neon_tests_are_marked_live():
    """Any test that skips on NEON_DATABASE_URL talks to the real database (#334)."""
    found = []
    for path in TESTS.rglob("test_*.py"):
        lines = path.read_text(encoding="utf-8").splitlines()
        for idx, line in enumerate(lines):
            if line.lstrip().startswith(("def test_", "async def test_")):
                decorators = _decorators(lines, idx)
                if any("NEON_DATABASE_URL" in d and "skipif" in d for d in decorators):
                    found.append(line.strip())
                    assert "@pytest.mark.live" in decorators, f"{path.name}: {line.strip()}"
    assert len(found) >= 4, found  # contract, security ledger, neon sink roundtrip and views


def test_marker_registered(pytestconfig):
    assert any(m.startswith("live:") for m in pytestconfig.getini("markers"))


ISOLATION_TEST = """
import os
import pytest
from dotenv import load_dotenv

load_dotenv()  # at import, like the CLI: walks up from this file to the fake parent .env


def test_env_has_no_neon_url():
    assert "NEON_DATABASE_URL" not in os.environ


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("NEON_DATABASE_URL"), reason="NEON_DATABASE_URL not set")
def test_real_neon():
    raise AssertionError("real-Neon test ran")
"""

# Unroutable on purpose: nothing here can reach a real database even if a test ran.
FAKE_DSN = "postgresql://fake:fake@127.0.0.1:9/fake-parent-env"


def _run_under_parent_env(pytester, monkeypatch, live):
    # Record NEON_DATABASE_URL and PYTHON_DOTENV_DISABLED so teardown restores both, whatever
    # the inner run's guard or load_dotenv() does to os.environ.
    monkeypatch.setenv("NEON_DATABASE_URL", "placeholder")
    monkeypatch.delenv("NEON_DATABASE_URL")
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "placeholder")
    monkeypatch.delenv("PYTHON_DOTENV_DISABLED")
    monkeypatch.delenv("SOCIETYSIM_LIVE", raising=False)
    if live:
        monkeypatch.setenv("SOCIETYSIM_LIVE", "1")
    else:
        monkeypatch.setenv("NEON_DATABASE_URL", FAKE_DSN)  # also exported in the shell
    (pytester.path / ".env").write_text(f"NEON_DATABASE_URL={FAKE_DSN}\n", encoding="utf-8")
    worktree = pytester.path / "worktree"
    worktree.mkdir()
    (worktree / "test_isolation.py").write_text(ISOLATION_TEST, encoding="utf-8")
    monkeypatch.chdir(worktree)
    monkeypatch.syspath_prepend(str(TESTS))
    pytester.makeini("[pytest]\nmarkers =\n    live: paid API test\n")
    return pytester.runpytest_inprocess(str(worktree), "-p", "conftest", "-p", "no:cacheprovider")


def test_parent_dir_env_is_invisible_to_tests(pytester, monkeypatch):
    result = _run_under_parent_env(pytester, monkeypatch, live=False)
    result.assert_outcomes(passed=1, skipped=1)
    assert "NEON_DATABASE_URL" not in os.environ


def test_parent_dir_env_is_found_when_opted_in(pytester, monkeypatch):
    """Control: with SOCIETYSIM_LIVE=1 the same fake .env loads, so the guard is what hides it."""
    result = _run_under_parent_env(pytester, monkeypatch, live=True)
    result.assert_outcomes(failed=2)
    result.stdout.fnmatch_lines(["*real-Neon test ran*"])
