"""scripts/deploy-play-space.sh: the dry-run plan, and that no secret value is ever printed."""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/deploy-play-space.sh"
SECRET_VALUES = {
    "PLAY_OPENAI_API_KEY": "sk-openai-VALUE-1111",
    "PLAY_HF_TOKEN": "hf_VALUE_2222",
    "PLAY_GEMINI_API_KEY": "gem-VALUE-3333",
    "NEON_DATABASE_URL": "postgres://user:pw-VALUE-4444@host/db",
    "TURNSTILE_SECRET_KEY": "0x-VALUE-5555",
    "HF_TOKEN": "hf_DEPLOY_VALUE_6666",
}


def _run(*args, secrets=True, extra=None):
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash not found on PATH")
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PLAY_", "HF_", "NEON_"))}
    env.pop("TURNSTILE_SECRET_KEY", None)
    if secrets:
        env.update(SECRET_VALUES)
    env.update(extra or {})
    return subprocess.run(
        [bash, str(SCRIPT), *args], capture_output=True, text=True, env=env, cwd=ROOT
    )


def test_dry_run_plan_maps_names_and_hides_values():
    r = _run("--dry-run")
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "polter-dev/societysim-play" in out
    for line in (
        "OPENAI_API_KEY  (from PLAY_OPENAI_API_KEY)",
        "HF_TOKEN  (from PLAY_HF_TOKEN)",
        "GEMINI_API_KEY  (from PLAY_GEMINI_API_KEY)",
        "NEON_DATABASE_URL",
        "TURNSTILE_SECRET_KEY",
    ):
        assert line in out
    for value in SECRET_VALUES.values():
        assert value not in out
    assert "nothing uploaded" in out


def test_dry_run_sets_proxy_and_allowlist_variables():
    out = _run("--dry-run").stdout
    assert "PLAY_TRUSTED_PROXIES=10.0.0.0/8,172.16.0.0/12,192.168.0.0/16" in out
    assert "PLAY_ALLOWED_MODELS=qwen3-235b," in out


def test_dry_run_passes_limits_and_flags_missing_required():
    r = _run("--dry-run", secrets=False, extra={"PLAY_DAILY_BUDGET_USD": "1.5"})
    assert r.returncode == 0
    assert "PLAY_DAILY_BUDGET_USD=1.5" in r.stdout
    assert "MISSING  NEON_DATABASE_URL" in r.stdout
    assert "MISSING  TURNSTILE_SECRET_KEY" in r.stdout


def test_real_run_refuses_without_required_secrets():
    r = _run(secrets=False)
    assert r.returncode == 1
    assert "NEON_DATABASE_URL" in r.stderr


def test_allowed_models_are_priced():
    """Every model the Space allows has a price, or the ledger would refuse its calls."""
    import litellm

    from societysim.models.registry import load_registry
    from societysim.setup import REGISTRY

    allowed = re.search(r'DEFAULT_MODELS="([^"]+)"', SCRIPT.read_text()).group(1).split(",")
    reg = load_registry(REGISTRY)
    for name in allowed:
        spec = reg.get(name)
        if spec.input_cost_per_mtok is None or spec.output_cost_per_mtok is None:
            in_cost, out_cost = litellm.cost_per_token(
                model=spec.litellm_model, prompt_tokens=1000, completion_tokens=1000
            )
            assert in_cost > 0 and out_cost > 0, name
