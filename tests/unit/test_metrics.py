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
    deception_matrix,
    lie_success,
    load_batch,
    load_game,
    openskill_ratings,
    wilson,
)
from societysim.config import GameConfig
from societysim.logging.events import (
    Event,
    EventMeta,
    EventType,
    Label,
    LabelType,
    Seat,
    Suspicion,
    Visibility,
)
from societysim.logging.sinks.jsonl import JsonlSink
from societysim.orchestrator import run_game
from societysim.roles import Phase, Role

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


def _self(summary: dict) -> dict:
    return summary["lies"]["self_tag"]


def test_lie_rate_counts_self_tag_lies_per_statement(m):
    # 4 self-tag lies (hidden_knowledge p0, false_role_claim + false_report p5, bussing p2)
    # over 26 statements; mis_elimination is not a lie
    assert _self(m["by_group"]["all"])["lie_rate"] == pytest.approx(4 / 26)
    flash = _self(m["models"]["gemini-flash"]["all"])
    assert flash["by_type"] == {"false_role_claim": 1, "false_report": 1}
    assert flash["lie_rate"] == pytest.approx(2 / 4)
    assert _self(m["models"]["qwen3-235b"]["seer"])["lie_rate"] == pytest.approx(1 / 2)
    assert _self(m["by_group"]["wolf"])["lie_rate"] == pytest.approx(3 / 8)
    assert m["by_group"]["all"]["lies"]["jev"]["lies"] == 0


def test_lie_sources_are_reported_side_by_side_not_summed(game):
    # Jev agrees on the role claim only, and adds a hidden_knowledge of its own on p0
    jev = [
        lb.model_copy(update={"source": "jev"})
        for lb in game.labels
        if lb.label in (LabelType.FALSE_ROLE_CLAIM, LabelType.HIDDEN_KNOWLEDGE)
    ]
    m = compute_metrics([GameData(game.events, game.labels + jev)])
    flash = m["models"]["gemini-flash"]["all"]["lies"]
    assert flash["self_tag"]["lies"] == 2 and flash["jev"]["lies"] == 1
    assert flash["jev"]["by_type"] == {"false_role_claim": 1}
    assert flash["jev"]["lie_success"]["voters"] == 5  # the role claim alone
    assert m["by_group"]["all"]["lies"]["jev"]["lie_rate"] == pytest.approx(2 / 26)
    assert m["by_group"]["all"]["lies"]["self_tag"]["lie_rate"] == pytest.approx(4 / 26)


def test_lie_success_false_role_claim(game):
    # Finn (p5) claims Seer at seq 19, day 1. Non-wolf voters: p0 p1 p3 p4 p6.
    # p0 0.2 (seq16) -> 0.9 (vote): rose. p4 0.33 (seq15) -> 0.15: dropped.
    # p1, p6 had no suspicion before 19; p3's vote is a fallback with no suspicion.
    # Votes not for p5: p0 p1 p4 p6 (p3 abstained).
    # Base: day 2 (p5 alive), voters p1>p6 p3>p5 p4>p6 p6>p5: not for p5 = 2 of 4.
    r = lie_success(game, _label(game, LabelType.FALSE_ROLE_CLAIM))
    assert r == {
        "comparisons": 2,
        "moved": 1,
        "voters": 5,
        "followed": 4,
        "base_voters": 4,
        "base_followed": 2,
        "base_abstained": 0,
    }


def test_base_rate_zero_when_the_only_base_day_is_all_abstains(game):
    # The real smoke-batch gemma 0%: the one base day had nobody voting, and an abstain never
    # follows the lie, so the base rate is 0 on a handful of voters. It is arithmetic, not a bug.
    events = [
        e.model_copy(update={"target": None}) if e.type is EventType.VOTE and e.day == 2 else e
        for e in game.events
    ]
    r = lie_success(GameData(events, game.labels), _label(game, LabelType.FALSE_ROLE_CLAIM))
    assert (r["base_voters"], r["base_followed"], r["base_abstained"]) == (4, 0, 4)


