"""scripts/deploy-play-space.sh: the dry-run plan, and that no secret value is ever printed."""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/deploy-play-space.sh"
# The staging step pipes git archive into tar; the deploy runs from macOS or Linux CI.
no_windows = pytest.mark.skipif(sys.platform == "win32", reason="bash/tar staging")
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


def _git(repo, *args):
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True
    )


def _temp_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, repo / "scripts/deploy-play-space.sh")
    for rel, text in {
        "server/Dockerfile": "FROM x\n",
        "server/README.md": "card\n",
        "pyproject.toml": "[project]\n",
        "uv.lock": "",
        "LICENSE": "mit\n",
        ".dockerignore": ".env\n",
        "configs/models.yaml": "models: []\n",
        "configs/voices.yaml": "{}\n",
        "configs/mixed_v1.yaml": "not in the image\n",
        "schema/a.json": "{}\n",
        "neon/schema.sql": "create table x ();\n",
        "neon/notes.md": "not in the image\n",
        "src/societysim/__init__.py": "",
    }.items():
        f = repo / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "x")
    return repo


def _stage(repo, out):
    return subprocess.run(
        ["bash", str(repo / "scripts/deploy-play-space.sh"), "--stage-only", str(out)],
        capture_output=True,
        text=True,
        cwd=repo,
    )


@no_windows
def test_stage_has_tracked_allowlist_only_never_untracked_env(tmp_path):
    repo = _temp_repo(tmp_path)
    dummy = "DUMMY-SECRET-VALUE-9999"
    (repo / ".env").write_text(f"OPENAI_API_KEY={dummy}\n")
    (repo / "runs").mkdir()
    (repo / "runs/g.jsonl").write_text(dummy)
    (repo / "src/societysim/.env.local").write_text(dummy)
    (repo / "src/societysim/stray.py").write_text(dummy)  # untracked source file
    out = tmp_path / "stage"
    r = _stage(repo, out)
    assert r.returncode == 0, r.stderr
    staged = {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()}
    assert staged == {
        "Dockerfile",
        "README.md",
        "pyproject.toml",
        "uv.lock",
        "LICENSE",
        ".dockerignore",
        "configs/models.yaml",
        "configs/voices.yaml",
        "schema/a.json",
        "neon/schema.sql",
        "src/societysim/__init__.py",
    }
    assert dummy not in r.stdout + r.stderr
    assert not any(dummy in p.read_text() for p in out.rglob("*") if p.is_file())


@no_windows
def test_stage_uses_committed_content_not_working_tree(tmp_path):
    repo = _temp_repo(tmp_path)
    (repo / "pyproject.toml").write_text("UNCOMMITTED-EDIT\n")
    out = tmp_path / "stage"
    r = _stage(repo, out)
    assert r.returncode == 0, r.stderr
    assert (out / "pyproject.toml").read_text() == "[project]\n"


@no_windows
def test_image_ships_the_neon_schema(tmp_path):
    """The ledger reads neon/schema.sql at startup (#332): without it Neon "is down" and
    every game is refused. It must be staged and copied to where SCHEMA_PATH looks."""
    from societysim.logging.sinks.neon import SCHEMA_PATH

    rel = SCHEMA_PATH.relative_to(ROOT).as_posix()
    r = subprocess.run(
        ["bash", str(SCRIPT), "--stage-only", str(tmp_path / "s")],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert r.returncode == 0, r.stderr
    assert rel in r.stdout.split()
    # WORKDIR /app with src at /app/src, so the schema must land at /app/<rel>.
    dockerfile = (ROOT / "server/Dockerfile").read_text()
    assert re.search(rf"^COPY {re.escape(rel)} \./{re.escape(rel)}$", dockerfile, re.M)
    assert "COPY src ./src" in dockerfile


def test_dockerfile_cmd_target_imports_and_builds(monkeypatch):
    pytest.importorskip("fastapi")
    import importlib
    import json

    cmd = re.search(r"^CMD (\[.*\])$", (ROOT / "server/Dockerfile").read_text(), re.M)
    args = json.loads(cmd.group(1))
    assert args[:2] == ["uvicorn", "--factory"]
    module, _, attr = args[2].partition(":")
    monkeypatch.setenv("PLAY_DEV_MODE", "1")
    monkeypatch.delenv("NEON_DATABASE_URL", raising=False)
    factory = getattr(importlib.import_module(module), attr)
    assert callable(factory)
    assert factory().title


@no_windows
def test_stage_hard_fails_on_tracked_secret_looking_file(tmp_path):
    repo = _temp_repo(tmp_path)
    (repo / "schema/leak.pem").write_text("x")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "leak")
    r = _stage(repo, tmp_path / "s")
    assert r.returncode == 1
    assert "leak.pem" in r.stderr
