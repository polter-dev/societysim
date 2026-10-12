"""SIGTERM and SIGHUP stop `societysim batch` like Ctrl-C: status 'interrupted', exit 128+n."""

import json
import os
import signal
from pathlib import Path

import pytest
from typer.testing import CliRunner

from societysim.batch import runner
from societysim.cli import app

REGISTRY = Path(__file__).resolve().parents[2] / "configs/models.yaml"


def write_config(tmp_path: Path) -> Path:
    path = tmp_path / "batch.yaml"
    path.write_text(
        json.dumps(
            {
                "batch_id": "sig",
                "n_games": 30,
                "models": ["gemma4-26b", "qwen3-235b"],
                "budget_usd": 5,
                "max_concurrency": 2,
                "game": {"n_players": 7},
            }
        )
    )
    return path


def test_batch_run_meta_records_the_runner_pid(tmp_path):
    import asyncio

    from societysim.config import BatchConfig, ModelSpec

    cfg = BatchConfig.model_validate(
        {"batch_id": "p", "n_games": 1, "models": ["a"], "budget_usd": 1, "game": {"n_players": 7}}
    )
    specs = {"a": ModelSpec(name="a", litellm_model="openai/a")}
    asyncio.run(runner.run_batch(cfg, specs, None, runs_dir=tmp_path))
    assert json.loads((tmp_path / "p" / "run_meta.json").read_text())["pid"] == os.getpid()


@pytest.mark.parametrize(("name", "code"), [("SIGTERM", 143), ("SIGHUP", 129)])
def test_batch_signal_saves_interrupted_and_exits_128_plus_signal(
    tmp_path, monkeypatch, name, code
):
    sig = getattr(signal, name, None)
    if sig is None:
        pytest.skip(f"no {name} on this platform")
    monkeypatch.chdir(tmp_path)
    real = runner.run_batch
    sent = []

    async def run_then_signal(*args, **kw):
        shown = kw["on_progress"]

        def hook(event):
            shown(event)
            if not sent:  # the signal arrives while games are still running
                sent.append(1)
                signal.raise_signal(sig)

        return await real(*args, **{**kw, "on_progress": hook})

    monkeypatch.setattr(runner, "run_batch", run_then_signal)
    # Stand-in for the default action (kill the process), so a missing handler fails the test
    # instead of killing pytest.
    original = signal.signal(sig, standin := lambda *_: None)
    try:
        result = CliRunner().invoke(
            app, ["batch", "--config", str(write_config(tmp_path)), "--registry", str(REGISTRY)]
        )
        after = signal.getsignal(sig)
    finally:
        signal.signal(sig, original)
    assert after is standin  # the handler is put back
    assert result.exit_code == code, result.output
    assert "re-run to resume" in result.output
    meta = json.loads((tmp_path / "runs/sig/run_meta.json").read_text())
    assert meta["status"] == "interrupted"
    assert len(meta["finished"]) < 30
