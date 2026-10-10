import asyncio
import json
from pathlib import Path

import pytest
from fakes import FakeClient
from typer.testing import CliRunner

from societysim.batch.runner import run_batch
from societysim.cli import app
from societysim.config import BatchConfig, ModelSpec
from societysim.logging.events import EventType
from societysim.models.client import CompletionResult

PLAYERS = [f"p{i}" for i in range(7)]
SPECS = {
    "a": ModelSpec(name="a", litellm_model="openai/a"),
    "b": ModelSpec(name="b", litellm_model="gemini/b"),
}
CALL_COST = 0.001


class PricedFake(FakeClient):
    """FakeClient that charges per call and tracks peak in-flight calls per provider."""

    def __init__(self, delay: float = 0.0):
        super().__init__(players=PLAYERS)
        self.delay = delay
        self.live: dict[str, int] = {}
        self.peak: dict[str, int] = {}

    async def complete(self, model, messages, response_model) -> CompletionResult:
        p = model.provider
        self.live[p] = self.live.get(p, 0) + 1
        self.peak[p] = max(self.peak.get(p, 0), self.live[p])
        try:
            await asyncio.sleep(self.delay)
            r = await super().complete(model, messages, response_model)
        finally:
            self.live[p] -= 1
        return r.model_copy(update={"usage": r.usage.model_copy(update={"cost_usd": CALL_COST})})


def cfg(**kw) -> BatchConfig:
    base = {"batch_id": "t", "n_games": 4, "models": ["a", "b"], "budget_usd": 100, "game": {}}
    return BatchConfig.model_validate(base | kw)


def finished_logs(tmp_path: Path) -> list[Path]:
    return sorted((tmp_path / "t").glob("*.jsonl"))


def last_type(path: Path) -> str:
    return json.loads(path.read_text().splitlines()[-1])["type"]


async def test_mixed_batch_runs_all_games_and_writes_meta(tmp_path):
    client = PricedFake()
    res = await run_batch(
        cfg(), SPECS, client, runs_dir=tmp_path, provider_concurrency={"openai": 2, "gemini": 1}
    )
    assert res["status"] == "completed"
    assert len(res["finished"]) == 4
    logs = finished_logs(tmp_path)
    assert len(logs) == 4
    assert all(last_type(p) == EventType.GAME_END for p in logs)
    meta = json.loads((tmp_path / "t/run_meta.json").read_text())
    assert meta["status"] == "completed"
    assert meta["spent_usd"] == pytest.approx(client.calls.__len__() * CALL_COST)
    assert meta["not_started"] == []
    assert client.peak["openai"] <= 2
    assert client.peak["gemini"] <= 1


async def test_games_run_in_parallel(tmp_path):
    client = PricedFake(delay=0.001)
    await run_batch(
        cfg(max_concurrency=4, models=["a"], mode="selfplay"), SPECS, client, runs_dir=tmp_path
    )
    assert client.peak["openai"] > 7  # more than one game's seats in flight


async def test_budget_stops_new_games(tmp_path):
    client = PricedFake()
    # One game costs well over $0.001 * several hundred calls; allow roughly one and a half.
    one_game = await run_batch(
        cfg(n_games=1, batch_id="probe"), SPECS, PricedFake(), runs_dir=tmp_path
    )
    cost = one_game["spent_usd"]
    res = await run_batch(
        cfg(n_games=10, max_concurrency=1, budget_usd=cost * 2.5), SPECS, client, runs_dir=tmp_path
    )
    assert res["status"] == "budget_stopped"
    assert 1 <= len(res["finished"]) < 10
    assert res["spent_usd"] <= cost * 2.5
    assert len(res["finished"]) + len(res["not_started"]) == 10
    meta = json.loads((tmp_path / "t/run_meta.json").read_text())
    assert meta["status"] == "budget_stopped"
    assert meta["not_started"] == res["not_started"]


