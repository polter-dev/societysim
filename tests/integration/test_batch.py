import asyncio
import json
import os
import re
from pathlib import Path

import pytest
from fakes import FakeClient
from typer.testing import CliRunner

from societysim.agents.schemas import (
    StatementResponse,
    TargetResponse,
    VoteResponse,
    WolfChatResponse,
)
from societysim.batch import runner
from societysim.batch.runner import PROVIDER_DOWN_AFTER, BatchMismatch, run_batch
from societysim.cli import app
from societysim.config import BatchConfig, ModelSpec
from societysim.engine.state import NAME_POOLS
from societysim.logging.events import EventType
from societysim.models.client import CompletionResult, Usage

PLAYERS = [f"p{i}" for i in range(7)]
SPECS = {
    "a": ModelSpec(name="a", litellm_model="openai/a"),
    "b": ModelSpec(name="b", litellm_model="gemini/b"),
}
CALL_COST = 0.001
# Prior per seat = 12 calls * 2000 tokens * $1/Mtok = $0.024, so a 7-seat game is about $0.17.
PRICED = {
    n: ModelSpec(name=n, litellm_model=f"{p}/{n}", input_cost_per_mtok=1, output_cost_per_mtok=0)
    for n, p in [("a", "openai"), ("b", "gemini"), ("c", "openai"), ("d", "gemini")]
}


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
    base = {
        "batch_id": "t",
        "n_games": 4,
        "models": ["a", "b"],
        "budget_usd": 100,
        "game": {"n_players": 7},
    }
    # The plain fakes answer invalid replies, so the breaker is off unless a test sets it.
    return BatchConfig.model_validate(base | {"max_invalid_rate": 1.0} | kw)


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


async def test_batch_names_match_each_models_avatar(tmp_path):
    specs = {
        "a": SPECS["a"].model_copy(update={"avatar_presentation": "feminine"}),
        "b": SPECS["b"].model_copy(update={"avatar_presentation": "masculine"}),
    }
    await run_batch(cfg(n_games=2), specs, PricedFake(), runs_dir=tmp_path)
    for log in finished_logs(tmp_path):
        seats = json.loads(log.read_text().splitlines()[0])["meta"]["seats"]
        for seat in seats.values():
            assert seat["name"] in NAME_POOLS[specs[seat["model"]].avatar_presentation]
    # Names change what a game says, not what it is: a resumed batch keeps its fingerprint.
    assert runner.fingerprint(cfg(), specs, "llm") == runner.fingerprint(cfg(), SPECS, "llm")


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
    assert res["write_errors"] == {}


async def test_lost_event_writes_are_counted_in_batch_meta(tmp_path, monkeypatch, caplog):
    class Broken:
        def write(self, s):
            raise OSError("disk says no")

    real_write = runner.JsonlSink.write

    async def write(self, event):  # game t-g0001 loses its event seq 3
        if event.game_id == "t-g0001" and event.seq == 3:
            f, self._f = self._f, Broken()
            try:
                return await real_write(self, event)
            finally:
                self._f = f
        return await real_write(self, event)

    monkeypatch.setattr(runner.JsonlSink, "write", write)
    res = await run_batch(cfg(n_games=2), SPECS, None, runs_dir=tmp_path)
    assert res["status"] == "completed" and res["write_errors"] == {"t-g0001": 1}
    meta = json.loads((tmp_path / "t/run_meta.json").read_text(encoding="utf-8"))
    assert meta["write_errors"] == {"t-g0001": 1}
    game = json.loads((tmp_path / "t/t-g0001.run_meta.json").read_text(encoding="utf-8"))
    assert game["write_errors"] == 1 and game["agents"] == "scripted"
    assert "t-g0001: 1 events missing" in caplog.text

    monkeypatch.setattr(runner.JsonlSink, "write", real_write)
    res = await run_batch(cfg(n_games=2), SPECS, None, runs_dir=tmp_path)
    assert res["write_errors"] == {"t-g0001": 1}  # a finished game keeps its count on resume
    (tmp_path / "t/t-g0001.jsonl").unlink()
    res = await run_batch(cfg(n_games=2), SPECS, None, runs_dir=tmp_path)
    assert res["finished"] == ["t-g0001"] and res["write_errors"] == {}  # replayed: fresh log


