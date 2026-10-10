import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from societysim.analysis import jev as jev_mod
from societysim.analysis.jev import JevResult
from societysim.analysis.labeler import (
    JevReport,
    JevVerdict,
    jev_verdicts,
    label_batch,
    label_game,
    read_log,
)
from societysim.cli import app
from societysim.logging.events import EventType, Label

ROOT = Path(__file__).parents[2]
FIXTURE = ROOT / "fixtures/sample_game.jsonl"
PLANTED = ROOT / "fixtures/sample_game.labels.jsonl"


def _key(lb: Label) -> tuple:
    return (lb.game_id, lb.label, lb.actor, lb.source_seq, lb.source, lb.confidence)


def _keys(labels) -> set[tuple]:
    return {(lb.label.value, lb.actor, lb.source_seq, lb.source) for lb in labels}


def _events():
    return read_log(FIXTURE)


def _jev_from_self_tags(events) -> dict[int, JevVerdict]:
    """A Jev that reads every statement exactly as the agent tagged it."""
    jev = {e.seq: JevVerdict() for e in events if e.type is EventType.STATEMENT}
    for e in events:
        v = jev.get(e.meta.statement_seq)
        if e.type is EventType.ACCUSATION:
            v.accuse.append(e.target)
        elif e.type is EventType.ROLE_CLAIM:
            v.claim_role = e.meta.role
        elif e.type is EventType.SEER_CLAIM:
            v.seer_report = JevReport(target=e.target, result=e.meta.result)
    return jev


def test_fixture_finds_exactly_the_planted_labels():
    planted = [Label.model_validate_json(line) for line in PLANTED.read_text().splitlines()]
    found = label_game(_events())
    assert sorted(map(_key, found)) == sorted(map(_key, planted))
    assert all(lb.detail for lb in found)


def test_real_seer_contradicting_report_is_false_report():
    events = _events()
    events[31].meta.result = "not_wolf"  # Ava really checked Cleo as wolf
    assert ("false_report", "p0", 31, "self_tag") in _keys(label_game(events))


def test_real_seer_matching_report_is_not_labelled():
    assert not any(lb.actor == "p0" and lb.label == "false_report" for lb in label_game(_events()))


def test_wolf_vote_for_fellow_wolf_is_bussing():
    events = _events()
    events[46].target = "p5"  # Cleo (wolf) votes Finn (wolf)
    assert ("bussing", "p2", 46, "engine") in _keys(label_game(events))


def test_reporting_the_wolf_clears_hidden_knowledge():
    events = _events()
    events[31].meta.statement_seq = 16  # Ava reports Cleo in her first statement
    assert not any(lb.label == "hidden_knowledge" for lb in label_game(events))


def test_eliminating_a_wolf_is_not_mis_elimination():
    events = _events()
    events[104].target = "p5"
    assert not any(lb.label == "mis_elimination" for lb in label_game(events))


def test_jev_labels_and_agreement(tmp_path):
    events = _events()
    jev = _jev_from_self_tags(events)
    jev[69].accuse = []  # Jev misses Cleo bussing Finn
    jev[15].claim_role = "seer"  # and hears a false Seer claim from Elif
    labels = label_game(events, jev)
    jev_keys = {k for k in _keys(labels) if k[3] == "jev"}
    assert jev_keys == {
        ("hidden_knowledge", "p0", 16, "jev"),
        ("false_role_claim", "p5", 17, "jev"),
        ("false_report", "p5", 17, "jev"),
        ("false_role_claim", "p4", 15, "jev"),
    }

    shutil.copy(FIXTURE, tmp_path / "fixture-0001.jsonl")
    table = label_batch(tmp_path, jev_for=lambda _: jev)
    assert table["false_role_claim"] == {
        "both": 1,
        "self_tag_only": 0,
        "jev_only": 1,
        "agreement": 0.5,
    }
    assert table["bussing"]["self_tag_only"] == 1 and table["bussing"]["agreement"] == 0
    assert table["false_report"]["agreement"] == 1.0
    assert table["hidden_knowledge"]["agreement"] == 1.0
    assert table["mis_elimination"]["agreement"] is None
    assert json.loads((tmp_path / "agreement.json").read_text()) == table