def _second_role_claim(game):
    # a second false_role_claim by the same liar (p5) on day 1, a later statement
    first = _label(game, LabelType.FALSE_ROLE_CLAIM)
    return first, first.model_copy(update={"source_seq": first.source_seq + 1})


def test_base_voters_are_counted_once_per_liar_type_source_day_voter(game):
    first, second = _second_role_claim(game)
    two = GameData(game.events, [first, second])
    seen: set = set()
    a, b = lie_success(two, first, seen), lie_success(two, second, seen)
    assert a["base_voters"] == 4 and b["base_voters"] == 0
    # without the shared set each label counts the same four voters again
    assert lie_success(two, second)["base_voters"] == 4


def test_two_labels_of_one_type_do_not_double_the_base(game):
    first, second = _second_role_claim(game)
    one = compute_metrics([GameData(game.events, [first])])
    two = compute_metrics([GameData(game.events, [first, second])])

    def base(m):
        ls = _self(m["models"]["gemini-flash"]["all"])["lie_success"]
        return ls["base_voters"], ls["base_followed"]

    assert base(one) == base(two) == (4, 2)


def test_other_lie_types_and_sources_keep_their_own_base(game):
    first, _ = _second_role_claim(game)
    other_source = first.model_copy(update={"source": "jev"})
    both = GameData(game.events, [first, other_source])
    seen: set = set()
    a, b = lie_success(both, first, seen), lie_success(both, other_source, seen)
    assert a["base_voters"] == b["base_voters"] == 4


def test_lie_success_false_report_follows_reported_target(game):
    # same suspicion pairs as above; reported Ava (p0) as wolf, and only p4 voted p0.
    # No base day: Ava is dead by the day-2 vote.
    r = lie_success(game, _label(game, LabelType.FALSE_REPORT))
    assert r == {
        "comparisons": 2,
        "moved": 1,
        "voters": 5,
        "followed": 1,
        "base_voters": 0,
        "base_followed": 0,
        "base_abstained": 0,
    }


def test_lie_success_bussing(game):
    # Cleo (p2) accuses fellow wolf Finn (p5) at seq 70, day 2. Suspicion of p5:
    # p1 0.7 -> 0.7 (no), p3 0.45 -> 0.9, p4 0.15 -> 0.7, p6 0.7 -> 0.9. Voted p5: p3, p6.
    # Base: day 1, non-wolf voters p0>p2 p1>p2 p3 abstain p4>p0 p6>p2: none for p5.
    r = lie_success(game, _label(game, LabelType.BUSSING))
    assert r == {
        "comparisons": 4,
        "moved": 3,
        "voters": 4,
        "followed": 2,
        "base_voters": 5,
        "base_followed": 0,
        "base_abstained": 1,
    }


def test_lie_success_is_aggregated_per_liar_and_exploratory(game, m):
    flash = _self(m["models"]["gemini-flash"]["all"])["lie_success"]
    assert flash["exploratory"] is True
    assert (flash["share_moved"], flash["share_followed"], flash["base_rate"]) == (0.5, 0.5, 0.5)
    gemma = _self(m["models"]["gemma-4-26b"]["wolf"])["lie_success"]
    assert (gemma["share_moved"], gemma["base_rate"]) == (0.75, 0)
    assert _self(m["models"]["qwen3-235b"]["all"])["lie_success"]["comparisons"] == 0
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


