import asyncio
import json
import os
from pathlib import Path

import pytest
from fakes import FakeClient
from typer.testing import CliRunner

from societysim.batch import runner
from societysim.batch.runner import LOCK_NAME, BatchLocked, run_batch
from societysim.cli import app
from societysim.config import BatchConfig, ModelSpec

REGISTRY = Path(__file__).resolve().parents[2] / "configs/models.yaml"
PLAYERS = [f"p{i}" for i in range(7)]
SPECS = {"a": ModelSpec(name="a", litellm_model="openai/a")}


def cfg() -> BatchConfig:
    return BatchConfig(
        batch_id="t",
        n_games=2,
        models=["a"],
        budget_usd=100,
        max_concurrency=1,
        max_invalid_rate=1.0,
        game={"n_players": 7},
    )


class Gated(FakeClient):
    """Holds the first model call until the test lets go."""

    def __init__(self):
        super().__init__(players=PLAYERS)
        self.started = asyncio.Event()
        self.go = asyncio.Event()

    async def complete(self, model, messages, response_model):
        self.started.set()
        await self.go.wait()
        return await super().complete(model, messages, response_model)


def snapshot(batch_dir):
    return {p.name: p.read_bytes() for p in batch_dir.iterdir() if p.name != LOCK_NAME}


async def test_second_runner_on_a_running_batch_raises_and_touches_nothing(tmp_path):
    client = Gated()
    first = asyncio.create_task(run_batch(cfg(), SPECS, client, runs_dir=tmp_path))
    await asyncio.wait_for(client.started.wait(), 10)
    before = snapshot(tmp_path / "t")
    assert before  # the first run has its game log and run_meta.json open

    with pytest.raises(BatchLocked, match=rf"already running, pid {os.getpid()}"):
        await run_batch(cfg(), SPECS, FakeClient(players=PLAYERS), runs_dir=tmp_path)
    with pytest.raises(BatchLocked):
        await run_batch(cfg(), SPECS, None, runs_dir=tmp_path)
    assert snapshot(tmp_path / "t") == before

    client.go.set()
    res = await first
    assert res["status"] == "completed" and len(res["finished"]) == 2


async def test_lock_is_released_when_the_run_ends(tmp_path):
    await run_batch(cfg(), SPECS, None, runs_dir=tmp_path)
    res = await run_batch(cfg(), SPECS, None, runs_dir=tmp_path)  # resume; lock file still there
    assert res["status"] == "completed" and len(res["skipped"]) == 2


async def test_lock_is_released_when_the_run_raises(tmp_path):
    (tmp_path / "t").mkdir()
    (tmp_path / "t/run_meta.json").write_text("not json")  # CorruptMeta, after the lock is taken
    with pytest.raises(Exception, match="unreadable"):
        await run_batch(cfg(), SPECS, None, runs_dir=tmp_path)
    (tmp_path / "t/run_meta.json").unlink()
    assert (await run_batch(cfg(), SPECS, None, runs_dir=tmp_path))["status"] == "completed"


async def test_lock_left_by_a_dead_process_does_not_block_resume(tmp_path):
    (tmp_path / "t").mkdir()
    (tmp_path / "t" / LOCK_NAME).write_text("99999999")  # SIGKILLed run: file stays, lock is gone
    res = await run_batch(cfg(), SPECS, None, runs_dir=tmp_path)
    assert res["status"] == "completed"
    assert json.loads((tmp_path / "t/run_meta.json").read_text())["status"] == "completed"


def test_cli_second_runner_exits_one_with_the_pid(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "b.yaml"
    path.write_text(
        json.dumps(
            {
                "batch_id": "t",
                "n_games": 2,
                "models": ["gemma4-26b", "qwen3-235b"],
                "budget_usd": 1,
                "game": {"n_players": 7},
            }
        )
    )
    out = tmp_path / "runs" / "t"
    out.mkdir(parents=True)
    with runner._batch_lock(out):
        res = CliRunner().invoke(
            app,
            ["batch", "--config", str(path), "--registry", str(REGISTRY)],
        )
    assert res.exit_code == 1, res.output
    assert f"already running, pid {os.getpid()}" in res.output
