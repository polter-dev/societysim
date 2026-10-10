"""Server tests run in dev mode with the per-client caps opened up, so the #280 tests can start
many games from one test client. test_security.py sets the real limits per test."""

import pytest

OPEN_LIMITS = {
    "PLAY_DEV_MODE": "1",
    "PLAY_RATE_PER_MIN": "100000",
    "PLAY_MAX_ACTIVE_PER_IP": "1000",
    "PLAY_MAX_GAMES_PER_IP_DAY": "1000",
    "PLAY_MAX_CONCURRENT": "1000",
}


@pytest.fixture(autouse=True)
def play_env(monkeypatch):
    for name in ("TURNSTILE_SECRET_KEY", "PLAY_ENABLED", "PLAY_ALLOWED_MODELS", "SPACE_HOST"):
        monkeypatch.delenv(name, raising=False)
    for name, value in OPEN_LIMITS.items():
        monkeypatch.setenv(name, value)