def test_provider_errors_are_reported_apart_from_invalid_output(game):
    """Dev (p3, gpt-oss-120b) loses a call to a 503: not an invalid reply, not a model attempt."""
    base = compute_metrics([game])["models"]["gpt-oss-120b"]["all"]
    assert base["provider_errors"] == 0 and base["provider_error_rate"] == 0
    last = game.events[-1]
    usage = {"model": "gpt-oss-120b", "attempts": 6}
    common = dict(game_id=last.game_id, t=last.t, day=last.day, phase=last.phase, actor="p3")
    failed = EventMeta(action="vote", attempt=1, error="ServiceUnavailableError", reason="provider")
    extra = [
        Event(
            seq=last.seq + 1,
            type=EventType.INVALID_OUTPUT,
            visibility=Visibility.NARRATOR,
            meta=failed,
            **common,
        ),
        Event(
            seq=last.seq + 2,
            type=EventType.FALLBACK_ACTION,
            visibility=Visibility.NARRATOR,
            meta=EventMeta(action="vote", reason="provider", usage=usage),
            **common,
        ),
    ]
    row = compute_metrics([GameData([*game.events, *extra], game.labels)])
    row = row["models"]["gpt-oss-120b"]["all"]
    assert row["invalid_output_rate"] == base["invalid_output_rate"]  # unchanged
    assert row["provider_errors"] == 1
    assert row["provider_error_rate"] > 0


def test_pre_reason_provider_errors_are_classified_by_error_text(game):
    """T5.2 logs have no reason field; their provider failures start with the exception name."""
    last = game.events[-1]
    common = dict(game_id=last.game_id, t=last.t, day=last.day, phase=last.phase, actor="p3")
    base = compute_metrics([game])["models"]["gpt-oss-120b"]["all"]

    def run(error):
        meta = EventMeta(action="vote", attempt=1, error=error)
        ev = Event(
            seq=last.seq + 1,
            type=EventType.INVALID_OUTPUT,
            visibility=Visibility.NARRATOR,
            meta=meta,
            **common,
        )
        got = compute_metrics([GameData([*game.events, ev], game.labels)])
        return got["models"]["gpt-oss-120b"]["all"]

    legacy = run("ServiceUnavailableError: HuggingfaceException - busy")
    assert legacy["provider_errors"] == 1
    assert legacy["invalid_output_rate"] == base["invalid_output_rate"]
    model_error = run("1 validation error for VoteResponse")
    assert model_error["provider_errors"] == 0
    assert model_error["invalid_output_rate"] > base["invalid_output_rate"]


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
    assert won["wolf_side"]["sided"]["mu"] > 0  # the dummy shares the wolves' win
    lost = openskill_ratings([_with_winner(game, "villagers")])
    assert lost["wolf"]["gemini-flash"]["mu"] < 25 < lost["village"]["qwen3-235b"]["mu"]
    drew = openskill_ratings([_with_winner(game, "draw")])
    flash = drew["wolf"]["gemini-flash"]["mu"]
    assert lost["wolf"]["gemini-flash"]["mu"] < flash < won["wolf"]["gemini-flash"]["mu"]


def _with_models(game: GameData, models: dict[str, str]) -> GameData:
    start = game.events[0]
    seats = {
        s: seat.model_copy(update={"model": models.get(s, seat.model)})
        for s, seat in start.meta.seats.items()
    }
    meta = start.meta.model_copy(update={"seats": seats})
    return GameData([start.model_copy(update={"meta": meta}), *game.events[1:]], game.labels)


def test_openskill_ignores_how_many_distinct_models_a_side_has(game):
    # same outcomes, wolves are always the same two models; the village side is either
    # five distinct models or two (A on three seats, B on two)
    five = {"p0": "v1", "p1": "v2", "p3": "v3", "p4": "v4", "p6": "v5"}
    two = {"p0": "A", "p1": "A", "p3": "A", "p4": "B", "p6": "B"}
    outcomes = ["wolves", "villagers", "wolves", "draw"]

    def run(models):
        games = [_with_models(_with_winner(game, w), models) for w in outcomes]
        for i, g in enumerate(games):  # distinct ids keep the rating order
            g.events = [e.model_copy(update={"game_id": f"g{i}"}) for e in g.events]
        return openskill_ratings(games)

    a, b = run(five), run(two)
    for pool in ("overall", "wolf"):
        for wolf in ("gemini-flash", "gemma-4-26b"):
            assert a[pool][wolf]["mu"] == pytest.approx(b[pool][wolf]["mu"])
            assert a[pool][wolf]["sigma"] == pytest.approx(b[pool][wolf]["sigma"])
    assert a["wolf_side"]["sided"]["mu"] == pytest.approx(b["wolf_side"]["sided"]["mu"])
    # every village model gets the same rating whichever way the seats are split
    assert a["village"]["v1"]["mu"] == pytest.approx(b["village"]["A"]["mu"])


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