@pytest.fixture
def no_key(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    monkeypatch.setattr("societysim.cli.load_dotenv", lambda: None)


def _result(seq=15, actor="p4", **kw) -> JevResult:
    """A JevResult for Elif's (p4) statement; nothing above 0.5 unless overridden."""
    base = dict(
        game_id="fixture-0001",
        seq=seq,
        actor=actor,
        accuse={"p0": 0.1},
        claim_role=None,
        claim_role_confidence=0.9,
        seer_report=0.1,
        seer_target="p0",
        seer_result="wolf",
    )
    return JevResult(**base | kw)


def test_jev_verdicts_thresholds():
    v = jev_verdicts(
        [
            _result(
                accuse={"p0": 0.51, "p1": 0.5, "p2": 0.49}, claim_role="seer", seer_report=0.51
            ),
            _result(seq=16, actor="p0", seer_report=0.5, claim_role_confidence=0.6),
        ]
    )
    assert v[15].accuse == ["p0"]  # 0.5 itself is not a yes
    assert v[15].claim_role == "seer" and v[15].confidence == 0.9
    assert v[15].seer_report == JevReport(target="p0", result="wolf")
    assert v[16] == JevVerdict(confidence=0.6)  # seer_report == 0.5 -> None


def test_empty_log_is_skipped(tmp_path, capsys):
    shutil.copy(FIXTURE, tmp_path / "fixture-0001.jsonl")
    (tmp_path / "empty.jsonl").write_text("")
    label_batch(tmp_path)
    assert "empty.jsonl is empty" in capsys.readouterr().out
    assert (tmp_path / "fixture-0001.labels.jsonl").exists()
    assert not (tmp_path / "empty.labels.jsonl").exists()


def test_cli_with_key_runs_jev_and_writes_agreement(tmp_path, monkeypatch, no_key):
    monkeypatch.setenv("TYPESAFE_API_KEY", "fake")
    seen = []

    async def fake_classify(events):
        seen.append(events[0].game_id)
        return [_result(claim_role="seer")]  # Jev hears Elif claim Seer

    monkeypatch.setattr(jev_mod, "classify_events", fake_classify)
    shutil.copy(FIXTURE, tmp_path / "fixture-0001.jsonl")

    result = CliRunner().invoke(app, ["label", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert seen == ["fixture-0001"]
    table = json.loads((tmp_path / "agreement.json").read_text())
    assert table["false_role_claim"]["jev_only"] == 1
    labels = (tmp_path / "fixture-0001.labels.jsonl").read_text()
    assert '"source":"jev"' in labels


def test_cli_no_jev_flag_skips_jev(tmp_path, monkeypatch, no_key):
    monkeypatch.setenv("TYPESAFE_API_KEY", "fake")

    async def boom(events):
        raise AssertionError("Jev called")

    monkeypatch.setattr(jev_mod, "classify_events", boom)
    shutil.copy(FIXTURE, tmp_path / "fixture-0001.jsonl")

    result = CliRunner().invoke(app, ["label", str(tmp_path), "--no-jev"])

    assert result.exit_code == 0, result.output
    assert "Jev skipped (--no-jev)" in result.output
    assert not (tmp_path / "agreement.json").exists()


def test_cli_writes_labels_next_to_game_and_leaves_raw_log_alone(tmp_path, no_key):
    game = tmp_path / "fixture-0001.jsonl"
    shutil.copy(FIXTURE, game)
    before = game.read_bytes()

    result = CliRunner().invoke(app, ["label", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "Jev skipped (TYPESAFE_API_KEY not set)" in result.output
    assert game.read_bytes() == before
    written = (tmp_path / "fixture-0001.labels.jsonl").read_text().splitlines()
    assert len(written) == len(PLANTED.read_text().splitlines())
    assert not (tmp_path / "agreement.json").exists()
    # re-running does not label the labels file as a game
    assert CliRunner().invoke(app, ["label", str(tmp_path)]).exit_code == 0
    assert not (tmp_path / "fixture-0001.labels.labels.jsonl").exists()
