import asyncio
import json
from pathlib import Path

import pytest
from fakes import FakeClient
from typer.testing import CliRunner

from societysim.batch.runner import BatchMismatch, run_batch
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


async def test_crashed_game_is_reported_not_lost(tmp_path):
    class Crashy(PricedFake):
        async def complete(self, model, messages, response_model):
            if len(self.calls) == 0:
                self.calls.append(None)  # first game only
                raise RuntimeError("boom")
            return await super().complete(model, messages, response_model)

    res = await run_batch(cfg(max_concurrency=1), SPECS, Crashy(), runs_dir=tmp_path)
    assert res["status"] == "failed"
    assert list(res["failed"]) == ["t-g0000"]
    assert "boom" in res["failed"]["t-g0000"]
    assert len(res["finished"]) == 3
    meta = json.loads((tmp_path / "t/run_meta.json").read_text())
    assert meta["status"] == "failed" and "t-g0000" in meta["failed"]


async def test_budget_holds_with_concurrency(tmp_path):
    cost = (
        await run_batch(cfg(n_games=1, batch_id="probe"), SPECS, PricedFake(), runs_dir=tmp_path)
    )["spent_usd"]
    cap = cost * 1.5
    res = await run_batch(
        cfg(n_games=8, max_concurrency=4, budget_usd=cap), SPECS, PricedFake(), runs_dir=tmp_path
    )
    assert res["status"] == "budget_stopped"
    assert len(res["finished"]) == 1  # no estimate yet, so one probe game, then the cap bites
    assert res["spent_usd"] <= cap


async def test_interrupts_do_not_reset_spend(tmp_path):
    cost = (
        await run_batch(cfg(n_games=1, batch_id="probe"), SPECS, PricedFake(), runs_dir=tmp_path)
    )["spent_usd"]
    cap = cost * 2.2

    async def interrupted_run():
        client = PricedFake(delay=0.001)
        task = asyncio.create_task(
            run_batch(
                cfg(n_games=10, max_concurrency=1, budget_usd=cap), SPECS, client, runs_dir=tmp_path
            )
        )
        while len(client.calls) < 150:  # part-way through a game
            await asyncio.sleep(0.001)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return json.loads((tmp_path / "t/run_meta.json").read_text())["spent_usd"]

    first = await interrupted_run()
    second = await interrupted_run()
    assert second > first  # the cut-off game's money is kept, not forgotten
    res = await run_batch(
        cfg(n_games=10, max_concurrency=1, budget_usd=cap), SPECS, PricedFake(), runs_dir=tmp_path
    )
    assert (
        res["spent_usd"] <= cap + cost
    )  # same-priced models: a game is estimated from its own models, so at most one over


class SpreadFake(PricedFake):
    """Model b costs 100x model a per call."""

    async def complete(self, model, messages, response_model) -> CompletionResult:
        r = await super().complete(model, messages, response_model)
        scale = 100 if model.name == "b" else 1
        return r.model_copy(
            update={"usage": r.usage.model_copy(update={"cost_usd": CALL_COST * scale})}
        )


async def test_budget_with_100x_price_spread_and_concurrency(tmp_path):
    selfplay = {"mode": "selfplay", "n_games": 8, "max_concurrency": 4}
    probe = await run_batch(
        cfg(n_games=1, mode="selfplay", batch_id="probe", models=["a"]),
        SPECS,
        SpreadFake(),
        runs_dir=tmp_path,
    )
    cheap = probe["spent_usd"]
    cap = cheap * 6
    res = await run_batch(cfg(budget_usd=cap, **selfplay), SPECS, SpreadFake(), runs_dir=tmp_path)
    assert res["status"] == "budget_stopped"
    # g0 (a) runs alone, then g1 (b) runs alone with no estimate and alone blows the cap. The
    # remaining bound: a model's first game can cost more than the cap; nothing else is
    # allowed beside it, so overshoot is that one game (about 100 cheap games), not 4x it.
    assert len(res["finished"]) == 2
    assert res["spent_usd"] <= cap + cheap * 130


async def test_unreadable_run_meta_is_never_a_fresh_batch(tmp_path):
    await run_batch(cfg(n_games=1), SPECS, None, runs_dir=tmp_path)
    (tmp_path / "t/run_meta.json").write_text('{"fingerprint": "ab')  # torn write
    with pytest.raises(BatchMismatch, match="unreadable"):
        await run_batch(cfg(n_games=1), SPECS, None, runs_dir=tmp_path)


