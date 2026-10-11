"""Shared pytest hooks.

Tests never see real credentials. Unless SOCIETYSIM_LIVE=1, `pytest_configure` (which runs
before any test module is imported) removes every real-service variable from os.environ and
sets PYTHON_DOTENV_DISABLED=1, so a `load_dotenv()` that walks up into a developer's real
`.env` (an agent worktree lives under the repo) can't put them back. Subprocesses inherit
both. Tests marked `live` call paid APIs or real databases and are skipped the same way.
"""

import os

import pytest

pytest_plugins = ["pytester"]

LIVE_ENV = "SOCIETYSIM_LIVE"

# Named in #334, plus anything shaped like a key, token, secret, DSN, webhook or endpoint.
SECRET_NAMES = {
    "NEON_DATABASE_URL",
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "GOOGLE_API_KEY",
    "HF_TOKEN",
    "HF_WRITE_TOKEN",
    "HUGGINGFACE_API_KEY",
    "HOSTED_VLLM_API_KEY",
    "TYPESAFE_API_KEY",
    "AMD_API_KEY",
    "ELEVENLABS_API_KEY",
    "TURNSTILE_SECRET_KEY",
    "DATABASE_URL",
}
SECRET_SUFFIXES = (
    "_KEY",
    "_TOKEN",
    "_SECRET",
    "_DATABASE_URL",
    "_WEBHOOK_URL",
    "_BASE_URL",
    "_API_BASE",
)
SECRET_PREFIXES = ("VITE_NEON_", "DATABRICKS_", "NEON_")


def live() -> bool:
    return os.environ.get(LIVE_ENV) == "1"


def is_secret(name: str) -> bool:
    upper = name.upper()
    return (
        upper in SECRET_NAMES
        or upper.endswith(SECRET_SUFFIXES)
        or upper.startswith(SECRET_PREFIXES)
    )


def isolate_env() -> list[str]:
    """Drop real-service variables and block .env loading. Returns the names dropped."""
    if live():
        return []
    dropped = sorted(name for name in os.environ if is_secret(name))
    for name in dropped:
        del os.environ[name]
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    return dropped


def pytest_configure(config):
    config._societysim_env_dropped = isolate_env()


def pytest_report_header(config):
    if live():
        return f"env guard: OFF ({LIVE_ENV}=1, real keys and .env are visible)"
    dropped = getattr(config, "_societysim_env_dropped", [])
    return f"env guard: on, {len(dropped)} real-service variable(s) removed, .env loading off"


def pytest_collection_modifyitems(config, items):
    if live():
        return
    skip = pytest.mark.skip(
        reason=f"live test: set {LIVE_ENV}=1 to run (real services, may spend money)"
    )
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)