async def test_resume_skips_a_finished_cp1252_log(tmp_path):
    # Before T230 Windows wrote logs in cp1252 with \r\n; resume must not replay (and overwrite)
    # such a finished, paid-for game.
    await run_batch(cfg(n_games=2), SPECS, None, runs_dir=tmp_path)
    log = tmp_path / "t/t-g0000.jsonl"
    events = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
    say = next(e for e in events if e["type"] == "statement")
    say["public_text"] = "it’s p3 — trust me"
    text = "".join(json.dumps(e, ensure_ascii=False) + "\r\n" for e in events)
    log.write_bytes(text.encode("cp1252"))
    old_bytes = log.read_bytes()
    res = await run_batch(cfg(n_games=2), SPECS, None, runs_dir=tmp_path)
    assert res["skipped"] == ["t-g0000", "t-g0001"] and res["finished"] == []
    assert log.read_bytes() == old_bytes
    assert runner.game_costs(log) is not None


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
        await run_batch(cfg(n_games=1, batch_id="probe"), PRICED, PricedFake(), runs_dir=tmp_path)
    )["spent_usd"]
    cap = cost * 1.5
    res = await run_batch(
        cfg(n_games=8, max_concurrency=4, budget_usd=cap), PRICED, PricedFake(), runs_dir=tmp_path
    )
    assert res["status"] == "budget_stopped"
    assert len(res["finished"]) == 1  # cap is 1.5x one game: one fits, a second would bust it
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
        PRICED,
        SpreadFake(),
        runs_dir=tmp_path,
    )
    cheap = probe["spent_usd"]
    cap = cheap * 6
    res = await run_batch(cfg(budget_usd=cap, **selfplay), PRICED, SpreadFake(), runs_dir=tmp_path)
    assert res["status"] == "budget_stopped"
    # Fresh models are estimated from the prior, so the first games start together; b's game
    # costs 100x its prior, which alone blows the cap. Overshoot is that one game (about 100
    # cheap games); once its cost is seen, no further game starts.
    assert len(res["finished"]) < 8
    assert res["not_started"]
    assert res["spent_usd"] <= cap + cheap * 130


async def test_unreadable_run_meta_is_never_a_fresh_batch(tmp_path):
    await run_batch(cfg(n_games=1), SPECS, None, runs_dir=tmp_path)
    (tmp_path / "t/run_meta.json").write_text('{"fingerprint": "ab')  # torn write
    with pytest.raises(BatchMismatch, match="unreadable"):
        await run_batch(cfg(n_games=1), SPECS, None, runs_dir=tmp_path)


async def test_run_meta_written_via_replace(tmp_path):
    await run_batch(cfg(n_games=1), SPECS, None, runs_dir=tmp_path)
    assert not list((tmp_path / "t").glob("*.tmp"))


def locked_replace(monkeypatch, locked):
    """os.replace raises PermissionError when locked(dst, n) is true; n counts calls so far."""
    real, calls = os.replace, []

    def fake(src, dst):
        n = len(calls)
        calls.append(Path(dst).name)
        if locked(Path(dst), n):
            raise PermissionError(5, "Access is denied")
        real(src, dst)

    monkeypatch.setattr(runner.os, "replace", fake)
    monkeypatch.setattr(runner, "REPLACE_BACKOFF", (0.0,) * 6)
    return calls


def test_replace_retries_then_raises(tmp_path, monkeypatch):
    src, dst = tmp_path / "a.tmp", tmp_path / "a"
    src.write_text("x")
    calls = locked_replace(monkeypatch, lambda d, n: n < 3)
    runner._replace(src, dst)
    assert dst.read_text() == "x" and len(calls) == 4
    src.write_text("y")
    calls.clear()
    locked_replace(monkeypatch, lambda d, n: True)
    with pytest.raises(PermissionError):
        runner._replace(src, dst)
    assert dst.read_text() == "x"


