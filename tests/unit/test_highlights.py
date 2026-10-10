"""Highlights on the hand-written fixture and on small edits of it. No API calls."""

import json
import shutil
from pathlib import Path

from typer.testing import CliRunner

from societysim.analysis.highlights import (
    find_highlights,
    lie_moments,
    role_action_moments,
    wolf_survival_moments,
)
from societysim.analysis.metrics import GameData, load_game
from societysim.cli import app
from societysim.logging.events import EventMeta, Label, LabelType

ROOT = Path(__file__).parents[2]
FIXTURE = ROOT / "fixtures/sample_game.jsonl"
# fixture seats: p0 seer, p2 wolf (Cleo), p3 doctor, p5 wolf (Finn), p6 villager (Gia)


def game() -> GameData:
    return load_game(FIXTURE)


def edit(g: GameData, seq: int, **update) -> None:
    g.events[seq] = g.events[seq].model_copy(update=update)


def kinds(moments):
    return [(m["type"], m["actor"], m["seq"][0]) for m in moments]


def test_fixture_moments_and_order():
    ms = find_highlights([game()])
    assert kinds(ms) == [
        ("lie_moved_vote", "p2", 70),  # bussing Finn: p3 and p6 voted Finn
        ("doctor_save", "p3", 12),
        ("wolf_survived_vote", "p2", 70),  # Gia went out instead
        ("lie_moved_vote", "p5", 19),
        ("lie_moved_vote", "p5", 20),
        ("wolf_survived_vote", "p5", 19),
    ]
    assert [m["score"] for m in ms] == [5.0, 3.0, 3.0, 2.5, 2.0, 1.0]
    top = ms[0]
    assert top["seq"] == [70, 104] and top["day"] == 2
    assert (top["actor_name"], top["actor_model"]) == ("Cleo", "gemma-4-26b")
    assert (top["target"], top["target_name"], top["target_model"]) == (
        "p5",
        "Finn",
        "gemini-flash",
    )
    assert "2/4 village voters followed" in top["description"]
    assert top["link"] == "?game=fixture-0001&seq=70&mode=narrator"


def test_lie_without_followers_is_not_a_moment():
    g = game()
    for seq in (100, 103):  # p3 and p6 voted Finn after Cleo bussed him; now nobody does
        edit(g, seq, target="p6")
    assert ("lie_moved_vote", "p2", 70) not in kinds(lie_moments(g))


def test_lie_labeled_by_both_sources_is_one_moment():
    g = game()
    bus = next(lb for lb in g.labels if lb.label is LabelType.BUSSING)
    g.labels.append(bus.model_copy(update={"source": "jev"}))
    assert [m["seq"][0] for m in lie_moments(g)].count(70) == 1


def test_engine_labels_are_not_lies():
    g = game()
    g.labels = [lb for lb in g.labels if lb.source == "engine"]
    assert lie_moments(g) == [] and wolf_survival_moments(g) == []


def test_wolf_voted_out_after_lying_is_not_a_survival():
    g = game()
    for seq in (98, 99, 101, 102):
        edit(g, seq, target="p2")
    edit(g, 104, target="p2", meta=EventMeta(role="wolf"))
    assert ("wolf_survived_vote", "p2", 70) not in kinds(wolf_survival_moments(g))


def test_wolf_survival_counts_votes_against():
    g = game()
    edit(g, 44, target="p5")
    edit(g, 45, target="p5")
    (m,) = [m for m in wolf_survival_moments(g) if m["actor"] == "p5"]
    assert m["score"] == 3.0 and "2 votes against" in m["description"]
    assert m["seq"] == [19, 54]


def test_seer_led_elimination():
    g = game()  # p0 checked Finn (p5) as a wolf on night 2; make day 2 vote Finn out
    for seq in (98, 99, 101, 103):
        edit(g, seq, target="p5")
    edit(g, 104, target="p5", meta=EventMeta(role="wolf"))
    (m,) = [m for m in role_action_moments(g) if m["type"] == "seer_led_elimination"]
    assert (m["actor"], m["target"], m["seq"], m["score"]) == ("p0", "p5", [64, 104], 7.0)


def test_failed_doctor_save_is_not_a_moment():
    g = game()
    edit(g, 12, meta=EventMeta(saved=False))
    assert [m for m in role_action_moments(g) if m["type"] == "doctor_save"] == []


def test_ties_order_by_game_id_then_seq():
    a, b = game(), game()
    b.events = [e.model_copy(update={"game_id": "fixture-0000"}) for e in b.events]
    b.labels = [
        Label.model_validate(lb.model_dump() | {"game_id": "fixture-0000"}) for lb in b.labels
    ]
    ms = find_highlights([a, b])
    assert [m["game_id"] for m in ms[:2]] == ["fixture-0000", "fixture-0001"]
    assert find_highlights([b, a]) == ms


def test_cli_writes_highlights_json(tmp_path):
    shutil.copy(FIXTURE, tmp_path / FIXTURE.name)
    shutil.copy(FIXTURE.with_name("sample_game.labels.jsonl"), tmp_path)
    res = CliRunner().invoke(app, ["highlights", str(tmp_path), "--top", "2"])
    assert res.exit_code == 0, res.output
    assert "lie_moved_vote" in res.output and "?game=fixture-0001&seq=70" in res.output
    data = json.loads((tmp_path / "highlights.json").read_text())
    assert [m["type"] for m in data] == ["lie_moved_vote", "doctor_save"]


def test_cli_out_writes_elsewhere(tmp_path):
    out = tmp_path / "h.json"
    res = CliRunner().invoke(app, ["highlights", str(FIXTURE.parent), "--out", str(out)])
    assert res.exit_code == 0, res.output
    assert len(json.loads(out.read_text())) == 6
    assert not (FIXTURE.parent / "highlights.json").exists()
