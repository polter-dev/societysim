import pytest

KEY_VARS = (
    "OPENAI_API_KEY",
    "HF_TOKEN",
    "GEMINI_API_KEY",
    "PLAY_OPENAI_API_KEY",
    "PLAY_HF_TOKEN",
    "PLAY_GEMINI_API_KEY",
)


@pytest.fixture(autouse=True)
def _restore_key_env(monkeypatch):
    """The startup key mapping writes os.environ; setenv records each var so it is restored."""
    for name in KEY_VARS:
        monkeypatch.setenv(name, "")
