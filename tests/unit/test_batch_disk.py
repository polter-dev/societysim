import errno
import json
from pathlib import Path

from fakes import FakeClient
from typer.testing import CliRunner

from societysim.batch import runner
from societysim.batch.runner import run_batch
from societysim.cli import app
from societysim.config import BatchConfig, ModelSpec
from societysim.models.client import CompletionResult

REGISTRY = Path(__file__).resolve().parents[2] / "configs/models.yaml"
PLAYERS = [f"p{i}" for i in range(7)]
SPECS = {"a": ModelSpec(name="a", litellm_model="openai/a")}
FULL = "No space left on device"


def cfg(**kw) -> BatchConfig:
    base = {
        "batch_id": "t",
        "n_games": 8,
        "models": ["a"],
        "budget_usd": 100,
        "max_concurrency": 2,
        "max_invalid_rate": 1.0,
        "game": {"n_players": 7},
    }
    return BatchConfig.model_validate(base | kw)


class Priced(FakeClient):
    def __init__(self):
        super().__init__(players=PLAYERS)

    async def complete(self, model, messages, response_model) -> CompletionResult:
        r = await super().complete(model, messages, response_model)
        return r.model_copy(update={"usage": r.usage.model_copy(update={"cost_usd": 0.001})})


def disk_full_sink(monkeypatch):
    async def write(self, event):
        raise OSError(errno.ENOSPC, FULL)

    monkeypatch.setattr(runner.JsonlSink, "write", write)


async def test_unwritable_logs_stop_new_games_after_the_first_failure(tmp_path, monkeypatch):
    disk_full_sink(monkeypatch)
    events = []
    res = await run_batch(cfg(), SPECS, None, runs_dir=tmp_path, on_progress=events.append)
    assert res["status"] == "failed"
    assert res["disk_error"]
    started = len(res["failed"])
    assert 1 <= started <= 2  # at most max_concurrency games started
    assert len(res["not_started"]) == 8 - started
    assert [e["reason"] for e in events if e["event"] == "stopped"] == ["disk_error"]
    meta = json.loads((tmp_path / "t/run_meta.json").read_text(encoding="utf-8"))
    assert meta["disk_error"] == res["disk_error"] and meta["status"] == "failed"


async def test_in_flight_spend_is_in_the_ledger_when_the_disk_stops_the_batch(
    tmp_path, monkeypatch
):
    disk_full_sink(monkeypatch)
    res = await run_batch(cfg(), SPECS, Priced(), runs_dir=tmp_path)
    assert res["disk_error"] and len(res["failed"]) <= 2
    assert res["spent_usd"] > 0
    meta = json.loads((tmp_path / "t/run_meta.json").read_text(encoding="utf-8"))
    assert meta["spent_usd"] == res["spent_usd"]


async def test_run_meta_out_of_space_stops_new_games_with_the_errno_text(tmp_path, monkeypatch):
    real = runner._replace
    n = 0

    def replace(src, dst):
        nonlocal n
        if dst.name == "run_meta.json":
            n += 1
            if 1 < n < 6:  # the ledger saves during the first game; the first save went through
                raise OSError(errno.ENOSPC, FULL)
        real(src, dst)

    monkeypatch.setattr(runner, "_replace", replace)
    res = await run_batch(cfg(max_concurrency=1), SPECS, Priced(), runs_dir=tmp_path)
    assert res["disk_error"] == FULL and res["status"] == "failed"
    assert len(res["finished"]) == 1 and len(res["not_started"]) == 7


async def test_a_healthy_disk_is_not_a_stop_reason(tmp_path):
    res = await run_batch(cfg(n_games=3), SPECS, None, runs_dir=tmp_path)
    assert res["status"] == "completed" and res["disk_error"] is None


def test_cli_names_the_disk_and_exits_non_zero(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    disk_full_sink(monkeypatch)
    path = tmp_path / "b.yaml"
    path.write_text(
        json.dumps(
            {
                "batch_id": "cli",
                "n_games": 6,
                "models": ["gemma4-26b", "qwen3-235b"],
                "budget_usd": 5,
                "max_concurrency": 2,
                "game": {"n_players": 7},
            }
        )
    )
    res = CliRunner().invoke(app, ["batch", "--config", str(path), "--registry", str(REGISTRY)])
    assert res.exit_code == 1, res.output
    assert "cannot write logs: no new games" in res.output
    assert f"could not write to {Path('runs') / 'cli'}: " in res.output
    assert "free space and re-run to resume" in res.output


def every_save_after_the_first_fails(monkeypatch):
    real = runner._replace
    n = 0

    def replace(src, dst):
        nonlocal n
        if dst.name == "run_meta.json":
            n += 1
            if n > 1:  # truly full or read-only: the ledger can never be written again
                raise OSError(errno.ENOSPC, FULL)
        real(src, dst)

    monkeypatch.setattr(runner, "_replace", replace)


async def test_full_disk_final_save_still_returns_the_summary(tmp_path, monkeypatch):
    every_save_after_the_first_fails(monkeypatch)
    res = await run_batch(cfg(max_concurrency=1), SPECS, Priced(), runs_dir=tmp_path)
    assert res["status"] == "failed" and res["disk_error"] == FULL
    assert res["spent_usd"] > 0 and len(res["not_started"]) == 7


def test_cli_full_disk_prints_the_clean_message_not_a_traceback(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    every_save_after_the_first_fails(monkeypatch)
    disk_full_sink(monkeypatch)
    path = tmp_path / "b.yaml"
    path.write_text(
        json.dumps(
            {
                "batch_id": "cli",
                "n_games": 6,
                "models": ["gemma4-26b", "qwen3-235b"],
                "budget_usd": 5,
                "max_concurrency": 2,
                "game": {"n_players": 7},
            }
        )
    )
    res = CliRunner().invoke(app, ["batch", "--config", str(path), "--registry", str(REGISTRY)])
    assert res.exit_code == 1, res.output
    assert res.exception is None or isinstance(res.exception, SystemExit)
    assert f"could not write to {Path('runs') / 'cli'}: " in res.output
    assert "free space and re-run to resume" in res.output
    assert "spent $" in res.output and "Traceback" not in res.output
