import copy
import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from societysim.analysis.metrics import compute_metrics, load_game
from societysim.analysis.report import (
    build_leaderboard,
    model_game_counts,
    render_markdown,
    write_report,
)
from societysim.cli import app

FIX = Path(__file__).parents[2] / "fixtures"


def _batch(tmp_path: Path, labels: bool = True) -> Path:
    batch = tmp_path / "batch"
    batch.mkdir()
    shutil.copy(FIX / "sample_game.jsonl", batch)
    if labels:
        shutil.copy(FIX / "sample_game.labels.jsonl", batch)
    return batch


def test_report_writes_everything(tmp_path):
    out = write_report(_batch(tmp_path))
    for name in (
        "report.md",
        "metrics.json",
        "leaderboard.json",
        "index.json",
        "win_rate.png",
        "detection_auc.png",
        "openskill.png",
    ):
        assert (out / name).stat().st_size > 0, name
    assert (out / "win_rate.png").read_bytes()[:4] == b"\x89PNG"


def test_report_md_labels_and_sources(tmp_path):
    md = (write_report(_batch(tmp_path)) / "report.md").read_text(encoding="utf-8")
    assert "Lie success (exploratory)" in md
    assert "Persuasion (exploratory)" in md
    assert "No-lie base rate" in md
    assert "Lie rate (self_tag)" in md and "Lie rate (jev)" in md
    rows = [ln for ln in md.splitlines() if "| self_tag |" in ln or "| jev |" in ln]
    assert any("| self_tag |" in ln for ln in rows) and any("| jev |" in ln for ln in rows)
    assert "Wolf-side advantage" in md


def test_leaderboard_shape(tmp_path):
    out = write_report(_batch(tmp_path))
    board = json.loads((out / "leaderboard.json").read_text())
    assert board["schema"] == 1 and board["games"] == 1
    assert board["label_sources"] == ["self_tag", "jev"]
    assert {r["model"] for r in board["models"]} == {
        "qwen3-235b",
        "llama-4-maverick",
        "gemma-4-26b",
        "gpt-oss-120b",
        "deepseek-v3.1",
        "gemini-flash",
        "gemini-flash-lite",
    }
    assert set(board["models"][0]["lie_rate"]) == {"self_tag", "jev"}
    ords = [r["openskill"]["ordinal"] for r in board["models"]]
    assert ords == sorted(ords, reverse=True)
    metrics = json.loads((out / "metrics.json").read_text())
    # the fixture has self_tag labels only: jev must not inherit them
    lies = metrics["by_group"]["all"]["lies"]
    assert lies["self_tag"]["lies"] > 0
    assert lies["jev"]["lies"] == 0
    assert "ordinal" in board["wolf_side_advantage"]["overall"]
    assert all("wolf_side" not in r["model"] for r in board["models"])


def test_index_json(tmp_path):
    out = write_report(_batch(tmp_path, labels=False))
    index = json.loads((out / "index.json").read_text())
    assert len(index) == 1
    g = index[0]
    assert g["game_id"] == "fixture-0001" and g["file"] == "sample_game.jsonl"
    assert g["seats"]["p2"]["role"] == "wolf" and g["days"] >= 1


def test_cli_report(tmp_path):
    batch = _batch(tmp_path)
    res = CliRunner().invoke(app, ["report", str(batch)])
    assert res.exit_code == 0, res.output
    assert (batch / "report" / "report.md").exists()
    empty = tmp_path / "empty"
    empty.mkdir()
    assert CliRunner().invoke(app, ["report", str(empty)]).exit_code == 1


def test_missing_jev_labels_show_na_not_zero(tmp_path):
    out = write_report(_batch(tmp_path))  # fixture has self_tag labels only
    board = json.loads((out / "leaderboard.json").read_text())
    assert all(r["lie_rate"]["jev"] is None for r in board["models"])
    assert any(r["lie_rate"]["self_tag"] is not None for r in board["models"])
    md = (out / "report.md").read_text(encoding="utf-8")
    lies = md.split("## Lies")[1].split("###")[0]
    rows = [ln for ln in lies.splitlines() if ln.startswith("| ") and "---" not in ln][1:]
    assert rows and all(ln.split("|")[-2].strip() == "n/a" for ln in rows)
    assert "compared with its prior" in md


def test_missing_base_rate_raises():
    metrics = compute_metrics([load_game(FIX / "sample_game.jsonl")])
    broken = copy.deepcopy(metrics)
    for groups in broken["models"].values():
        for grp in groups.values():
            for src in grp["lies"].values():
                del src["lie_success"]["base_rate"]
    board = build_leaderboard(broken)
    with pytest.raises(KeyError):
        render_markdown(broken, board)


def _two_seat_batch(tmp_path: Path) -> Path:
    """The fixture game with llama's seat given to qwen: qwen fills 2 seats in 1 game."""
    batch = _batch(tmp_path)
    log = batch / "sample_game.jsonl"
    log.write_text(
        log.read_text(encoding="utf-8").replace("llama-4-maverick", "qwen3-235b"),
        encoding="utf-8",
    )
    return batch


def test_leaderboard_reports_games_and_seats_separately(tmp_path):
    out = write_report(_two_seat_batch(tmp_path))
    board = json.loads((out / "leaderboard.json").read_text())
    rows = {r["model"]: r for r in board["models"]}
    assert "llama-4-maverick" not in rows
    assert (rows["qwen3-235b"]["games"], rows["qwen3-235b"]["seats"]) == (1, 2)
    assert (rows["gemma-4-26b"]["games"], rows["gemma-4-26b"]["seats"]) == (1, 1)
    assert all(r["games"] <= board["games"] for r in board["models"])
    md = (out / "report.md").read_text(encoding="utf-8")
    assert "| Model | Games | Seats | Win rate (per seat) |" in md
    assert "Win rate is per seat" in md
    assert "`seats`" in md.split("## leaderboard.json shape")[1]


def test_model_game_counts_are_distinct_games():
    g = load_game(FIX / "sample_game.jsonl")
    other = copy.deepcopy(g)
    other.events[0] = other.events[0].model_copy(update={"game_id": "other"})
    counts = model_game_counts([g, other])
    assert set(counts.values()) == {2}
    assert model_game_counts([g, g]) == {m: 1 for m in counts}  # same game twice is one game


def test_unknown_game_count_is_null_not_seats():
    metrics = compute_metrics([load_game(FIX / "sample_game.jsonl")])
    board = build_leaderboard(metrics)
    assert all(r["games"] is None and r["seats"] == 1 for r in board["models"])


def test_base_rate_shows_its_denominator(tmp_path):
    # fixture: gemma-4-26b's only bussing has 5 base votes, none followed, one an abstain
    out = write_report(_batch(tmp_path))
    md = (out / "report.md").read_text(encoding="utf-8")
    row = next(ln for ln in md.splitlines() if ln.startswith("| gemma-4-26b | self_tag |"))
    assert row.split("|")[-2].strip() == "0% (5, 1 abstain)"
    # no base days at all: n/a with a zero denominator
    jev = next(ln for ln in md.splitlines() if ln.startswith("| gemma-4-26b | jev |"))
    assert jev.split("|")[-2].strip() == "n/a (0)"