def test_human_seat_is_not_ranked():
    game = load_game(FIXTURE)
    game.events[0].meta.seats["p4"].model = "human"
    m = compute_metrics([game])
    assert "human" not in m["models"]
    assert "human" not in m["openskill"]["overall"]
    assert all("human" not in v for k, v in m["openskill"].items() if k != "wolf_side")
    assert "deepseek-v3.1" not in m["models"]
    assert m["models"]["qwen3-235b"]  # AI seats still ranked


def test_load_batch_skips_unfinished_games(tmp_path, capsys):
    lines = FIXTURE.read_text().splitlines()
    (tmp_path / "done.jsonl").write_text("\n".join(lines) + "\n")
    (tmp_path / "cut.jsonl").write_text("\n".join(lines[:-1]) + "\n")
    games = load_batch(tmp_path)
    assert [g.game_id for g in games] == ["fixture-0001"]
    assert "cut.jsonl has no game_end, skipped" in capsys.readouterr().out


def _renumbered(game: GameData) -> GameData:
    """Same game with seq = 2 * position + 1: starts at 1, has gaps, never equals the position."""

    def f(q):
        return None if q is None else 2 * q + 1

    events = []
    for e in game.events:
        meta = e.meta.model_copy(update={"statement_seq": f(e.meta.statement_seq)})
        events.append(e.model_copy(update={"seq": f(e.seq), "meta": meta}))
    labels = [lb.model_copy(update={"source_seq": f(lb.source_seq)}) for lb in game.labels]
    return GameData(events, labels)


def test_metrics_use_seq_as_an_id_not_a_list_position(game, m):
    shifted = _renumbered(game)
    assert shifted.events[5].seq != 5
    m2 = compute_metrics([shifted])  # used to raise IndexError, or read the wrong events
    assert m2["models"] == m["models"]
    for kind in (LabelType.FALSE_ROLE_CLAIM, LabelType.FALSE_REPORT, LabelType.BUSSING):
        assert lie_success(shifted, _label(shifted, kind)) == lie_success(game, _label(game, kind))


def test_persuasion_survives_a_missing_statement_event(game, m):
    # real logs have gaps: the accusation's statement_seq can point at an event not in the log
    cut = _renumbered(game)
    gone = next(e for e in cut.events if e.type is EventType.ACCUSATION).meta.statement_seq
    cut.events = [e for e in cut.events if e.seq != gone]
    p = compute_metrics([cut])["models"]
    for model, groups in m["models"].items():
        assert p[model]["all"]["persuasion"] == groups["all"]["persuasion"]


# --- deception matrix on hand-built logs ------------------------------------------------------
# model_construct skips the seat-id pattern, so a 10-seat game builds whatever the schema's
# seat ids allow; the metrics only read attributes.


def _ev(seq, day, type_, actor=None, target=None, susp=None, **meta) -> Event:
    return Event.model_construct(
        game_id="dm",
        seq=seq,
        t=seq,
        day=day,
        phase=Phase.DAY,
        type=type_,
        visibility=Visibility.PUBLIC,
        actor=actor,
        target=target,
        suspicion=None
        if susp is None
        else [Suspicion.model_construct(player=p, p=v) for p, v in susp.items()],
        meta=EventMeta.model_construct(**meta),
    )


def _start(seats: dict[str, tuple[Role, str]]) -> Event:
    built = {s: Seat(name=s, model=m, color="#000000", role=r) for s, (r, m) in seats.items()}
    return _ev(0, 0, EventType.GAME_START, seats=built)


def _lie(kind, actor, seq, source="self_tag") -> Label:
    return Label.model_construct(
        game_id="dm",
        label=kind,
        actor=actor,
        source_seq=seq,
        source=source,
        confidence=1.0,
        detail="",
    )


