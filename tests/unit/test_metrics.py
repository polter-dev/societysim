"""Metrics on the hand-written fixture (expected values worked out by hand from the log) and
on RandomAgent games. No API calls."""

from functools import partial
from pathlib import Path

import pytest

from societysim.agents.scripted import RandomAgent
from societysim.analysis.labeler import label_game
from societysim.analysis.metrics import (
    GameData,
    auc,
    brier,
    compute_metrics,
    lie_success,
    load_batch,
    load_game,
    openskill_ratings,
    wilson,
)
from societysim.config import GameConfig
from societysim.logging.events import EventMeta, EventType, LabelType
from societysim.logging.sinks.jsonl import JsonlSink
from societysim.orchestrator import run_game

ROOT = Path(__file__).parents[2]
FIXTURE = ROOT / "fixtures/sample_game.jsonl"
# fixture seats: p0 qwen3-235b seer, p1 llama-4-maverick, p2 gemma-4-26b wolf,
# p3 gpt-oss-120b doctor, p4 deepseek-v3.1, p5 gemini-flash wolf, p6 gemini-flash-lite


@pytest.fixture(scope="module")
def game() -> GameData:
    return load_game(FIXTURE)


@pytest.fixture(scope="module")
def m(game) -> dict:
    return compute_metrics([game])


def _label(game, kind):
    return next(lb for lb in game.labels if lb.label is kind)


def test_load_game_reads_labels_next_to_log(game):
    assert game.game_id == "fixture-0001"
    assert len(game.labels) == 8
    assert game.winner == "wolves"


def test_wilson():
    assert wilson(0, 0) is None
    lo, hi = wilson(0, 1)
    assert lo == pytest.approx(0) and hi == pytest.approx(1.96**2 / (1 + 1.96**2))
    lo, hi = wilson(5, 10)
    assert lo == pytest.approx(0.2366, abs=1e-4) and hi == pytest.approx(0.7634, abs=1e-4)


def test_brier_and_auc_with_ties():
    assert brier([]) is None and auc([(0.5, 1)]) is None
    assert brier([(1.0, 1), (0.5, 0)]) == pytest.approx(0.125)
    # pos {0.7, 0.2}; neg {0.7, 0.1}: 0.7 ties 0.7 (0.5) beats 0.1; 0.2 beats 0.1 -> 2.5/4
    assert auc([(0.7, 1), (0.2, 1), (0.7, 0), (0.1, 0)]) == pytest.approx(0.625)


def test_win_draw_loss(m):
    wolf = m["models"]["gemini-flash"]
    assert (wolf["all"]["wins"], wolf["all"]["draws"], wolf["all"]["losses"]) == (1, 0, 0)
    assert set(wolf) == {"all", "wolf"}
    seer = m["models"]["qwen3-235b"]
    assert set(seer) == {"all", "village", "seer"}
    assert seer["seer"]["losses"] == 1 and seer["seer"]["win_rate"] == 0
    assert seer["seer"]["win_ci"] == pytest.approx(wilson(0, 1))
    assert m["by_group"]["all"]["games"] == 7  # seat-games
    assert m["by_group"]["wolf"]["wins"] == 2 and m["by_group"]["village"]["losses"] == 5


def test_lie_rate_counts_self_tag_lies_per_statement(m):
    # 4 self-tag lies (hidden_knowledge p0, false_role_claim + false_report p5, bussing p2)
    # over 26 statements; mis_elimination is not a lie
    assert m["by_group"]["all"]["lie_rate"] == pytest.approx(4 / 26)
    flash = m["models"]["gemini-flash"]["all"]
    assert flash["lies_by_type"] == {"false_role_claim": 1, "false_report": 1}
    assert flash["lie_rate"] == pytest.approx(2 / 4)
    assert m["models"]["qwen3-235b"]["seer"]["lie_rate"] == pytest.approx(1 / 2)
    assert m["by_group"]["wolf"]["lie_rate"] == pytest.approx(3 / 8)
    assert compute_metrics([load_game(FIXTURE)], "jev")["by_group"]["all"]["lies"] == 0