def test_replace_gives_up_after_a_bounded_number_of_tries(tmp_path, monkeypatch):
    (tmp_path / "a.tmp").write_text("x")
    calls = locked_replace(monkeypatch, lambda d, n: True)
    with pytest.raises(PermissionError):
        runner._replace(tmp_path / "a.tmp", tmp_path / "a")
    assert len(calls) == len(runner.REPLACE_BACKOFF) + 1
    assert sum(runner.REPLACE_BACKOFF) <= 2.5


async def test_run_meta_locked_for_a_while_is_retried(tmp_path, monkeypatch):
    calls = locked_replace(monkeypatch, lambda d, n: d.name == "run_meta.json" and n < 3)
    res = await run_batch(cfg(n_games=2), SPECS, None, runs_dir=tmp_path)
    assert res["status"] == "completed" and res["failed"] == {}
    meta = json.loads((tmp_path / "t/run_meta.json").read_text())
    assert meta["status"] == "completed" and calls.count("run_meta.json") > 3


async def test_stuck_run_meta_never_fails_a_finished_game(tmp_path, monkeypatch):
    # The first save goes through. Then the ledger and every game's run_meta tag stay locked
    # until the end of the run, when the final save succeeds.
    n_meta = 0

    def locked(dst, n):
        nonlocal n_meta
        if dst.name == "run_meta.json":
            n_meta += 1
            return 1 < n_meta < 30
        return dst.name.endswith(".run_meta.json")

    locked_replace(monkeypatch, locked)
    res = await run_batch(cfg(n_games=2, max_concurrency=1), SPECS, PricedFake(), runs_dir=tmp_path)
    assert res["failed"] == {} and len(res["finished"]) == 2
    assert all(last_type(p) == EventType.GAME_END for p in finished_logs(tmp_path))


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
        "game": {"n_players": 7},
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


async def test_fresh_models_start_in_parallel_from_the_prior(tmp_path):
    client = PricedFake(delay=0.001)
    res = await run_batch(
        cfg(n_games=4, max_concurrency=4, mode="selfplay", models=["a", "b", "c", "d"]),
        PRICED,
        client,
        runs_dir=tmp_path,
    )
    assert res["status"] == "completed"
    assert max(client.peak.values()) > 7  # several fresh-model games ran at once


class Spans(PricedFake):
    """PricedFake that records when each game makes its first and last call."""

    def __init__(self):
        super().__init__(delay=0.002)
        self.span: dict[str, list[int]] = {}
        self.tick = 0

    async def complete(self, model, messages, response_model) -> CompletionResult:
        from societysim.batch.runner import _game

        game = _game.get()
        self.tick += 1
        self.span.setdefault(game, [self.tick, self.tick])[1] = self.tick
        r = await super().complete(model, messages, response_model)
        self.tick += 1
        self.span[game][1] = self.tick
        return r

    def peak_games(self) -> int:
        marks = [(a, 1) for a, _ in self.span.values()] + [(b, -1) for _, b in self.span.values()]
        live = peak = 0
        for _, d in sorted(marks):
            live += d
            peak = max(peak, live)
        return peak


NINE = {
    f"m{i}": ModelSpec(
        name=f"m{i}", litellm_model=f"openai/m{i}", input_cost_per_mtok=1, output_cost_per_mtok=0
    )
    for i in range(9)
}


async def test_nine_unseen_priced_models_fill_max_concurrency(tmp_path):
    client = Spans()
    res = await run_batch(
        cfg(n_games=9, max_concurrency=4, models=list(NINE)), NINE, client, runs_dir=tmp_path
    )
    assert res["status"] == "completed"
    assert client.peak_games() == 4  # all four slots filled from the prior, none over


async def test_nine_unseen_priced_models_still_respect_the_budget(tmp_path):
    client = Spans()
    # A game's prior is about $0.168, so $0.40 reserves for two at once, not four.
    res = await run_batch(
        cfg(n_games=9, max_concurrency=4, models=list(NINE), budget_usd=0.4),
        NINE,
        client,
        runs_dir=tmp_path,
    )
    assert client.peak_games() == 2
    assert res["spent_usd"] <= 0.4