async def test_resume_skips_finished_games(tmp_path):
    first = await run_batch(cfg(), SPECS, PricedFake(), runs_dir=tmp_path)
    before = {p.name: p.read_bytes() for p in finished_logs(tmp_path)}
    # one game lost its tail (crash), one never existed
    victim = tmp_path / "t" / f"{first['finished'][0]}.jsonl"
    victim.write_text(victim.read_text().splitlines()[0] + "\n")
    gone = tmp_path / "t" / f"{first['finished'][1]}.jsonl"
    gone.unlink()

    client = PricedFake()
    res = await run_batch(cfg(), SPECS, client, runs_dir=tmp_path)
    assert sorted(res["finished"]) == sorted([first["finished"][0], first["finished"][1]])
    assert len(res["skipped"]) == 2
    after = {p.name: p.read_bytes() for p in finished_logs(tmp_path)}
    redone = {f"{g}.jsonl" for g in res["finished"]}
    assert {k: v for k, v in after.items() if k not in redone} == {
        k: v for k, v in before.items() if k not in redone
    }  # skipped games were not touched
    assert all(last_type(tmp_path / "t" / k) == EventType.GAME_END for k in redone)
    assert res["spent_usd"] > 0  # includes the skipped games' recorded cost


async def test_resume_with_nothing_left_makes_no_calls(tmp_path):
    await run_batch(cfg(), SPECS, PricedFake(), runs_dir=tmp_path)
    client = PricedFake()
    res = await run_batch(cfg(), SPECS, client, runs_dir=tmp_path)
    assert res["finished"] == [] and len(res["skipped"]) == 4
    assert client.calls == []


async def test_cancellation_stops_cleanly_and_rerun_resumes(tmp_path):
    client = PricedFake(delay=0.002)
    task = asyncio.create_task(
        run_batch(cfg(n_games=6, max_concurrency=2), SPECS, client, runs_dir=tmp_path)
    )
    while not client.calls:
        await asyncio.sleep(0.001)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    meta = json.loads((tmp_path / "t/run_meta.json").read_text())
    assert meta["status"] == "interrupted"
    assert len(meta["finished"]) < 6
    calls_at_cancel = len(client.calls)
    await asyncio.sleep(0.02)
    assert len(client.calls) == calls_at_cancel  # nothing keeps running

    res = await run_batch(cfg(n_games=6, max_concurrency=2), SPECS, PricedFake(), runs_dir=tmp_path)
    assert res["status"] == "completed"
    assert len(res["finished"]) + len(res["skipped"]) == 6
    assert all(last_type(p) == EventType.GAME_END for p in finished_logs(tmp_path))


async def test_scripted_batch_needs_no_client(tmp_path):
    res = await run_batch(cfg(n_games=3), SPECS, None, runs_dir=tmp_path)
    assert res["status"] == "completed" and res["spent_usd"] == 0


# --- CLI ---------------------------------------------------------------------------------

RUNNER = CliRunner()
REGISTRY = Path(__file__).resolve().parents[2] / "configs/models.yaml"


def write_config(tmp_path: Path, **kw) -> Path:
    path = tmp_path / "batch.yaml"
    body = {
        "batch_id": "cli",
        "n_games": 3,
        "models": ["gemma4-26b", "qwen3-235b"],
        "budget_usd": 5,
    } | kw
    path.write_text(json.dumps(body))
    return path


def test_cli_batch_scripted_and_resume(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = write_config(tmp_path)
    first = RUNNER.invoke(app, ["batch", "--config", str(path), "--registry", str(REGISTRY)])
    assert first.exit_code == 0, first.output
    assert "completed: 3 played, 0 skipped" in first.output
    assert len(list((tmp_path / "runs/cli").glob("*.jsonl"))) == 3
    second = RUNNER.invoke(app, ["batch", "--config", str(path), "--registry", str(REGISTRY)])
    assert "completed: 0 played, 3 skipped" in second.output


def test_cli_batch_keyboard_interrupt_exits_130(tmp_path, monkeypatch):
    async def interrupted(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("societysim.batch.runner.run_batch", interrupted)
    result = RUNNER.invoke(
        app, ["batch", "--config", str(write_config(tmp_path)), "--registry", str(REGISTRY)]
    )
    assert result.exit_code == 130
    assert "re-run to resume" in result.output


def test_cli_batch_unknown_model_and_missing_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bad = RUNNER.invoke(
        app,
        [
            "batch",
            "--config",
            str(write_config(tmp_path, models=["nope"])),
            "--registry",
            str(REGISTRY),
        ],
    )
    assert bad.exit_code == 1 and "unknown model 'nope'" in bad.output
    gone = RUNNER.invoke(app, ["batch", "--config", str(tmp_path / "x.yaml")])
    assert gone.exit_code == 1 and "config file not found" in gone.output