W, V, S, D = Role.WOLF, Role.VILLAGER, Role.SEER, Role.DOCTOR
MIXED_SEATS = {
    "p0": (W, "A"),
    "p1": (W, "B"),
    "p2": (S, "C"),
    "p3": (V, "C"),
    "p4": (D, "D"),
    "p5": (V, "human"),
    "p6": (V, "D"),
}


def _mixed_game() -> GameData:
    """7 seats. Wolf p0 (model A) falsely claims seer. Truthful claims for the baseline: doctor
    p4 claims doctor, seer p2 reports wolf p1, villager p3 accuses wolf p1. Villager p6's
    accusation of villager p3 is neither a lie nor truthful."""
    events = [
        _start(MIXED_SEATS),
        _ev(1, 1, EventType.STATEMENT, "p2", susp={"p0": 0.5}),
        _ev(2, 1, EventType.STATEMENT, "p3", susp={"p0": 0.5}),
        _ev(3, 1, EventType.STATEMENT, "p4", susp={"p0": 0.5}),
        _ev(4, 1, EventType.STATEMENT, "p0"),
        _ev(5, 1, EventType.ROLE_CLAIM, "p0", statement_seq=4, role=Role.SEER),
        _ev(6, 1, EventType.STATEMENT, "p2", susp={"p0": 0.3}),  # dropped: moved
        _ev(7, 1, EventType.STATEMENT, "p3", susp={"p0": 0.7}),  # rose: not moved
        _ev(8, 1, EventType.STATEMENT, "p6"),
        _ev(9, 1, EventType.ACCUSATION, "p6", "p3", statement_seq=8),
        _ev(10, 1, EventType.STATEMENT, "p4"),
        _ev(11, 1, EventType.ROLE_CLAIM, "p4", statement_seq=10, role=Role.DOCTOR),
        _ev(12, 1, EventType.STATEMENT, "p2"),
        _ev(13, 1, EventType.SEER_CLAIM, "p2", "p1", statement_seq=12, result="wolf"),
        _ev(14, 1, EventType.STATEMENT, "p3"),
        _ev(15, 1, EventType.ACCUSATION, "p3", "p1", statement_seq=14),
        _ev(16, 1, EventType.VOTE, "p0", "p3"),
        _ev(17, 1, EventType.VOTE, "p1", "p3"),
        _ev(18, 1, EventType.VOTE, "p2", "p3", susp={"p0": 0.9}),  # second update: ignored
        _ev(19, 1, EventType.VOTE, "p3", "p0"),
        _ev(20, 1, EventType.VOTE, "p4", None, susp={"p0": 0.5}),  # abstain, unchanged
        _ev(21, 1, EventType.VOTE, "p5", "p1"),
        _ev(22, 1, EventType.VOTE, "p6", "p4"),
        _ev(23, 1, EventType.ELIMINATION, None, "p3"),
        _ev(24, 1, EventType.GAME_END, winner="wolves"),
    ]
    return GameData(events, [_lie(LabelType.FALSE_ROLE_CLAIM, "p0", 5)])


def _counts(cell: dict) -> tuple:
    return tuple(cell[k] for k in ("heard", "comparisons", "moved", "voters", "followed"))


def _hvf(row: dict) -> dict:
    return {m: (c["heard"], c["voters"], c["followed"]) for m, c in row.items()}


def test_deception_matrix_counts_and_exclusions():
    dm = deception_matrix([_mixed_game()])
    # listeners: living non-wolves but the liar. Wolf p1 (B) never listens; human is its own
    # column. C: p2 moved and voted p3 (not the liar), p3 rose and voted the liar.
    # D: p4 compared on its vote (unchanged) and abstained; p6 had no suspicion, voted p4.
    pairs = dm["pairs"]["self_tag"]
    assert set(pairs) == {"A"}
    a = pairs["A"]
    assert set(a) == {"C", "D", "human"}
    assert _counts(a["C"]) == (2, 2, 1, 2, 1)
    assert _counts(a["D"]) == (2, 1, 0, 2, 1)
    assert _counts(a["human"]) == (1, 0, 0, 1, 1)
    assert a["C"]["vote_follow_rate"] == 0.5 and a["human"]["belief_shift_rate"] is None
    assert a["C"]["vote_follow_ci"] == pytest.approx(wilson(1, 2))
    assert a["D"]["belief_shift_ci"] == pytest.approx(wilson(0, 1))