async def test_unpriced_model_game_runs_alone_until_it_has_finished_once(tmp_path):
    specs = {
        "free": ModelSpec(name="free", litellm_model="openai/free"),
        "paid": NINE["m0"].model_copy(update={"name": "paid"}),
    }
    client = Spans()
    res = await run_batch(
        cfg(n_games=6, max_concurrency=4, models=["free", "paid"]),
        specs,
        client,
        runs_dir=tmp_path,
    )
    assert res["status"] == "completed"
    first = client.span["t-g0000"]
    others = [v for g, v in client.span.items() if g != "t-g0000"]
    assert all(first[1] < v[0] for v in others)  # nothing overlapped the first game
    assert client.peak_games() > 1  # then it ran in parallel


async def test_prior_over_budget_still_stops_new_games(tmp_path):
    client = PricedFake()
    res = await run_batch(
        cfg(
            n_games=4,
            max_concurrency=4,
            mode="selfplay",
            models=["a", "b", "c", "d"],
            budget_usd=0.25,
        ),
        PRICED,
        client,
        runs_dir=tmp_path,
    )
    assert res["status"] == "budget_stopped"
    assert 1 <= len(res["finished"]) < 4
    assert res["not_started"]


# --- provider failures -------------------------------------------------------------------

OUTAGE = (
    "APIError: 402 out of credits at https://router.example/v1 key hf_abcdefghijklmnopqrstuvwxyz"
)


class Outage(PricedFake):
    """Model `down` errors with no tokens once `after` calls are made, or only on every
    `every`-th call. bad_json=True answers garbage with tokens instead."""

    def __init__(self, down="b", after=0, every=0, bad_json=False, delay=0.0):
        super().__init__(delay=delay)
        self.down, self.after, self.every, self.bad_json = down, after, every, bad_json

    async def complete(self, model, messages, response_model) -> CompletionResult:
        n = len(self.calls)
        if model.name != self.down or n < self.after or (self.every and n % self.every):
            return await super().complete(model, messages, response_model)
        self.calls.append((model, messages, response_model))
        await asyncio.sleep(self.delay)
        if self.bad_json:
            usage = Usage(model=model.name, input_tokens=100, output_tokens=20)
            return CompletionResult(parsed=None, raw="nope", error="bad json", usage=usage)
        return CompletionResult(parsed=None, raw="", error=OUTAGE, usage=Usage(model=model.name))


async def test_provider_down_mid_batch_stops_new_games_and_rerun_replays(tmp_path):
    client = Outage(after=300, delay=0.001)
    c = cfg(n_games=8, max_concurrency=1)
    res = await run_batch(c, SPECS, client, runs_dir=tmp_path)
    assert res["status"] == "failed"
    assert res["failed"] and res["not_started"]
    assert all("ProviderDown" in why for why in res["failed"].values())
    [down] = res["provider_down"]
    assert down.startswith("b: ") and "402 out of credits" in down
    assert "http" not in down and "hf_abc" not in down  # no URLs or keys
    meta = json.loads((tmp_path / "t/run_meta.json").read_text())
    assert meta["status"] == "failed" and meta["failed"] == res["failed"]

    again = await run_batch(c, SPECS, PricedFake(), runs_dir=tmp_path)
    assert again["status"] == "completed"
    assert set(res["failed"]) | set(res["not_started"]) <= set(again["finished"])
    assert len(again["finished"]) + len(again["skipped"]) == 8


async def test_provider_down_inside_a_gather_leaves_no_stray_calls(tmp_path, monkeypatch):
    monkeypatch.setattr("societysim.batch.runner.PROVIDER_DOWN_AFTER", 1)
    # a's first failure trips it inside the night's parallel calls, with siblings in flight
    client = Outage(down="a", delay=0.005)
    res = await run_batch(cfg(n_games=2, max_concurrency=1), SPECS, client, runs_dir=tmp_path)
    assert res["status"] == "failed" and len(res["not_started"]) == 1
    assert asyncio.all_tasks() == {asyncio.current_task()}  # siblings were cancelled
    calls = len(client.calls)
    await asyncio.sleep(0.02)
    assert len(client.calls) == calls


async def test_occasional_provider_errors_do_not_stop_the_batch(tmp_path):
    client = Outage(every=3)
    res = await run_batch(cfg(), SPECS, client, runs_dir=tmp_path)
    assert res["status"] == "completed" and res["provider_down"] == []
    assert len(res["finished"]) == 4


