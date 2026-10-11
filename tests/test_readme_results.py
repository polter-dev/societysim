import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("readme_results", ROOT / "scripts/readme_results.py")
rr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rr)

README = (
    f"# title\n\nintro\n\n## Results\n\n{rr.START}\n{rr.PLACEHOLDER}\n{rr.END}\n\n## Next\n\ntail\n"
)


def _row(model, games, seats, win, lie_self, lie_jev, auc=0.7, ordinal=1.5, invalid=0.02):
    return {
        "model": model,
        "games": games,
        "seats": seats,
        "win_rate": win,
        "win_ci": None if win is None else [max(0.0, win - 0.2), min(1.0, win + 0.2)],
        "wolf_win_rate": None,
        "village_win_rate": None,
        "openskill": None if ordinal is None else {"mu": 25.0, "sigma": 7.0, "ordinal": ordinal},
        "detection_auc": auc,
        "detection_brier": None,
        "invalid_output_rate": invalid,
        "cost_usd_per_game": 0.01,
        "lie_rate": {"self_tag": lie_self, "jev": lie_jev},
    }


def _batch(root: Path, name="demo_10p", jev=None, wolf=None, unfinished=2) -> Path:
    report = root / name / "report"
    report.mkdir(parents=True)
    wolf = (
        wolf
        if wolf is not None
        else {
            "overall": {"mu": 0.83, "sigma": 5.63, "ordinal": -16.0},
            "sided": {"mu": 0.8, "sigma": 5.66, "ordinal": -16.2},
        }
    )
    lb = {
        "schema": 1,
        "games": 20,
        "label_sources": ["self_tag", "jev"],
        "wolf_side_advantage": wolf,
        "models": [
            _row("alpha", 3, 30, 0.6, 0.108, jev),
            _row("beta", 2, 20, 0.2, 0.36, jev, auc=None, ordinal=None, invalid=None),
        ],
    }
    metrics = {"games": 20, "unfinished_games": unfinished, "summary": {"cost_usd": 2.158}}
    index = [{"game_id": "g0", "seats": {f"p{i}": {} for i in range(10)}}]
    for name_, data in (("leaderboard", lb), ("metrics", metrics), ("index", index)):
        (report / f"{name_}.json").write_text(json.dumps(data), encoding="utf-8")
    return root / name


def test_block_has_header_table_wolf_side_and_sample_size(tmp_path):
    batch = _batch(tmp_path)
    readme = tmp_path / "README.md"
    readme.write_text(README, encoding="utf-8")
    assert rr.main([str(batch), "--readme", str(readme)]) == 0
    text = readme.read_text(encoding="utf-8")
    assert "### batch `demo_10p`, 10 players, 20 games, spend $2.16" in text
    assert "| alpha | 3 / 30 | 60.0% (40.0% to 80.0%) | 1.50 | 0.70 | 10.8% | n/a | 2.0% |" in text
    assert "| beta | 2 / 20 | 20.0% (0.0% to 40.0%) | n/a | n/a | 36.0% | n/a | n/a |" in text
    assert "Lie rate: self-tag | Lie rate: Jev" in text
    assert "overall pool mu +0.83 (sigma 5.63)" in text
    assert "no clear advantage" in text
    assert "20 finished games (2 more did not finish and are not counted)" in text
    assert "each model filled 20 to 30 seats" in text


def test_only_the_block_changes(tmp_path):
    batch = _batch(tmp_path)
    readme = tmp_path / "README.md"
    readme.write_text(README, encoding="utf-8")
    rr.main([str(batch), "--readme", str(readme)])
    text = readme.read_text(encoding="utf-8")
    head, rest = text.split(rr.START)
    _, tail = rest.split(rr.END)
    assert head == README.split(rr.START)[0]
    assert tail == README.split(rr.END)[1]


def test_idempotent(tmp_path):
    batch = _batch(tmp_path)
    readme = tmp_path / "README.md"
    readme.write_text(README, encoding="utf-8")
    assert rr.update_readme(readme, [batch]) is True
    first = readme.read_bytes()
    assert rr.update_readme(readme, [batch]) is False
    assert readme.read_bytes() == first


def test_no_runs_leaves_placeholder(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text(README.replace(rr.PLACEHOLDER, "old numbers"), encoding="utf-8")
    assert rr.update_readme(readme, []) is True
    assert rr.PLACEHOLDER in readme.read_text(encoding="utf-8")
    assert "old numbers" not in readme.read_text(encoding="utf-8")
    assert rr.update_readme(readme, []) is False


def test_two_batches_and_jev_side_by_side(tmp_path):
    a = _batch(tmp_path, "one", jev=0.05)
    b = _batch(tmp_path, "two", unfinished=0)
    out = rr.render([a, b])
    assert out.index("batch `one`") < out.index("batch `two`")
    assert "| 10.8% | 5.0% |" in out
    assert "did not finish" not in out.split("batch `two`")[1]


def test_clear_wolf_advantage_and_missing_advantage(tmp_path):
    strong = {"overall": {"mu": 12.0, "sigma": 4.0, "ordinal": 0.0}}
    assert "Clearly away from 0." in rr.render([_batch(tmp_path, "s", wolf=strong)])
    assert "not available" in rr._wolf_line({})


def test_reads_cp1252_report_and_missing_markers(tmp_path):
    batch = _batch(tmp_path)
    lb = batch / "report" / "leaderboard.json"
    lb.write_bytes(lb.read_bytes().replace(b"alpha", "al’pha".encode("cp1252")))
    assert "al’pha" in rr.render([batch])
    readme = tmp_path / "README.md"
    readme.write_text("# no markers\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        rr.update_readme(readme, [batch])


def test_missing_report_is_an_error_not_a_crash(tmp_path, capsys):
    readme = tmp_path / "README.md"
    readme.write_text(README, encoding="utf-8")
    assert rr.main([str(tmp_path / "nope"), "--readme", str(readme)]) == 1
    assert readme.read_text(encoding="utf-8") == README
