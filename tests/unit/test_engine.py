"""Tests for pure game transitions and their emitted events."""

import pytest

from societysim.config import GameConfig
from societysim.engine.engine import (
    check_win,
    finish_if_over,
    resolve_night,
    seer_check,
    speaking_orders,
    start_phase,
    tally,
)
from societysim.engine.state import GameState
from societysim.logging.events import Event, EventType, Visibility
from societysim.roles import Phase, Role


class MemoryLog:
    def __init__(self):
        self.events: list[Event] = []

    def log(self, **fields) -> Event:
        event = Event(game_id="test-game", seq=len(self.events), **fields)
        self.events.append(event)
        return event


@pytest.fixture
def game():
    state = GameState(GameConfig(seed=7))
    log = MemoryLog()
    start_phase(state, log, Phase.NIGHT)
    return state, log


def seat(state, role):
    return next(s for s in state.seats if state.roles[s] is role)


def seats(state, role):
    return [s for s in state.seats if state.roles[s] is role]


def test_doctor_saves_self_and_empty_death_reveal(game):
    state, log = game
    doctor = seat(state, Role.DOCTOR)
    assert resolve_night(state, log, doctor, save=doctor, doctor=doctor) == []
    assert doctor in state.alive
    assert state.last_doctor_protect == doctor
    assert state.last_doctor_protect_night == 1
    assert [event.type for event in log.events[1:]] == [
        EventType.NIGHT_KILL,
        EventType.DOCTOR_SAVE,
        EventType.DEATH_REVEAL,
    ]
    assert log.events[-2].meta.saved is True
    assert log.events[-1].target is None
    assert log.events[-1].meta.role is None


def test_doctor_save_misses_and_reveals_death(game):
    state, log = game
    doctor = seat(state, Role.DOCTOR)
    victim = seat(state, Role.VILLAGER)
    assert resolve_night(state, log, victim, save=doctor, doctor=doctor) == [victim]
    assert log.events[-2].meta.saved is False
    assert log.events[-1].target == victim
    assert log.events[-1].meta.role is Role.VILLAGER
    assert victim not in state.alive


def test_doctor_cannot_repeat_previous_night_but_can_after_gap(game):
    state, log = game
    doctor = seat(state, Role.DOCTOR)
    protected = seat(state, Role.VILLAGER)
    resolve_night(state, log, protected, save=protected, doctor=doctor)
    start_phase(state, log, Phase.NIGHT)
    before = len(log.events)
    victim = next(s for s in seats(state, Role.VILLAGER) if s != protected)
    assert resolve_night(state, log, victim, save=protected, doctor=doctor) == [victim]
    assert all(event.type is not EventType.DOCTOR_SAVE for event in log.events[before:])
    assert state.last_doctor_protect_night == 1

    start_phase(state, log, Phase.NIGHT)
    assert resolve_night(state, log, protected, save=protected, doctor=doctor) == []
    assert log.events[-2].type is EventType.DOCTOR_SAVE
    assert log.events[-2].meta.saved is True
    assert state.last_doctor_protect_night == 3


def test_seer_checks_target_before_target_dies(game):
    state, log = game
    seer = seat(state, Role.SEER)
    victim = seat(state, Role.VILLAGER)
    assert seer_check(state, log, seer, victim) == "not_wolf"
    assert resolve_night(state, log, victim) == [victim]
    assert state.seer_results[seer] == [(1, victim, "not_wolf")]
    assert [event.type for event in log.events[1:]] == [
        EventType.SEER_CHECK,
        EventType.NIGHT_KILL,
        EventType.DEATH_REVEAL,
    ]
    assert log.events[1].meta.result == "not_wolf"
    assert seer_check(state, log, seer, seat(state, Role.WOLF)) == "wolf"


def test_dead_or_wrong_role_actors_produce_no_special_event(game):
    state, log = game
    seer = seat(state, Role.SEER)
    doctor = seat(state, Role.DOCTOR)
    villager = seat(state, Role.VILLAGER)
    state.alive.remove(seer)
    state.alive.remove(doctor)
    before = len(log.events)
    assert seer_check(state, log, seer, villager) is None
    assert seer_check(state, log, villager, seat(state, Role.WOLF)) is None
    assert len(log.events) == before
    resolve_night(state, log, villager, save=villager, doctor=doctor)
    assert all(event.type is not EventType.DOCTOR_SAVE for event in log.events[before:])
    assert state.last_doctor_protect is None

    before = len(log.events)
    another_villager = next(s for s in seats(state, Role.VILLAGER) if s in state.alive)
    resolve_night(state, log, another_villager, save=another_villager, doctor=villager)
    assert all(event.type is not EventType.DOCTOR_SAVE for event in log.events[before:])


