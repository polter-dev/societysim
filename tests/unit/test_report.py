import copy
import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from societysim.analysis.metrics import compute_metrics, load_game
from societysim.analysis.report import (
    build_leaderboard,
    build_summary,
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


def _mixed_batch(tmp_path: Path) -> Path:
    batch = _batch(tmp_path, labels=False)
    lines = (FIX / "sample_game.jsonl").read_text().splitlines()
    ends = [i for i, ln in enumerate(lines) if json.loads(ln)["type"] == "game_end"]
    cut = "\n".join(lines[: ends[0]]) + "\n"
    for gid, tail in (("cut", ""), ("torn", '{"game_id": "x')):
        text = cut.replace("fixture-0001", gid) + tail
        (batch / f"{gid}.jsonl").write_text(text)
    return batch


def test_unfinished_games_are_skipped_and_counted(tmp_path, capsys):
    batch = _mixed_batch(tmp_path)
    out = write_report(batch)
    m = json.loads((out / "metrics.json").read_text())
    assert m["games"] == 1 and m["unfinished_games"] == 2
    assert [g["game_id"] for g in json.loads((out / "index.json").read_text())] == ["fixture-0001"]
    assert "2 unfinished games" in (out / "report.md").read_text()
    assert capsys.readouterr().out.count("has no game_end, skipped") == 2


def test_label_report_highlights_succeed_on_mixed_batch(tmp_path):
    batch = _mixed_batch(tmp_path)
    r = CliRunner()
    for cmd in (["label", "--no-jev"], ["report"], ["highlights"]):
        res = r.invoke(app, [cmd[0], str(batch), *cmd[1:]])
        assert res.exit_code == 0, (cmd, res.output)


def _report(tmp_path: Path, batch: Path | None = None):
    out = write_report(batch or _batch(tmp_path))
    return out, json.loads((out / "metrics.json").read_text())


def test_headline_numbers(tmp_path):
    out, m = _report(tmp_path)
    sm = m["summary"]
    us = [
        e["meta"]["usage"]
        for e in map(json.loads, (FIX / "sample_game.jsonl").read_text().splitlines())
        if e["meta"].get("usage")
    ]
    assert sm["games"] == 1 and sm["unfinished_games"] == 0
    assert sm["cost_usd"] == pytest.approx(sum(u["cost_usd"] for u in us))
    assert sm["output_tokens"] == sum(u["output_tokens"] for u in us)
    assert sm["wolf_wins"] + sm["village_wins"] + sm["draws"] == 1
    allg = m["by_group"]["all"]["lies"]["self_tag"]["lie_success"]
    assert sm["lies"]["self_tag"]["lie_success"] == allg
    assert sm["top_openskill"]["model"] in m["models"]
    md = (out / "report.md").read_text(encoding="utf-8")
    assert md.index("## Headline") < md.index("## Results") < md.index("## Leaderboard")
    assert "Top OpenSkill" in md and "Lies (self_tag)" in md


def test_highest_lie_success_needs_five_comparisons(tmp_path):
    games = [load_game(FIX / "sample_game.jsonl")]
    metrics = compute_metrics(games)
    for src in ("self_tag", "jev"):
        for g in metrics["models"].values():
            g["all"]["lies"][src]["lie_success"]["comparisons"] = 4
            g["all"]["lies"][src]["lie_success"]["share_moved"] = 1.0
    top = build_summary(metrics, games)["top_share_moved"]
    assert top == {"self_tag": None, "jev": None}
    name = next(iter(metrics["models"]))
    metrics["models"][name]["all"]["lies"]["jev"]["lie_success"]["comparisons"] = 5
    top = build_summary(metrics, games)["top_share_moved"]
    assert top["jev"]["model"] == name and top["self_tag"] is None


def test_results_table_columns(tmp_path):
    out, _ = _report(tmp_path)
    md = (out / "report.md").read_text(encoding="utf-8")
    assert (
        "| Model | Games | Win rate (95% CI) | OpenSkill ordinal | Lie rate (self_tag / jev) "
        "| Lie success, moved (self_tag / jev) | Detection AUC |" in md
    )
    row = next(ln for ln in md.splitlines() if ln.startswith("| gemma-4-26b | 1 |"))
    assert " to " in row and "n/a" in row and "(n=" in row
    assert "### Lie types" in md and "false_role_claim (self_tag)" in md


def _speed_batch(tmp_path: Path) -> Path:
    """Fixture with gemma-4-26b renamed amd-gemma (free), one cached and one zero-latency call."""
    batch = _batch(tmp_path)
    log = batch / "sample_game.jsonl"
    events = [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines()]
    seats = events[0]["meta"]["seats"]
    amd = {s for s, v in seats.items() if v["model"] == "gemma-4-26b"}
    for v in seats.values():
        if v["model"] == "gemma-4-26b":
            v["model"] = "amd-gemma"
    n = 0
    for e in events:
        u = e["meta"].get("usage")
        if u and e["actor"] in amd:
            u.update(cost_usd=0.0, latency_ms=1000, output_tokens=100)
            if n == 0:
                u.update(cached=True, latency_ms=99999)
            elif n == 1:
                u.update(latency_ms=0, output_tokens=99999)
            n += 1
    assert n >= 3
    log.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    return batch


def test_speed_skips_cached_and_zero_latency(tmp_path):
    out, m = _report(tmp_path, _speed_batch(tmp_path))
    s = m["speed"]["amd-gemma"]
    assert s["calls"] >= 3 and s["timed_calls"] == s["calls"] - 2
    assert s["median_latency_ms"] == 1000 and s["p90_latency_ms"] == 1000
    assert s["output_tokens_per_s"] == pytest.approx(100.0)
    md = (out / "report.md").read_text(encoding="utf-8")
    assert "## Speed" in md
    speed = md.split("## Speed")[1].split("## Charts")[0]
    row = next(ln for ln in speed.splitlines() if ln.startswith("| amd-gemma |"))
    assert row.endswith("| 0 (self-hosted) |")
    assert speed.count("self-hosted") == 1


def test_lie_rate_chart_and_stable_keys(tmp_path):
    out, m = _report(tmp_path)
    assert (out / "lie_rate.png").read_bytes()[:4] == b"\x89PNG"
    assert "![Lie rate](lie_rate.png)" in (out / "report.md").read_text(encoding="utf-8")
    base = json.loads(json.dumps(compute_metrics([load_game(FIX / "sample_game.jsonl")])))
    assert set(m) - set(base) == {"summary", "speed"}
    assert m["models"] == base["models"] and m["by_group"] == base["by_group"]
    board = json.loads((out / "leaderboard.json").read_text())
    assert "summary" not in board and "speed" not in board