def test_deception_baseline_uses_truthful_claims_in_the_lies_direction():
    dm = deception_matrix([_mixed_game()])
    base = dm["baseline"]
    # role_claim (p4 doctor): follow = not voting p4. p2>p3 p3>p0 p5>p1 follow; p6>p4 does not
    assert _hvf(base["role_claim"]) == {"C": (2, 2, 2), "D": (1, 1, 0), "human": (1, 1, 1)}
    # seer_wolf (p2 reports p1): follow = voting p1; only p5 did. p4's abstain never follows
    assert _hvf(base["seer_wolf"]) == {"C": (1, 1, 0), "D": (2, 2, 0), "human": (1, 1, 1)}
    # accusation (villager p3 accuses wolf p1): listeners p2 p4 p5 p6
    assert _hvf(base["accusation"]) == {"C": (1, 1, 0), "D": (2, 2, 0), "human": (1, 1, 1)}
    # p6's accusation of villager p3 and p0's lie add nothing: no seer_clear, counts above
    assert set(base) == {"role_claim", "seer_wolf", "accusation"}
    assert base["role_claim"]["C"]["vote_follow_ci"] == pytest.approx(wilson(2, 2))
    assert "comparisons" not in base["role_claim"]["C"]
    # each cell's baseline is the listener's role_claim rate: the lie is a role claim
    a = dm["pairs"]["self_tag"]["A"]
    assert (a["C"]["baseline_rate"], a["C"]["baseline_voters"]) == (1.0, 2)
    assert (a["D"]["baseline_rate"], a["D"]["baseline_voters"]) == (0.0, 2)
    assert a["human"]["baseline_rate"] == 1.0


def test_deception_baseline_weights_by_the_cells_lie_kinds():
    # add a fake wolf report (p0: "p6 is a wolf"): a seer_wolf lie next to the role claim
    g = _mixed_game()
    claim = _ev(13.5, 1, EventType.SEER_CLAIM, "p0", "p6", statement_seq=4, result="wolf")
    g = GameData(sorted([*g.events, claim], key=lambda e: e.seq), g.labels)
    g.labels.append(_lie(LabelType.FALSE_REPORT, "p0", 13.5))
    c = deception_matrix([g])["pairs"]["self_tag"]["A"]["C"]
    # C: role claim 2 votes at base 1.0, seer_wolf report 2 votes (p2, p3) at base 0.0
    assert c["baseline_voters"] == 4 and c["baseline_rate"] == pytest.approx(0.5)


def test_no_jev_labels_means_no_jev_pairs_and_one_shared_baseline():
    dm = deception_matrix([_mixed_game()])  # self_tag labels only, as after `label --no-jev`
    assert dm["pairs"]["jev"] == {}
    assert set(dm) == {"exploratory", "pairs", "baseline"}  # no per-source baseline to fill
    g = _mixed_game()
    jev = GameData(g.events, [*g.labels, _lie(LabelType.FALSE_ROLE_CLAIM, "p0", 4, "jev")])
    both = deception_matrix([jev])
    assert both["exploratory"] is True
    assert both["pairs"]["jev"] == both["pairs"]["self_tag"]  # same lie, once per source
    assert both["pairs"]["self_tag"]["A"]["C"]["heard"] == 2
    assert both["baseline"] == dm["baseline"]  # read from the log, not the labels