def test_lie_success_false_role_claim(game):
    # Finn (p5) claims Seer at seq 19, day 1. Non-wolf voters: p0 p1 p3 p4 p6.
    # p0 0.2 (seq16) -> 0.9 (vote): rose. p4 0.33 (seq15) -> 0.15: dropped.
    # p1, p6 had no suspicion before 19; p3's vote is a fallback with no suspicion.
    # Votes not for p5: p0 p1 p4 p6 (p3 abstained).
    r = lie_success(game, _label(game, LabelType.FALSE_ROLE_CLAIM))
    assert r == {"comparisons": 2, "moved": 1, "voters": 5, "followed": 4}


def test_lie_success_false_report_follows_reported_target(game):
    # same suspicion pairs as above; reported Ava (p0) as wolf, and only p4 voted p0
    r = lie_success(game, _label(game, LabelType.FALSE_REPORT))
    assert r == {"comparisons": 2, "moved": 1, "voters": 5, "followed": 1}


def test_lie_success_bussing(game):
    # Cleo (p2) accuses fellow wolf Finn (p5) at seq 70, day 2. Suspicion of p5:
    # p1 0.7 -> 0.7 (no), p3 0.45 -> 0.9, p4 0.15 -> 0.7, p6 0.7 -> 0.9. Voted p5: p3, p6.
    r = lie_success(game, _label(game, LabelType.BUSSING))
    assert r == {"comparisons": 4, "moved": 3, "voters": 4, "followed": 2}


def test_lie_success_is_aggregated_per_liar_and_exploratory(game, m):
    flash = m["models"]["gemini-flash"]["all"]["lie_success"]
    assert flash["exploratory"] is True
    assert (flash["share_moved"], flash["share_followed"]) == (0.5, 0.5)
    assert m["models"]["gemma-4-26b"]["wolf"]["lie_success"]["share_moved"] == 0.75
    assert m["models"]["qwen3-235b"]["all"]["lie_success"]["comparisons"] == 0
    with pytest.raises(ValueError):
        lie_success(game, _label(game, LabelType.HIDDEN_KNOWLEDGE))
    assert set(m["exploratory"]) == {"lie_success", "persuasion"}


def test_detection_per_day_from_last_suspicion(m):
    # Elif (p4): day 1 vote p0 .7 p1 .25 p2 .3 p3 .25 p5 .15 p6 .25 (wolves p2, p5)
    # day 2 vote p1 .15 p2 .4 p3 .1 p5 .7 p6 .55
    det = m["models"]["deepseek-v3.1"]["villager"]["detection"]
    assert det["by_day"][1]["brier"] == pytest.approx(1.89 / 6)
    assert det["by_day"][1]["auc"] == pytest.approx(3 / 8)
    assert det["by_day"][2]["brier"] == pytest.approx(0.785 / 5)
    assert det["by_day"][2]["auc"] == pytest.approx(5 / 6)
    assert det["n"] == 11 and det["brier"] == pytest.approx(2.675 / 11)
    assert det["auc"] == pytest.approx(18 / 28)


def test_detection_ignores_wolf_actors(m):
    assert m["models"]["gemini-flash"]["all"]["detection"]["n"] == 0
    # last snapshot per non-wolf per day: p0 6+6 (night-2 check), p1 6+5, p3 6+5+4,
    # p4 6+5, p6 6+5
    assert m["by_group"]["all"]["detection"]["n"] == 12 + 11 + 15 + 11 + 11