async def test_run_meta_written_via_replace(tmp_path):
    await run_batch(cfg(n_games=1), SPECS, None, runs_dir=tmp_path)
    assert not list((tmp_path / "t").glob("*.tmp"))


async def test_scripted_logs_do_not_satisfy_an_llm_resume(tmp_path):
    await run_batch(cfg(), SPECS, None, runs_dir=tmp_path)
    meta = json.loads((tmp_path / "t/t-g0000.run_meta.json").read_text())
    assert meta["agents"] == "scripted"
    client = PricedFake()
    with pytest.raises(BatchMismatch, match="scripted agents, not llm"):
        await run_batch(cfg(), SPECS, client, runs_dir=tmp_path)
    assert client.calls == []
    meta = json.loads((tmp_path / "t/run_meta.json").read_text())
    assert meta["agents"] == "scripted" and len(meta["skipped"]) == 0  # untouched


async def test_changed_registry_entry_refuses_resume(tmp_path):
    await run_batch(cfg(), SPECS, None, runs_dir=tmp_path)
    changed = SPECS | {"a": SPECS["a"].model_copy(update={"litellm_model": "openai/other"})}
    with pytest.raises(BatchMismatch, match="registry"):
        await run_batch(cfg(), changed, None, runs_dir=tmp_path)


async def test_resume_refuses_a_different_config(tmp_path):
    await run_batch(cfg(), SPECS, None, runs_dir=tmp_path)
    await run_batch(cfg(n_games=6, budget_usd=1, max_concurrency=2), SPECS, None, runs_dir=tmp_path)
    with pytest.raises(BatchMismatch):
        await run_batch(cfg(seed=1), SPECS, None, runs_dir=tmp_path)
    with pytest.raises(BatchMismatch):
        await run_batch(cfg(models=["b", "a"]), SPECS, None, runs_dir=tmp_path)


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
    assert "completed (scripted): 3 played, 0 skipped" in first.output
    assert len(list((tmp_path / "runs/cli").glob("*.jsonl"))) == 3
    second = RUNNER.invoke(app, ["batch", "--config", str(path), "--registry", str(REGISTRY)])
    assert "completed (scripted): 0 played, 3 skipped" in second.output


def test_cli_batch_dry_run_prints_plan_and_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = write_config(tmp_path, n_games=3)
    result = RUNNER.invoke(
        app, ["batch", "--config", str(path), "--registry", str(REGISTRY), "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert len(lines) == 4  # 3 games plus the total
    assert lines[0].startswith("cli-g0000 seed=") and "p0=gemma4-26b/" in lines[0]
    assert "wolf" in result.output and "3 games, estimated $" in lines[-1]
    assert not (tmp_path / "runs").exists()


def test_cli_batch_llm_after_scripted_exits_one(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOSTED_VLLM_API_KEY", "test-key")
    from societysim.models import client as client_module

    monkeypatch.setattr(client_module, "LiteLLMClient", lambda *, limits: PricedFake())
    path = write_config(tmp_path)
    args = ["batch", "--config", str(path), "--registry", str(REGISTRY)]
    assert RUNNER.invoke(app, args).exit_code == 0
    result = RUNNER.invoke(app, [*args, "--agents", "llm"])
    assert result.exit_code == 1, result.output
    assert "scripted agents, not llm" in result.output


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


def test_cli_batch_config_mismatch_exits_one(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    args = ["--registry", str(REGISTRY)]
    assert (
        RUNNER.invoke(app, ["batch", "--config", str(write_config(tmp_path)), *args]).exit_code == 0
    )
    other = RUNNER.invoke(app, ["batch", "--config", str(write_config(tmp_path, seed=9)), *args])
    assert other.exit_code == 1 and "different seed" in other.output


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


async def test_calls_per_game_cap_and_resume(tmp_path):
    one_game = {"max_concurrency": 1, "models": ["a"], "mode": "selfplay"}
    client = PricedFake(delay=0.001)
    await run_batch(
        cfg(n_games=1, max_calls_per_game=1, **one_game), SPECS, client, runs_dir=tmp_path
    )
    assert client.peak["openai"] == 1
    # The cap is not part of the fingerprint: changing it resumes the same batch.
    client = PricedFake(delay=0.001)
    res = await run_batch(
        cfg(n_games=2, max_calls_per_game=7, **one_game), SPECS, client, runs_dir=tmp_path
    )
    assert res["skipped"] == ["t-g0000"] and len(res["finished"]) == 1
    assert client.peak["openai"] > 1