@pytest.mark.parametrize(
    ("votes", "expected", "reason"),
    [
        ({"p0": None, "p1": "abstain"}, {}, "all_abstain"),
        ({"p0": "p1", "p2": "p3", "p4": None}, {"p1": 1, "p3": 1}, "tie"),
    ],
)
def test_tally_without_elimination_always_logs_tally(game, votes, expected, reason):
    state, log = game
    start_phase(state, log, Phase.DAY)
    start_phase(state, log, Phase.VOTE)
    assert tally(state, log, votes) is None
    event = log.events[-1]
    assert event.type is EventType.NO_ELIMINATION
    assert event.meta.tally == expected
    assert "tally" in event.meta.model_dump()
    assert event.meta.reason == reason


def test_tally_eliminates_plurality_and_reveals_role(game):
    state, log = game
    target = seat(state, Role.VILLAGER)
    voters = [s for s in state.seats if s != target][:2]
    start_phase(state, log, Phase.VOTE)
    assert tally(state, log, {voters[0]: target, voters[1]: target}) == target
    event = log.events[-1]
    assert event.type is EventType.ELIMINATION
    assert event.target == target
    assert event.meta.tally == {target: 2}
    assert event.meta.role is Role.VILLAGER
    assert target not in state.alive


def test_wolf_parity_win_immediately_after_night_kill(game):
    state, log = game
    wolves = seats(state, Role.WOLF)
    others = [s for s in state.seats if state.roles[s] is not Role.WOLF][:3]
    state.alive = set(wolves + others)
    assert check_win(state) is None
    assert resolve_night(state, log, others[0]) == [others[0]]
    assert finish_if_over(state, log) == "wolves"
    assert log.events[-1].type is EventType.GAME_END
    assert log.events[-1].meta.winner == "wolves"
    assert log.events[-1].meta.reason == "wolf_parity"
    assert log.events[-1].meta.roles == state.roles
    assert log.events[-2].type is EventType.DEATH_REVEAL


def test_win_check_after_elimination(game):
    state, log = game
    wolf = seat(state, Role.WOLF)
    others = seats(state, Role.VILLAGER)[:2]
    state.alive = set([wolf, *others])
    start_phase(state, log, Phase.VOTE)
    assert tally(state, log, {others[0]: wolf, others[1]: wolf}) == wolf
    assert finish_if_over(state, log) == "villagers"
    assert log.events[-1].meta.winner == "villagers"
    assert log.events[-1].meta.reason == "no_wolves"


def test_draw_only_after_day_six_vote(game):
    state, log = game
    state.day = 5
    start_phase(state, log, Phase.VOTE)
    assert tally(state, log, {}) is None
    assert finish_if_over(state, log) is None
    assert log.events[-1].type is EventType.NO_ELIMINATION
    start_phase(state, log, Phase.NIGHT)
    start_phase(state, log, Phase.DAY)
    start_phase(state, log, Phase.VOTE)
    assert state.day == 6
    assert tally(state, log, {}) is None
    assert finish_if_over(state, log) == "draw"
    assert log.events[-1].meta.winner == "draw"
    assert log.events[-1].meta.reason == "max_days"


def test_event_stamps_sequence_and_visibility(game):
    state, log = game
    seer = seat(state, Role.SEER)
    doctor = seat(state, Role.DOCTOR)
    victim = seat(state, Role.VILLAGER)
    seer_check(state, log, seer, victim)
    resolve_night(state, log, victim, save=doctor, doctor=doctor)
    start_phase(state, log, Phase.DAY)
    start_phase(state, log, Phase.VOTE)
    tally(state, log, {})
    assert [e.seq for e in log.events] == list(range(len(log.events)))
    assert [(e.t, e.day, e.phase) for e in log.events] == [
        (1, 1, Phase.NIGHT),
        (1, 1, Phase.NIGHT),
        (1, 1, Phase.NIGHT),
        (1, 1, Phase.NIGHT),
        (1, 1, Phase.NIGHT),
        (2, 1, Phase.DAY),
        (3, 1, Phase.VOTE),
        (3, 1, Phase.VOTE),
    ]
    assert [e.visibility for e in log.events] == [
        Visibility.PUBLIC,
        Visibility.SEER,
        Visibility.WOLVES,
        Visibility.DOCTOR,
        Visibility.PUBLIC,
        Visibility.PUBLIC,
        Visibility.PUBLIC,
        Visibility.PUBLIC,
    ]
    assert log.events[0].public_text == "Night 1"
    assert log.events[5].public_text == "Day 1"
    assert log.events[6].public_text == "Day 1 vote"


def test_same_seed_produces_identical_events_and_speaking_orders():
    def run():
        state = GameState(GameConfig(seed=19))
        log = MemoryLog()
        start_phase(state, log, Phase.NIGHT)
        seer = seat(state, Role.SEER)
        doctor = seat(state, Role.DOCTOR)
        victim = seat(state, Role.VILLAGER)
        seer_check(state, log, seer, victim)
        resolve_night(state, log, victim, save=doctor, doctor=doctor)
        start_phase(state, log, Phase.DAY)
        orders = speaking_orders(state)
        start_phase(state, log, Phase.VOTE)
        tally(state, log, {})
        return orders, [event.model_dump(mode="json") for event in log.events]

    assert run() == run()
