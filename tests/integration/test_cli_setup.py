"""`play --preset` / `play --setup` and `catalog`, with scripted agents only (no API calls)."""

import json
from pathlib import Path

import litellm  # noqa: F401  (it loads .env on import; do that before tests set the env)
import pytest
from typer.testing import CliRunner

from societysim.cli import app
from societysim.setup import DEFAULT_LINEUP, HUMAN

RUNNER = CliRunner()
REGISTRY = Path(__file__).resolve().parents[2] / "configs/models.yaml"


@pytest.fixture(autouse=True)
def _no_dotenv(monkeypatch, tmp_path):
    monkeypatch.setattr("societysim.cli.load_dotenv", lambda: None)
    monkeypatch.chdir(tmp_path)


def events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def _answer(question: str) -> str:
    """Scripted stdin for a human seat: the first legal option, nothing optional."""
    if question.startswith(("statement", "message")):
        return "I trust nobody yet."
    if question.startswith(("vote", "target", "kill")):
        return question.rsplit("(", 1)[1].split()[0].rstrip("):")
    return ""


def test_play_preset_basic_runs_ten_seats_two_wolves(tmp_path):
    result = RUNNER.invoke(app, ["play", "--preset", "basic", "--seed", "4"])
    assert result.exit_code == 0, result.output
    evs = events(tmp_path / "runs/basic/basic-s4.jsonl")
    seats = evs[0]["meta"]["seats"]
    assert list(seats) == [f"p{i}" for i in range(10)]
    assert {s["model"] for s in seats.values()} == set(DEFAULT_LINEUP)
    assert sorted(evs[-1]["meta"]["roles"].values()).count("wolf") == 2
    assert evs[0]["meta"]["seed"] == 4


def test_play_preset_basic_with_a_human_seat(tmp_path, monkeypatch):
    monkeypatch.setattr("builtins.input", _answer)
    result = RUNNER.invoke(app, ["play", "--preset", "basic", "--human-seat", "p0"])
    assert result.exit_code == 0, result.output
    evs = events(tmp_path / "runs/basic/basic-s0.jsonl")
    seats = evs[0]["meta"]["seats"]
    assert seats["p0"]["model"] == HUMAN
    assert sum(s["model"] != HUMAN for s in seats.values()) == 9
    assert sorted(evs[-1]["meta"]["roles"].values()).count("wolf") == 2


def test_play_setup_file_runs_an_advanced_game(tmp_path):
    setup = {
        "mode": "advanced",
        "n_players": 8,
        "n_wolves": 3,
        "special_roles": {"seer": True, "doctor": False},
        "seats": [
            {"seat": f"p{i}", "model": ["gemma4-26b", "qwen3-235b"][i % 2]} for i in range(8)
        ],
        "seed": 11,
    }
    (tmp_path / "eight.json").write_text(json.dumps(setup))
    result = RUNNER.invoke(app, ["play", "--setup", "eight.json"])
    assert result.exit_code == 0, result.output
    evs = events(tmp_path / "runs/eight/eight-s11.jsonl")
    seats = evs[0]["meta"]["seats"]
    assert [s["model"] for s in seats.values()] == ["gemma4-26b", "qwen3-235b"] * 4
    roles = sorted(evs[-1]["meta"]["roles"].values())
    assert roles.count("wolf") == 3 and "doctor" not in roles and roles.count("seer") == 1
    assert evs[0]["meta"]["seed"] == 11

    again = RUNNER.invoke(app, ["play", "--setup", "eight.json", "--batch-id", "b"])
    assert again.exit_code == 0, again.output
    assert (tmp_path / "runs/b/b-s11.jsonl").read_text().replace("b-s11", "eight-s11") == (
        tmp_path / "runs/eight/eight-s11.jsonl"
    ).read_text()


def test_play_setup_reads_yaml(tmp_path):
    (tmp_path / "s.yaml").write_text("mode: advanced\nn_players: 7\nmodel_pool: [gemma4-26b]\n")
    result = RUNNER.invoke(app, ["play", "--setup", "s.yaml", "--seed", "2"])
    assert result.exit_code == 0, result.output
    seats = events(tmp_path / "runs/s/s-s2.jsonl")[0]["meta"]["seats"]
    assert [s["model"] for s in seats.values()] == ["gemma4-26b"] * 7


def test_play_setup_errors_are_one_clear_line(tmp_path):
    (tmp_path / "bad.json").write_text(json.dumps({"mode": "advanced", "n_wolves": 5}))
    result = RUNNER.invoke(app, ["play", "--setup", "bad.json"])
    assert result.exit_code == 1
    assert result.output.strip() == (
        "invalid setup: 5 wolves need more than 5 villagers; use at most 4 wolves for 10 players"
    )
    assert not (tmp_path / "runs").exists()


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ([], "give exactly one of --config, --preset or --setup"),
        (["--preset", "basic", "--setup", "x.json"], "give exactly one of"),
        (["--setup", "absent.json"], "setup file not found: absent.json"),
        (["--preset", "basic", "--human-seat", "p10"], "invalid setup: human_seat: String should"),
    ],
)
def test_play_option_errors(args, message):
    result = RUNNER.invoke(app, ["play", *args])
    assert result.exit_code == 1
    assert message in result.output


def test_catalog_command_writes_models_catalog(tmp_path, monkeypatch):
    monkeypatch.delenv("AMD_VLLM_BASE_URL", raising=False)
    result = RUNNER.invoke(app, ["catalog", "--out", "site", "--registry", str(REGISTRY)])
    assert result.exit_code == 0, result.output
    cat = json.loads((tmp_path / "site/models_catalog.json").read_text())
    assert cat["default_lineup"] == DEFAULT_LINEUP
    assert cat["human"] == HUMAN
    amd = next(m for m in cat["models"] if m["name"] == "amd-gpt-oss-20b")
    assert amd["available"] is False
    assert "1 unavailable" in result.output
    assert set(cat["models"][0]) == {
        "name",
        "display_name",
        "provider",
        "sponsor",
        "price_tier",
        "structured",
        "structured_ok",
        "color",
        "available",
        "unavailable_reason",
    }
