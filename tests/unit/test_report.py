import copy
import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from societysim.analysis.metrics import compute_metrics, load_game
from societysim.analysis.report import (
    Metrics,
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


FIXTURE_MODELS = (
    "qwen3-235b",
    "llama-4-maverick",
    "gemma-4-26b",
    "gpt-oss-120b",
    "deepseek-v3.1",
    "gemini-flash-lite",  # before gemini-flash: it contains that name
    "gemini-flash",
)


def _self_play_log(batch: Path, model: str, gid: str, winner: str = "wolves") -> None:
    """The fixture game with every seat played by `model`."""
    text = (FIX / "sample_game.jsonl").read_text(encoding="utf-8")
    for m in FIXTURE_MODELS:
        text = text.replace(f'"{m}"', f'"{model}"')
    text = text.replace("fixture-0001", gid).replace('"winner":"wolves"', f'"winner":"{winner}"')
    (batch / f"{gid}.jsonl").write_text(text, encoding="utf-8")


def test_self_play_only_model_is_na_and_ranked_after_rated_models(tmp_path):
    batch = _batch(tmp_path, labels=False)
    _self_play_log(batch, "solo", "selfplay-1")
    _self_play_log(batch, "solo", "selfplay-2")
    out, m = _report(tmp_path, batch)
    board = json.loads((out / "leaderboard.json").read_text())
    assert board["models"][-1]["model"] == "solo"
    assert board["models"][-1]["openskill"] is None
    assert all(r["openskill"] for r in board["models"][:-1])
    assert m["summary"]["top_openskill"]["model"] != "solo"
    md = (out / "report.md").read_text(encoding="utf-8")
    assert "does not move that model's rating" in md


def test_self_play_only_batch_has_no_openskill_ranking(tmp_path):
    batch = tmp_path / "batch"
    batch.mkdir()
    # A plays 3 games, C 2, B 1; B's village win gives it the best per-seat win rate
    for gid in ("g1", "g2", "g3"):
        _self_play_log(batch, "A", gid, "wolves")
    _self_play_log(batch, "B", "g4", "villagers")
    _self_play_log(batch, "C", "g5", "wolves")
    _self_play_log(batch, "C", "g6", "villagers")
    out, m = _report(tmp_path, batch)
    board = json.loads((out / "leaderboard.json").read_text())
    rows = board["models"]
    assert all(r["openskill"] is None for r in rows)
    assert [r["model"] for r in rows] == ["B", "C", "A"]  # by win rate, not by game count
    assert m["summary"]["top_openskill"] is None
    assert "Top OpenSkill" not in (out / "report.md").read_text(encoding="utf-8")


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
    assert set(m) - set(base) == {"summary", "speed", "profiles"}
    assert m["models"] == base["models"] and m["by_group"] == base["by_group"]
    board = json.loads((out / "leaderboard.json").read_text())
    assert "summary" not in board and "speed" not in board


def test_headline_pluralises():
    from societysim.analysis.report import _n

    assert _n(1, "wolf seat") == "1 wolf seat"
    assert _n(0, "wolf seat") == "0 wolf seats"
    assert _n(2, "wolf seat") == "2 wolf seats"


def test_headline_wolf_seats_singular(tmp_path):
    _, m = _report(tmp_path)
    sm = copy.deepcopy(m["summary"])
    sm["top_wolf_win"] = {"model": "x", "win_rate": 1.0, "seats": 1}
    from societysim.analysis.report import _headline

    text = "\n".join(_headline(sm, {"self_tag"}))
    assert "over 1 wolf seat)" in text
    sm["top_wolf_win"]["seats"] = 2
    assert "over 2 wolf seats)" in "\n".join(_headline(sm, {"self_tag"}))


def test_who_fools_whom_section_and_heatmap(tmp_path):
    out = write_report(_batch(tmp_path))  # self_tag labels only, as after `label --no-jev`
    md = (out / "report.md").read_text(encoding="utf-8")
    sec = md.split("## Who fools whom (exploratory)")[1].split("## Cost")[0]
    st, rest = sec.split("### jev")
    jev, base = rest.split("### No-lie baseline")
    assert "| Liar \\ listener |" in st
    # deepseek, then gemini-flash-lite; each with that listener's role-claim/report baseline
    assert "| gemini-flash | 100% (n=2), base 25% | 50% (n=2), base 100% |" in st
    assert "None qualifies yet." in st  # no pair has 5 votes in one game
    assert "![Who fools whom (self_tag)](deception_self_tag.png)" in st
    assert jev.strip() == "No jev labels."  # no jev baseline either
    assert "| True role claim: did not vote the claimant | 50% (n=2) |" in base
    assert (out / "deception_self_tag.png").read_bytes()[:4] == b"\x89PNG"
    assert not (out / "deception_jev.png").exists()
    dm = json.loads((out / "metrics.json").read_text())["deception_matrix"]
    assert dm["pairs"]["self_tag"]["gemini-flash"]["deepseek-v3.1"]["voters"] == 2
    assert dm["pairs"]["jev"] == {}


def _cell(followed: int, voters: int, base: float | None = None) -> dict:
    return {
        "heard": voters,
        "comparisons": 0,
        "moved": 0,
        "voters": voters,
        "followed": followed,
        "vote_follow_rate": followed / voters,
        "vote_follow_ci": [0.1, 0.9],
        "belief_shift_rate": None,
        "belief_shift_ci": None,
        "baseline_rate": base,
        "baseline_voters": voters if base is not None else 0,
    }


def test_top_pairs_need_five_votes_and_rank_by_follow_rate():
    from societysim.analysis.report import _deception

    pairs = {
        "a": {"x": _cell(5, 5, 0.1), "y": _cell(4, 4)},  # y: 100% but only 4 votes
        "b": {"x": _cell(3, 6), "y": _cell(6, 8)},
    }
    dm = {"pairs": {"self_tag": pairs, "jev": {}}, "baseline": {}}
    lines = _deception(dm, {"self_tag", "jev"})
    top = [ln for ln in lines if "(10% to 90%)" in ln]
    assert [ln.split(" | ")[:2] for ln in top] == [["| a", "x"], ["| b", "y"], ["| b", "x"]]
    assert top[0].endswith("| 10% (n=5) |")  # the pair's own baseline sits beside it
    assert top[1].endswith("| n/a (n=0) |")
    assert "No lies were heard by another model." in lines
    assert "No truthful claims were heard." in lines


async def test_index_and_report_for_a_ten_player_game(tmp_path):
    from functools import partial

    from pydantic import TypeAdapter

    from societysim.agents.scripted import RandomAgent
    from societysim.analysis.labeler import label_game
    from societysim.analysis.report import Seat
    from societysim.config import GameConfig
    from societysim.logging.sinks.jsonl import JsonlSink
    from societysim.orchestrator import run_game

    sink = JsonlSink("batch", runs_dir=tmp_path)
    await run_game(GameConfig(n_players=10, seed=1), partial(RandomAgent), [sink], "g10")
    batch = tmp_path / "batch"
    labels = label_game(load_game(batch / "g10.jsonl").events)
    (batch / "g10.labels.jsonl").write_text("".join(lb.model_dump_json() + "\n" for lb in labels))
    out = write_report(batch)
    index = json.loads((out / "index.json").read_text())
    assert sorted(index[0]["seats"]) == [f"p{i}" for i in range(10)]
    assert TypeAdapter(Seat).validate_python("p9") == "p9"
    assert "Sky" in {s["name"] for s in index[0]["seats"].values()}  # 10th neutral name


def test_model_profiles_in_metrics(tmp_path):
    out = write_report(_batch(tmp_path))  # self_tag labels only
    metrics = json.loads((out / "metrics.json").read_text())
    board = json.loads((out / "leaderboard.json").read_text())
    profiles = metrics["profiles"]
    assert set(profiles) == {r["model"] for r in board["models"]}
    for r in board["models"]:
        p = profiles[r["model"]]
        for k in ("games", "seats", "win_rate", "win_ci", "wolf_win_rate", "village_win_rate"):
            assert p[k] == r[k], k
        assert p["openskill"] == r["openskill"] and p["lie_rate"] == r["lie_rate"]
        assert p["median_latency_ms"] == metrics["speed"][r["model"]]["median_latency_ms"]
        assert p["fools"]["jev"] == [] and p["fooled_by"]["jev"] == []  # no jev labels
        assert p["lie_rate"]["jev"] is None

    gf = profiles["gemini-flash"]  # the fixture's lying wolf
    assert gf["voice"] == {"voice_id": "cgSgspJ2msm6clMCkdW9", "fallback": False}
    assert gf["detection"] == {"n": 0, "auc": None, "brier": None}  # never a village seat
    fools = gf["fools"]["self_tag"]
    cells = metrics["deception_matrix"]["pairs"]["self_tag"]["gemini-flash"]
    assert {f["model"] for f in fools} == set(cells)
    assert fools[0] == {**cells["deepseek-v3.1"], "model": "deepseek-v3.1"}
    rates = [(f["vote_follow_rate"], f["voters"]) for f in fools]
    assert rates == sorted(rates, key=lambda t: (-t[0], -t[1]))
    assert gf["fooled_by"]["self_tag"] == []

    ds = profiles["deepseek-v3.1"]
    assert ds["fools"]["self_tag"] == []
    assert [f["model"] for f in ds["fooled_by"]["self_tag"]] == ["gemini-flash", "gemma-4-26b"]
    assert ds["fooled_by"]["self_tag"][0]["voters"] == 2
    assert ds["detection"]["n"] == 11 and ds["detection"]["auc"] is not None
    # a model missing from voices.yaml gets the default voice and says so
    assert profiles["llama-4-maverick"]["voice"]["fallback"] is True


def test_profile_pairs_put_missing_rates_last():
    from societysim.analysis.report import _pair_rows

    none = {**_cell(0, 1), "voters": 0, "followed": 0, "vote_follow_rate": None}
    rows = _pair_rows({"z": _cell(1, 2), "n": none, "a": _cell(1, 2), "b": _cell(2, 4)})
    assert [r["model"] for r in rows] == ["b", "a", "z", "n"]


# --- humans vs models (#305) ------------------------------------------------------------------


def _human_batch(tmp_path: Path) -> Path:
    """The fixture with Elif (p4, villager, deepseek-v3.1) played by a visitor."""
    batch = _batch(tmp_path)
    log = batch / "sample_game.jsonl"
    start, rest = log.read_text(encoding="utf-8").split("\n", 1)
    assert start.count('"model":"deepseek-v3.1"') == 1  # the seat; later ones are call usage
    start = start.replace('"model":"deepseek-v3.1"', '"model":"human"')
    log.write_text(f"{start}\n{rest}", encoding="utf-8")
    return batch


def _humans_section(md: str) -> str:
    return md.split("## Humans vs models (exploratory)")[1].split("## Cost")[0]


def test_no_visitor_games_yet(tmp_path):
    out = write_report(_batch(tmp_path))
    assert _humans_section((out / "report.md").read_text(encoding="utf-8")).strip() == (
        "No visitor games yet."
    )
    h = json.loads((out / "metrics.json").read_text())["humans"]
    assert (h["games"], h["seats"], h["by_role"]) == (0, 0, {})


def test_humans_section_and_metrics_for_a_visitor_game(tmp_path):
    out = write_report(_human_batch(tmp_path))
    metrics = json.loads((out / "metrics.json").read_text())
    Metrics.model_validate(metrics)  # the shape schema/metrics.schema.json is generated from
    h = metrics["humans"]
    assert (h["games"], h["seats"]) == (1, 1)
    assert set(h["by_role"]) == {"all", "village", "villager"}
    # the visitor (was deepseek) heard gemini-flash's two lies (both followed) and gemma's
    # bussing (not followed): its column of the deception matrix, summed
    mh = h["lies"]["self_tag"]["model_to_human"]
    assert (mh["heard"], mh["voters"], mh["followed"]) == (3, 3, 2)
    col = [row["human"] for row in metrics["deception_matrix"]["pairs"]["self_tag"].values()]
    assert mh["heard"] == sum(c["heard"] for c in col) == 3
    assert "human" not in metrics["models"] and "human" not in metrics["profiles"]
    sec = _humans_section((out / "report.md").read_text(encoding="utf-8"))
    assert "1 visitor game, 1 human seat." in sec
    assert "| villager | 1 |" in sec
    st, jev = sec.split("### jev")
    # heard, voted as lie implied, caught (voted the liar), belief shift
    assert "| Model lies, human listeners | 3 | 67% (n=3) | 0% (n=3) | 100% (n=3) |" in st
    assert "| Human lies, model listeners | 0 | n/a (n=0) | n/a (n=0) | n/a (n=0) |" in st
    assert jev.strip() == "No jev labels."
    # no visitor handle or game id in what is published
    for text in (sec, json.dumps(h)):
        assert "Elif" not in text and "fixture-0001" not in text and "p4" not in text


def test_reliability_table_and_leaderboard_carry_the_three_rates(tmp_path):
    out = write_report(_batch(tmp_path))
    md = (out / "report.md").read_text(encoding="utf-8")
    table = md.split("## Cost and reliability")[1].split("\n\n")[1].splitlines()
    head = [c.strip() for c in table[0].strip("|").split("|")]
    assert head == [
        "Model",
        "Invalid attempts",
        "Fallback rate",
        "Retry success",
        "Provider errors",
        "USD per game",
    ]
    board = json.loads((out / "leaderboard.json").read_text())
    metrics = json.loads((out / "metrics.json").read_text())
    for row in board["models"]:
        a = metrics["models"][row["model"]]["all"]
        for key in ("invalid_output_rate", "fallback_rate", "retry_success_rate"):
            assert row[key] == a[key]
        assert row["invalid_attempt_rate"] == row["invalid_output_rate"]
    gia = next(r for r in board["models"] if r["model"] == "gemini-flash-lite")
    assert gia["invalid_attempt_rate"] == pytest.approx(1 / 7)
    gloss = md.split("## leaderboard.json shape")[1]
    assert all(k in gloss for k in ("invalid_attempt_rate", "fallback_rate", "retry_success_rate"))
    Metrics.model_validate(metrics)