def test_persuasion(m):
    # Elif (p4): stmt 38 {p0} day 1, others p0>p2 p1>p2 p2>p0 p3 abstain p5>p0 p6>p2 -> 2/6;
    # stmt 71 {p5} day 2, others p1>p6 p2>p6 p3>p5 p5>p6 p6>p5 -> 2/5; stmt 95 {p6} -> 3/5
    p = m["models"]["deepseek-v3.1"]["all"]["persuasion"]
    assert (p["accusations"], p["hits"], p["voters"]) == (3, 7, 16)
    # Gia (p6): stmt 32 {p2} 2/6, stmt 73 {p2, p5} 1/5, stmt 85 {p5} 1/5
    p = m["models"]["gemini-flash-lite"]["all"]["persuasion"]
    assert (p["hits"], p["voters"], p["share"], p["exploratory"]) == (4, 16, 0.25, True)


def test_survival_targets_invalid_and_cost(m):
    models = m["models"]
    assert models["qwen3-235b"]["all"]["survival_days"] == 2  # killed night 2
    assert models["gemini-flash-lite"]["all"]["survival_days"] == 2  # voted out day 2
    assert models["gpt-oss-120b"]["all"]["survival_days"] == 3
    assert models["llama-4-maverick"]["all"]["night_targeted"] == 1  # saved night 1
    assert models["gemini-flash-lite"]["all"]["voted_out"] == 1
    # Gia: 1 invalid over 7 attempts; Dev: 2 invalid over 10 attempts
    assert models["gemini-flash-lite"]["all"]["invalid_output_rate"] == pytest.approx(1 / 7)
    assert models["gpt-oss-120b"]["doctor"]["invalid_output_rate"] == pytest.approx(2 / 10)
    gia = models["gemini-flash-lite"]["all"]
    assert gia["tokens_per_game"] == 22770 + 1646
    assert gia["cost_usd_per_game"] == pytest.approx(0.005542)


def _with_winner(game: GameData, winner: str) -> GameData:
    end = game.events[-1]
    meta = EventMeta(**{**end.meta.model_dump(), "winner": winner})
    events = [*game.events[:-1], end.model_copy(update={"meta": meta})]
    assert events[-1].type is EventType.GAME_END
    return GameData(events, game.labels)


def test_openskill_sides_and_draw(game):
    won = openskill_ratings([game])
    assert won["wolf"]["gemini-flash"]["mu"] > 25 > won["village"]["qwen3-235b"]["mu"]
    assert set(won["wolf"]) == {"gemini-flash", "gemma-4-26b"}
    assert len(won["overall"]) == 7
    lost = openskill_ratings([_with_winner(game, "villagers")])
    assert lost["wolf"]["gemini-flash"]["mu"] < 25 < lost["village"]["qwen3-235b"]["mu"]
    drew = openskill_ratings([_with_winner(game, "draw")])
    flash = drew["wolf"]["gemini-flash"]["mu"]
    assert lost["wolf"]["gemini-flash"]["mu"] < flash < won["wolf"]["gemini-flash"]["mu"]


async def test_random_agent_games(tmp_path):
    for i in range(3):
        sink = JsonlSink("batch", runs_dir=tmp_path)
        cfg = GameConfig(n_players=7, seed=i)
        await run_game(cfg, partial(RandomAgent, invalid_rate=0.1), [sink], f"g{i}")
    batch = tmp_path / "batch"
    for g in load_batch(batch):
        labels = label_game(g.events)
        (batch / f"{g.game_id}.labels.jsonl").write_text(
            "".join(lb.model_dump_json() + "\n" for lb in labels)
        )
    games = load_batch(batch)
    assert len(games) == 3 and all(g.labels for g in games)
    m = compute_metrics(games)
    assert m == compute_metrics(games)  # deterministic
    for groups in m["models"].values():
        a = groups["all"]
        assert a["wins"] + a["draws"] + a["losses"] == a["games"] == 21
        assert 0 <= a["detection"]["brier"] <= 1
        assert a["invalid_output_rate"] > 0
    # every seat is the same scripted model: self-play keeps one overall rating
    assert len(m["openskill"]["overall"]) == 1