async def test_bad_json_never_counts_as_provider_down(tmp_path):
    res = await run_batch(cfg(), SPECS, Outage(bad_json=True), runs_dir=tmp_path)
    assert res["status"] == "completed" and res["provider_down"] == []
    assert len(res["finished"]) == 4


async def test_down_after_exactly_n_failures(tmp_path):
    from societysim.agents.schemas import VoteResponse
    from societysim.batch.runner import MeteredClient, ProviderDown

    metered = MeteredClient(Outage(down="a"))
    for _ in range(PROVIDER_DOWN_AFTER - 1):
        await metered.complete(SPECS["a"], [], VoteResponse)
    await metered.complete(SPECS["b"], [], VoteResponse)  # other models keep their own count
    with pytest.raises(ProviderDown, match="^a: "):
        await metered.complete(SPECS["a"], [], VoteResponse)


def test_cli_batch_provider_down_says_which_and_how_to_resume(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOSTED_VLLM_API_KEY", "test-key")
    from societysim.models import client as client_module

    monkeypatch.setattr(
        client_module, "LiteLLMClient", lambda *, limits: Outage(down="qwen3-235b", after=100)
    )
    path = write_config(tmp_path, n_games=4, max_concurrency=1)
    args = ["batch", "--config", str(path), "--registry", str(REGISTRY), "--agents", "llm"]
    result = RUNNER.invoke(app, args)
    assert result.exit_code == 1, result.output
    assert "provider down: qwen3-235b: " in result.output
    assert f"re-run once it is back to resume: {Path('runs') / 'cli'}" in result.output


def test_cli_batch_dry_run_warns_once_per_unpriced_model(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "societysim.batch.runner.prior_seat_cost",
        lambda spec: 0.0 if spec.name == "qwen3-235b" else 1.0,
    )
    path = write_config(tmp_path, n_games=3)
    result = RUNNER.invoke(
        app, ["batch", "--config", str(path), "--registry", str(REGISTRY), "--dry-run"]
    )
    assert result.exit_code == 0, result.output
    assert result.output.count("warning: qwen3-235b is unpriced") == 1
    assert "gemma4-26b is unpriced" not in result.output


async def test_progress_callback_counts_fresh_run_and_resume(tmp_path):
    events: list[dict] = []
    await run_batch(cfg(n_games=3), SPECS, None, runs_dir=tmp_path, on_progress=events.append)
    assert [e["done"] for e in events] == [1, 2, 3]
    assert {e["total"] for e in events} == {3} and {e["event"] for e in events} == {"finished"}
    assert events[0]["eta_seconds"] is not None and events[-1]["eta_seconds"] == 0
    assert events[-1]["running"] == 0 and events[0]["winner"]
    # Drop one game's log; the resume counts the 2 skipped ones.
    (tmp_path / "t/t-g0002.jsonl").unlink()
    events.clear()
    await run_batch(cfg(n_games=3), SPECS, None, runs_dir=tmp_path, on_progress=events.append)
    assert [(e["done"], e["total"]) for e in events] == [(3, 3)]


async def test_progress_reports_failed_game_and_budget_stop(tmp_path):
    class Crashy(PricedFake):
        async def complete(self, model, messages, response_model):
            if not self.calls:
                self.calls.append(None)
                raise RuntimeError("boom")
            return await super().complete(model, messages, response_model)

    events: list[dict] = []
    await run_batch(
        cfg(max_concurrency=1), SPECS, Crashy(), runs_dir=tmp_path, on_progress=events.append
    )
    assert events[0]["event"] == "failed" and "boom" in events[0]["reason"]
    assert [e["done"] for e in events] == [1, 2, 3, 4]

    events.clear()
    await run_batch(
        cfg(batch_id="b", n_games=5, max_concurrency=1, budget_usd=0.01),
        SPECS,
        PricedFake(),
        runs_dir=tmp_path,
        on_progress=events.append,
    )
    assert events[-1]["event"] == "stopped" and events[-1]["reason"] == "budget"


class ClockFake(PricedFake):
    """Every model call takes `tick` fake seconds on the clock the runner reads."""

    def __init__(self, clock: dict[str, float], tick: float = 1.0):
        super().__init__()
        self.clock, self.tick = clock, tick

    async def complete(self, model, messages, response_model) -> CompletionResult:
        self.clock["t"] += self.tick
        return await super().complete(model, messages, response_model)


def _fake_clock(monkeypatch) -> dict[str, float]:
    clock = {"t": 1000.0}
    monkeypatch.setattr(runner, "_now", lambda: clock["t"])
    return clock


async def test_eta_is_remaining_games_over_observed_finish_rate(tmp_path, monkeypatch):
    clock = _fake_clock(monkeypatch)
    events: list[dict] = []

    def seen(e):
        events.append({**e, "elapsed": clock["t"] - 1000.0})

    await run_batch(
        cfg(n_games=4, max_concurrency=1),
        SPECS,
        ClockFake(clock),
        runs_dir=tmp_path,
        on_progress=seen,
    )
    # Serial: after the first game the ETA is one game's time per game left.
    assert events[0]["eta_seconds"] == pytest.approx(events[0]["seconds"] * 3)
    for e in events:
        assert e["eta_seconds"] == pytest.approx(e["elapsed"] / e["done"] * (4 - e["done"]))
    assert events[-1]["eta_seconds"] == 0


async def test_eta_uses_finish_rate_with_concurrency_not_per_game_time(tmp_path, monkeypatch):
    clock = _fake_clock(monkeypatch)
    events: list[dict] = []

    def seen(e):
        events.append({**e, "elapsed": clock["t"] - 1000.0})

    await run_batch(
        cfg(n_games=6, max_concurrency=3),
        SPECS,
        ClockFake(clock),
        runs_dir=tmp_path,
        on_progress=seen,
    )
    for e in events:
        assert e["eta_seconds"] == pytest.approx(
            e["elapsed"] / e["done"] * (e["total"] - e["done"])
        )
    assert events[-1]["eta_seconds"] == 0


async def test_eta_is_none_until_a_game_finishes_and_skips_resumed_games(tmp_path, monkeypatch):
    clock = _fake_clock(monkeypatch)

    class Boom(PricedFake):
        async def complete(self, model, messages, response_model):
            raise RuntimeError("boom")

    events: list[dict] = []
    await run_batch(
        cfg(n_games=3, max_concurrency=1),
        SPECS,
        Boom(),
        runs_dir=tmp_path,
        on_progress=events.append,
    )
    assert {e["event"] for e in events} == {"failed"}
    assert [e["eta_seconds"] for e in events] == [None, None, None]  # no pace to go on

    # Resume with 3 of 6 games on disk: only the games played now set the pace.
    await run_batch(
        cfg(batch_id="r", n_games=6, max_concurrency=1), SPECS, ClockFake(clock), runs_dir=tmp_path
    )
    for n in (3, 4, 5):
        (tmp_path / f"r/r-g000{n}.jsonl").unlink()
    clock["t"] += 500
    events.clear()
    await run_batch(
        cfg(batch_id="r", n_games=6, max_concurrency=1),
        SPECS,
        ClockFake(clock),
        runs_dir=tmp_path,
        on_progress=events.append,
    )
    assert [e["done"] for e in events] == [4, 5, 6]
    # If the 3 skipped games counted as finished, these would be a third of this.
    assert events[0]["eta_seconds"] == pytest.approx(events[0]["seconds"] * 2)
    assert events[1]["eta_seconds"] == pytest.approx(
        (events[0]["seconds"] + events[1]["seconds"]) / 2
    )
    assert events[2]["eta_seconds"] == 0


async def test_eta_only_counts_running_games_once_new_games_are_barred(tmp_path, monkeypatch):
    clock = _fake_clock(monkeypatch)
    events: list[dict] = []
    await run_batch(
        cfg(n_games=8, models=["a", "b", "c", "d"], max_concurrency=3, budget_usd=0.4),
        PRICED,
        ClockFake(clock),
        runs_dir=tmp_path,
        on_progress=events.append,
    )
    assert events[-1]["event"] == "stopped" and events[-1]["reason"] == "budget"
    ends = [e for e in events if e["event"] == "finished"]
    # Fewer games ran than were asked for; the ones that will never start are not owed.
    assert ends[-1]["done"] < ends[-1]["total"]
    assert ends[-1]["running"] == 0 and ends[-1]["eta_seconds"] == 0


def test_cli_progress_line_says_estimating_until_there_is_an_eta():
    from societysim.cli import _progress_line

    e = {
        "event": "failed", "reason": "boom", "done": 1, "total": 3, "game_id": "g", "seconds": 5,
        "spent": 0, "budget": 5, "running": 2, "eta_seconds": None,
    }  # fmt: skip
    assert _progress_line(e).endswith("| ETA estimating…")
    assert _progress_line({**e, "eta_seconds": 600}).endswith("| ETA ~10m")


def test_cli_batch_prints_a_progress_line_per_game(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = write_config(tmp_path)
    args = ["batch", "--config", str(path), "--registry", str(REGISTRY)]
    out = RUNNER.invoke(app, args).output
    for n in (1, 2, 3):
        assert f"[{n}/3] cli-g000{n - 1} finished (" in out
    assert "spent $0.00 of $5.00" in out and "ETA" in out


class HealthyFake(PricedFake):
    """Valid replies built from the legal ids in the prompt; models in `bad` answer garbage."""

    def __init__(self, bad=(), provider_down=()):
        super().__init__()
        self.bad = set(bad)
        self.provider_down = set(provider_down)  # answer with an exhausted-retries 503

    async def complete(self, model, messages, response_model) -> CompletionResult:
        if model.name in self.provider_down:
            usage = Usage(model=model.name, input_tokens=100, output_tokens=20, attempts=6)
            return CompletionResult(
                parsed=None,
                raw="",
                error="ServiceUnavailableError: HF is down",
                provider_error=True,
                usage=usage,
            )
        if model.name in self.bad:
            usage = Usage(model=model.name, input_tokens=100, output_tokens=20)
            return CompletionResult(parsed=None, raw="nope", error="bad json", usage=usage)
        lines = messages[-1]["content"].splitlines()
        rated = next(x for x in lines if x.startswith('"suspicion" must'))
        rated = re.findall(r"\((p\d)\)", rated)
        legal = next((x for x in lines if x.startswith("Players you can")), None)
        legal = re.findall(r"\((p\d)\)", legal) if legal else rated
        who = self.rng.choice(legal)
        action = {
            StatementResponse: {
                "text": "hm",
                "accuse": [who],
                "claim_role": None,
                "seer_report": None,
            },
            VoteResponse: {"vote": who},
            WolfChatResponse: {"message": "go", "proposed_target": who},
            TargetResponse: {"target": who},
        }[response_model]
        reasoning = {"thoughts": "x", "suspicion": [{"player": p, "p": 0.5} for p in rated]}
        self.script.append({"private_reasoning": reasoning, "action": action})
        r = await FakeClient.complete(self, model, messages, response_model)  # no await before pop
        return r.model_copy(update={"usage": r.usage.model_copy(update={"cost_usd": CALL_COST})})


# --- invalid-output circuit breaker -------------------------------------------------------

LIMIT = {"max_invalid_rate": 0.25}  # the default; cfg() turns the breaker off


async def test_healthy_fake_gives_valid_games(tmp_path):
    res = await run_batch(cfg(n_games=1, **LIMIT), SPECS, HealthyFake(), runs_dir=tmp_path)
    assert res["status"] == "completed"
    assert all(v["invalid"] == 0 and v["calls"] > 0 for v in res["invalid_output"].values())


async def test_healthy_batch_does_not_trip_the_breaker(tmp_path):
    res = await run_batch(cfg(**LIMIT), SPECS, HealthyFake(), runs_dir=tmp_path)
    assert res["status"] == "completed" and res["invalid_stop"] is None
    assert len(res["finished"]) == 4


async def test_invalid_model_trips_the_breaker_and_names_it(tmp_path):
    c = cfg(n_games=8, max_concurrency=1, **LIMIT)
    res = await run_batch(c, SPECS, HealthyFake(bad="b"), runs_dir=tmp_path)
    assert res["status"] == "stopped_invalid"
    assert 1 <= len(res["finished"]) < 8 and res["not_started"]
    assert res["invalid_stop"].startswith("b: 100.0% invalid output")
    assert res["invalid_output"]["a"]["invalid"] == 0
    assert res["invalid_output"]["b"]["invalid"] > 0
    meta = json.loads((tmp_path / "t/run_meta.json").read_text())
    assert meta["status"] == "stopped_invalid"
    assert meta["invalid_output"] == res["invalid_output"]
    assert meta["invalid_stop"] == res["invalid_stop"]


async def test_provider_errors_alone_do_not_trip_the_breaker(tmp_path):
    c = cfg(n_games=4, max_concurrency=1, **LIMIT)
    res = await run_batch(c, SPECS, HealthyFake(provider_down="b"), runs_dir=tmp_path)
    assert res["status"] == "completed" and res["invalid_stop"] is None
    assert "b" not in res["invalid_output"]  # no model calls, no invalids
    events = [json.loads(x) for x in (tmp_path / "t/t-g0000.jsonl").read_text().splitlines()]
    assert any(e["meta"].get("reason") == "provider" for e in events)  # they were logged


async def test_model_errors_still_trip_the_breaker_next_to_provider_errors(tmp_path):
    c = cfg(n_games=8, max_concurrency=1, **LIMIT)
    fake = HealthyFake(bad="b", provider_down="a")
    res = await run_batch(c, SPECS, fake, runs_dir=tmp_path)
    assert res["status"] == "stopped_invalid"
    assert res["invalid_stop"].startswith("b: 100.0% invalid output")
    assert "a" not in res["invalid_output"]


async def test_breaker_waits_for_the_minimum_number_of_calls(tmp_path):
    c = cfg(n_games=3, max_concurrency=1, min_calls_for_invalid_check=10**6, **LIMIT)
    res = await run_batch(c, SPECS, HealthyFake(bad="b"), runs_dir=tmp_path)
    assert res["status"] == "completed" and res["invalid_stop"] is None


async def test_resume_rechecks_invalid_rate_and_a_higher_limit_continues(tmp_path):
    c = cfg(n_games=8, max_concurrency=1, **LIMIT)
    first = await run_batch(c, SPECS, HealthyFake(bad="b"), runs_dir=tmp_path)
    assert first["status"] == "stopped_invalid"
    client = HealthyFake()
    again = await run_batch(c, SPECS, client, runs_dir=tmp_path)  # same limit: still tripped
    assert again["status"] == "stopped_invalid" and client.calls == []
    assert again["invalid_output"] == first["invalid_output"]
    # max_invalid_rate is not in the fingerprint, so a higher limit resumes the same batch
    done = await run_batch(
        cfg(n_games=8, max_concurrency=1, max_invalid_rate=1.0), SPECS, client, runs_dir=tmp_path
    )
    assert done["status"] == "completed"
    assert len(done["finished"]) + len(done["skipped"]) == 8


def _breaker_cli(tmp_path, monkeypatch, **kw):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOSTED_VLLM_API_KEY", "test-key")
    from societysim.models import client as client_module

    monkeypatch.setattr(
        client_module, "LiteLLMClient", lambda *, limits: HealthyFake(bad=["qwen3-235b"])
    )
    path = write_config(tmp_path, n_games=6, max_concurrency=1, max_invalid_rate=0.25, **kw)
    args = ["batch", "--config", str(path), "--registry", str(REGISTRY), "--agents", "llm"]
    return RUNNER.invoke(app, args)


def test_cli_batch_invalid_stop_names_model_and_exits_2(tmp_path, monkeypatch):
    result = _breaker_cli(tmp_path, monkeypatch)
    assert result.exit_code == 2, result.output
    assert "invalid output: no new games" in result.output  # progress line, not "budget:"
    assert "budget: no new games" not in result.output
    assert "stopped for invalid output: qwen3-235b: " in result.output
    assert "raise max_invalid_rate (now 25.0%)" in result.output
    meta = json.loads((tmp_path / "runs/cli/run_meta.json").read_text())
    assert meta["status"] == "stopped_invalid"
