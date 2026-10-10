import json
import shutil
from pathlib import Path

from typer.testing import CliRunner

from societysim.analysis.report import write_report
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
    assert set(metrics) == {"self_tag", "jev"}
    # the fixture has self_tag labels only: jev must not inherit them
    assert metrics["self_tag"]["by_group"]["all"]["lies"] > 0
    assert metrics["jev"]["by_group"]["all"]["lies"] == 0


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