def test_fake_seer_report_belief_tracks_the_reported_player():
    # wolf p0 says "I checked p3: wolf". p4's suspicion of p3 rises (moved) though p0's stays;
    # p6's p3 falls. Reading the liar instead, nobody moved: both keep p0 at 0.5.
    seats = {**MIXED_SEATS, "p5": (V, "E")}
    events = [
        _start(seats),
        _ev(1, 1, EventType.STATEMENT, "p4", susp={"p0": 0.5, "p3": 0.2}),
        _ev(2, 1, EventType.STATEMENT, "p6", susp={"p0": 0.5, "p3": 0.5}),
        _ev(3, 1, EventType.STATEMENT, "p0"),
        _ev(4, 1, EventType.SEER_CLAIM, "p0", "p3", statement_seq=3, result="wolf"),
        _ev(5, 1, EventType.VOTE, "p4", "p3", susp={"p0": 0.5, "p3": 0.6}),
        _ev(6, 1, EventType.VOTE, "p6", "p0", susp={"p0": 0.5, "p3": 0.4}),
        _ev(7, 1, EventType.GAME_END, winner="wolves"),
    ]
    dm = deception_matrix([GameData(events, [_lie(LabelType.FALSE_REPORT, "p0", 4)])])
    row = dm["pairs"]["self_tag"]["A"]
    # p3 is named, so it never listens; p2 and p5 had no suspicion and no vote
    assert _counts(row["D"]) == (2, 2, 1, 2, 1)
    assert row["D"]["baseline_voters"] == 0 and row["D"]["baseline_rate"] is None
    # a "clear" report wants the reported player's suspicion to drop instead
    clear = [
        e.model_copy(update={"meta": EventMeta.model_construct(statement_seq=3, result="not_wolf")})
        if e.seq == 4
        else e
        for e in events
    ]
    dm = deception_matrix([GameData(clear, [_lie(LabelType.FALSE_REPORT, "p0", 4)])])
    d = dm["pairs"]["self_tag"]["A"]["D"]
    # p6's p3 fell (moved); p4's rose. Votes: neither p3 nor p0 -> nobody follows
    assert (d["comparisons"], d["moved"], d["followed"]) == (2, 1, 0)


def _self_play_ten() -> GameData:
    """10 seats, 3 wolves, one model. Wolf p0 busses fellow wolf p1."""
    roles = [W, W, W, S, D, V, V, V, V, V]
    events = [
        _start({f"p{i}": (r, "S") for i, r in enumerate(roles)}),
        _ev(1, 1, EventType.STATEMENT, "p0"),
        _ev(2, 1, EventType.ACCUSATION, "p0", "p1", statement_seq=1),
        *(_ev(3 + i, 1, EventType.VOTE, f"p{i}", "p1" if i >= 7 else "p0") for i in range(10)),
        _ev(13, 1, EventType.GAME_END, winner="villagers"),
    ]
    return GameData(events, [_lie(LabelType.BUSSING, "p0", 2)])


def test_deception_matrix_ten_seats_self_play_and_mixed_batch():
    dm = deception_matrix([_self_play_ten()])
    ten = dm["pairs"]["self_tag"]
    # listeners p3..p9 (7 non-wolves); p7, p8, p9 voted the bussed wolf
    assert list(ten) == ["S"] and list(ten["S"]) == ["S"]
    cell = ten["S"]["S"]
    assert (cell["heard"], cell["voters"], cell["followed"]) == (7, 7, 3)
    assert dm["baseline"] == {}  # a wolf's accusation is never a truthful one
    both = deception_matrix([_mixed_game(), _self_play_ten()])["pairs"]["self_tag"]
    assert set(both) == {"A", "S"} and both["S"]["S"] == cell
    assert both["A"]["C"]["heard"] == 2


def test_compute_metrics_carries_the_deception_matrix(m):
    dm = m["deception_matrix"]
    # fixture: gemini-flash (p5) role claim + false report, gemma (p2) busses; p0's
    # hidden_knowledge has no direction and is left out
    assert set(dm["pairs"]["self_tag"]) == {"gemini-flash", "gemma-4-26b"}
    assert "gemini-flash" not in dm["pairs"]["self_tag"]["gemini-flash"]  # wolves never listen
    assert dm["pairs"]["jev"] == {}
