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


@pytest.fixture(autouse=True)
def _fixed_play_randomness(monkeypatch):
    monkeypatch.setattr("societysim.cli.secrets.token_hex", lambda n: "deadbeefdeadbeef")
    monkeypatch.setattr("societysim.cli.secrets.randbelow", lambda n: 17)


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
    log = tmp_path / "runs/selfplay_smoke/selfplay_smoke-deadbeefdeadbeef.jsonl"
    meta = log.with_suffix(".run_meta.json")
    assert log.is_file()
    assert meta.is_file()
    assert str(Path("runs/selfplay_smoke/selfplay_smoke-deadbeefdeadbeef.jsonl")) in first.output
    assert "winner:" in first.output
    first_bytes = log.read_bytes()
    assert first_bytes
    assert json.loads(meta.read_text())["game_id"] == "selfplay_smoke-deadbeefdeadbeef"
    assert json.loads(meta.read_text())["agents"] == "scripted"
    assert json.loads(meta.read_text())["seed"] == 17

    second = RUNNER.invoke(app, args)
    assert second.exit_code == 0, second.output
    assert log.read_bytes() == first_bytes


def test_seed_override_changes_deal_but_not_id_shape(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = RUNNER.invoke(
        app, ["play", "--config", str(CONFIG), "--seed", "19", "--batch-id", "trial"]
    )
    assert result.exit_code == 0, result.output
    log = tmp_path / "runs/trial/trial-deadbeefdeadbeef.jsonl"
    assert log.is_file()
    assert events(log)[0]["meta"]["seed"] == 19
    assert json.loads(log.with_suffix(".run_meta.json").read_text())["seed"] == 19


def test_play_without_seed_draws_a_new_seed_for_each_game(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    seeds = iter([101, 202])
    ids = iter(["1111111111111111", "2222222222222222"])
    monkeypatch.setattr("societysim.cli.secrets.randbelow", lambda n: next(seeds))
    monkeypatch.setattr("societysim.cli.secrets.token_hex", lambda n: next(ids))
    for suffix, seed in [("1111111111111111", 101), ("2222222222222222", 202)]:
        result = RUNNER.invoke(app, ["play", "--config", str(CONFIG)])
        assert result.exit_code == 0, result.output
        log = tmp_path / f"runs/selfplay_smoke/selfplay_smoke-{suffix}.jsonl"
        assert log.is_file()
        assert events(log)[0]["meta"]["seed"] == seed
        assert json.loads(log.with_suffix(".run_meta.json").read_text())["seed"] == seed
        assert f"selfplay_smoke-{suffix}" in result.output
        assert f"-s{seed}" not in result.output


def test_explicit_seed_repeats_game_events_with_new_id(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ids = iter(["1111111111111111", "2222222222222222"])
    monkeypatch.setattr("societysim.cli.secrets.token_hex", lambda n: next(ids))
    logs = []
    for suffix in ("1111111111111111", "2222222222222222"):
        result = RUNNER.invoke(app, ["play", "--config", str(CONFIG), "--seed", "19"])
        assert result.exit_code == 0, result.output
        logs.append((tmp_path / f"runs/selfplay_smoke/selfplay_smoke-{suffix}.jsonl").read_text())
    assert logs[0].replace("selfplay_smoke-1111111111111111", "GAME_ID") == logs[1].replace(
        "selfplay_smoke-2222222222222222", "GAME_ID"
    )


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
    monkeypatch.setenv("HOSTED_VLLM_API_KEY", "test-key")
    clients = []

    def fake_client(*, limits):
        assert "hosted_vllm" in limits
        client = FakeClient(players=[f"p{i}" for i in range(7)])
        clients.append(client)
        return client

    monkeypatch.setattr(client_module, "LiteLLMClient", fake_client)
    result = RUNNER.invoke(app, ["play", "--config", str(CONFIG), "--agents", "llm"])
    assert result.exit_code == 0, result.output
    log = tmp_path / "runs/selfplay_smoke/selfplay_smoke-deadbeefdeadbeef.jsonl"
    assert log.is_file()
    assert clients and clients[0].calls
    assert events(log)[-1]["type"] == EventType.GAME_END
    assert events(log)[-1]["meta"]["winner"] in {"wolves", "villagers", "draw"}


def test_llm_mode_requires_provider_key_before_starting(tmp_path, monkeypatch):
    from societysim.models import client as client_module

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("societysim.cli.load_dotenv", lambda: None)
    monkeypatch.delenv("HOSTED_VLLM_API_KEY", raising=False)
    monkeypatch.delenv("HF_TOKEN", raising=False)

    def unexpected_client(*, limits):
        pytest.fail("LiteLLMClient must not be created without a provider key")

    monkeypatch.setattr(client_module, "LiteLLMClient", unexpected_client)
    result = RUNNER.invoke(app, ["play", "--config", str(CONFIG), "--agents", "llm"])
    assert result.exit_code == 1, result.output
    assert "HOSTED_VLLM_API_KEY" in result.output
    assert len(result.output.splitlines()) == 1
    assert not (tmp_path / "runs").exists()


def _answer(question: str) -> str:
    """Scripted stdin for a human seat: choice 1, nothing optional (no suspicion either)."""
    if "blank" in question:
        return ""
    return "1" if "[1-" in question else "I trust nobody yet."


def test_human_seat_plays_against_llms_and_is_logged_as_human(tmp_path, monkeypatch):
    from societysim.models import client as client_module

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOSTED_VLLM_API_KEY", "test-key")
    monkeypatch.setattr("builtins.input", _answer)
    clients = []

    def fake_client(*, limits):
        clients.append(FakeClient(players=[f"p{i}" for i in range(7)]))
        return clients[-1]

    monkeypatch.setattr(client_module, "LiteLLMClient", fake_client)
    args = ["play", "--config", str(CONFIG), "--agents", "llm", "--human-seat", "p3"]
    result = RUNNER.invoke(app, [*args, "--batch-id", "h"])
    assert result.exit_code == 0, result.output
    assert "You are playing Werewolf" in result.output
    assert clients[0].calls  # the AI seats still go through the model client
    log = tmp_path / "runs/h/h-deadbeefdeadbeef.jsonl"
    evs = events(log)
    seats = evs[0]["meta"]["seats"]
    assert seats["p3"]["model"] == "human"
    assert {s["model"] for k, s in seats.items() if k != "p3"} == {"gemma4-26b"}
    meta = json.loads(log.with_suffix(".run_meta.json").read_text())
    assert meta["models"][3] == "human"
    assert meta["agents"] == "llm"
    mine = [e for e in evs if e.get("actor") == "p3" and e["meta"].get("usage")]
    assert mine
    assert all(e["meta"]["usage"]["model"] == "human" for e in mine)
    # Skipped suspicion is logged as null, never as a made-up 0.5.
    assert all(e["suspicion"] is None for e in mine)
    assert all(e["private_reasoning"] == "human player" for e in mine)
    assert not any(e["meta"].get("suspicion_default") for e in evs)
    assert not [e for e in evs if e["type"] == EventType.INVALID_OUTPUT and e["actor"] == "p3"]
    assert evs[-1]["type"] == EventType.GAME_END


def test_human_seat_works_in_a_ten_player_game(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("builtins.input", _answer)
    config = tmp_path / "ten.yaml"
    config.write_text("n_players: 10\nseed: 0\nmodels: [gemma4-26b]\n")
    args = ["play", "--config", str(config), "--human-seat", "p9", "--batch-id", "h"]
    result = RUNNER.invoke(app, args)
    assert result.exit_code == 0, result.output
    evs = events(tmp_path / "runs/h/h-deadbeefdeadbeef.jsonl")
    seats = evs[0]["meta"]["seats"]
    assert list(seats) == [f"p{i}" for i in range(10)]
    assert seats["p9"]["model"] == "human"
    assert sum(s["model"] == "gemma4-26b" for s in seats.values()) == 9
    assert any(
        e["actor"] == "p9" and e["meta"].get("usage", {}).get("model") == "human" for e in evs
    )
    assert sorted(evs[-1]["meta"]["roles"].values()).count("wolf") == 2
    assert evs[-1]["type"] == EventType.GAME_END


def test_human_seat_stdin_eof_abandons_the_game(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    args = ["play", "--config", str(CONFIG), "--human-seat", "p0", "--batch-id", "h"]
    result = RUNNER.invoke(app, args, input="")
    assert result.exit_code == 130
    assert "game abandoned" in result.output


def test_human_seat_must_be_a_seat(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = RUNNER.invoke(app, ["play", "--config", str(CONFIG), "--human-seat", "p7"])
    assert result.exit_code == 1
    assert "--human-seat must be one of p0" in result.output


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("HF_TOKEN"), reason="HF_TOKEN is required")
def test_real_hf_selfplay(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = RUNNER.invoke(app, ["play", "--config", str(CONFIG), "--agents", "llm"])
    assert result.exit_code == 0, result.output
    log = tmp_path / "runs/selfplay_smoke/selfplay_smoke-deadbeefdeadbeef.jsonl"
    game_events = events(log)
    assert game_events[-1]["meta"]["winner"] in {"wolves", "villagers", "draw"}
    types = {event["type"] for event in game_events}
    assert {
        EventType.GAME_START,
        EventType.PHASE_START,
        EventType.STATEMENT,
        EventType.VOTE,
        EventType.GAME_END,
    } <= types
    assert {EventType.DEATH_REVEAL, EventType.NIGHT_KILL, EventType.ELIMINATION} & types
    invalid = sum(event["type"] == EventType.INVALID_OUTPUT for event in game_events)
    replies = sum("usage" in event["meta"] for event in game_events)
    if replies:
        assert invalid / replies < 0.10
    total_cost = sum(event["meta"].get("usage", {}).get("cost_usd", 0) for event in game_events)
    assert total_cost < 0.25
