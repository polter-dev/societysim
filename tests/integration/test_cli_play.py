# Manual real run (HF_TOKEN comes from .env):
# uv run societysim play --config configs/selfplay_smoke.yaml --agents llm

import json
import os
from pathlib import Path

import pytest
from fakes import FakeClient
from typer.testing import CliRunner

from societysim.cli import app
from societysim.config import GameConfig, load_yaml
from societysim.logging.events import EventType

CONFIG = Path(__file__).resolve().parents[2] / "configs/selfplay_smoke.yaml"
RUNNER = CliRunner()


def events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_smoke_config_loads():
    cfg = load_yaml(CONFIG, GameConfig)
    assert cfg.n_players == 7
    assert cfg.seed == 0
    assert cfg.seat_models() == ["gemma4-26b"] * 7


def test_scripted_play_is_reproducible_and_writes_metadata(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    args = ["play", "--config", str(CONFIG)]
    first = RUNNER.invoke(app, args)
    assert first.exit_code == 0, first.output
    log = tmp_path / "runs/selfplay_smoke/selfplay_smoke-s0.jsonl"
    meta = log.with_suffix(".run_meta.json")
    assert log.is_file()
    assert meta.is_file()
    assert str(Path("runs/selfplay_smoke/selfplay_smoke-s0.jsonl")) in first.output
    assert "winner:" in first.output
    first_bytes = log.read_bytes()
    assert first_bytes
    assert json.loads(meta.read_text())["game_id"] == "selfplay_smoke-s0"

    second = RUNNER.invoke(app, args)
    assert second.exit_code == 0, second.output
    assert log.read_bytes() == first_bytes


def test_seed_override_changes_game_id(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = RUNNER.invoke(
        app, ["play", "--config", str(CONFIG), "--seed", "19", "--batch-id", "trial"]
    )
    assert result.exit_code == 0, result.output
    log = tmp_path / "runs/trial/trial-s19.jsonl"
    assert log.is_file()
    assert events(log)[0]["meta"]["seed"] == 19


def test_missing_config_exits_one(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = RUNNER.invoke(app, ["play", "--config", str(tmp_path / "absent.yaml")])
    assert result.exit_code == 1
    assert result.output.strip().startswith("config file not found:")
    assert len(result.output.splitlines()) == 1


def test_unknown_model_exits_one(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "unknown.yaml"
    config.write_text("n_players: 7\nseed: 0\nmodels: [not-in-registry]\n")
    result = RUNNER.invoke(app, ["play", "--config", str(config)])
    assert result.exit_code == 1
    assert "unknown model 'not-in-registry'" in result.output
    assert len(result.output.splitlines()) == 1


def test_llm_mode_uses_client_and_completes_offline(tmp_path, monkeypatch):
    from societysim.models import client as client_module

    monkeypatch.chdir(tmp_path)
    clients = []

    def fake_client(*, limits):
        assert "hosted_vllm" in limits
        client = FakeClient(players=[f"p{i}" for i in range(7)])
        clients.append(client)
        return client

    monkeypatch.setattr(client_module, "LiteLLMClient", fake_client)
    result = RUNNER.invoke(app, ["play", "--config", str(CONFIG), "--agents", "llm"])
    assert result.exit_code == 0, result.output
    log = tmp_path / "runs/selfplay_smoke/selfplay_smoke-s0.jsonl"
    assert log.is_file()
    assert clients and clients[0].calls
    assert events(log)[-1]["type"] == EventType.GAME_END
    assert events(log)[-1]["meta"]["winner"] in {"wolves", "villagers", "draw"}


@pytest.mark.skipif(not os.environ.get("HF_TOKEN"), reason="HF_TOKEN is required")
def test_real_hf_selfplay(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = RUNNER.invoke(app, ["play", "--config", str(CONFIG), "--agents", "llm"])
    assert result.exit_code == 0, result.output
    log = tmp_path / "runs/selfplay_smoke/selfplay_smoke-s0.jsonl"
    game_events = events(log)
    assert game_events[-1]["meta"]["winner"] in {"wolves", "villagers", "draw"}
    types = {event["type"] for event in game_events}
    assert set(EventType) <= types
    invalid = sum(event["type"] == EventType.INVALID_OUTPUT for event in game_events)
    replies = sum("usage" in event["meta"] for event in game_events)
    assert invalid / replies < 0.10
    total_cost = sum(event["meta"].get("usage", {}).get("cost_usd", 0) for event in game_events)
    assert total_cost < 0.25
