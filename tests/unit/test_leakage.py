"""Leakage tests for agents/observation.py (hard rule 2).

A scripted two-day game logs every event type, with canaries in every private field. For
every role x every checkpoint (night, day, vote; with and without role reveal):
- no private_reasoning, suspicion value or narrator text reaches any observation;
- removing any event type the role may not see leaves the observation byte-identical;
- changing hidden state (other players' roles, other Seer results, Doctor marker) leaves it
  byte-identical too, so nothing leaks from GameState either.
"""

import copy

import pytest
from helpers import CANARY_P, GAMES, SECRET, WOLFCHAT

from societysim.agents.observation import observe, visible
from societysim.logging.events import Event, EventType, Visibility
from societysim.roles import Phase, Role

PRIVATE_TYPES = [
    EventType.GAME_START,
    EventType.WOLF_CHAT,
    EventType.WOLF_PROPOSAL,
    EventType.NIGHT_KILL,
    EventType.SEER_CHECK,
    EventType.DOCTOR_SAVE,
    EventType.INVALID_OUTPUT,
    EventType.FALLBACK_ACTION,
    EventType.GAME_END,
]
ALLOWED = {
    Role.WOLF: {EventType.WOLF_CHAT, EventType.WOLF_PROPOSAL, EventType.NIGHT_KILL},
    Role.SEER: {EventType.SEER_CHECK},
    Role.DOCTOR: {EventType.DOCTOR_SAVE},
    Role.VILLAGER: set(),
}
CHECKPOINTS = ["night1", "day1", "vote1", "night2", "day2", "vote2"]


VIEWERS = {Role.WOLF: "wolf", Role.SEER: "seer", Role.DOCTOR: "doctor", Role.VILLAGER: "villager"}

matrix = pytest.mark.parametrize(
    ("reveal", "checkpoint", "role"),
    [(r, c, role) for r in (True, False) for c in CHECKPOINTS for role in Role],
)


def setup(reveal, checkpoint, role):
    game = GAMES[reveal]
    state, events = game.checkpoints[checkpoint]
    end = Event(
        game_id="leak",
        seq=len(events),
        t=state.t,
        day=state.day,
        phase=state.phase,
        type=EventType.GAME_END,
        visibility=Visibility.PUBLIC,
        meta={"winner": "draw", "reason": "max_days", "roles": dict(state.roles)},
    )
    return state, [*events, end], game.cast[VIEWERS[role]]


@matrix
def test_no_private_field_or_narrator_text(reveal, checkpoint, role):
    state, events, seat = setup(reveal, checkpoint, role)
    obs = observe(state, events, seat)
    assert SECRET not in obs
    assert str(CANARY_P) not in obs
    if role is Role.WOLF:
        if checkpoint.startswith("night"):  # tonight's chat is shown in full to wolves
            assert f"{WOLFCHAT}-{state.day}-1-" in obs
    else:
        assert WOLFCHAT not in obs


@pytest.mark.parametrize(
    ("reveal", "checkpoint", "role", "hidden"),
    [
        (r, c, role, t)
        for r in (True, False)
        for c in CHECKPOINTS
        for role in Role
        for t in PRIVATE_TYPES
        if t not in ALLOWED[role]
    ],
)
def test_removing_hidden_events_changes_nothing(reveal, checkpoint, role, hidden):
    state, events, seat = setup(reveal, checkpoint, role)
    stripped = [e for e in events if e.type is not hidden]
    assert observe(state, events, seat) == observe(state, stripped, seat)


@pytest.mark.parametrize("hidden", [EventType.SEER_CHECK, EventType.DOCTOR_SAVE])
def test_seer_and_doctor_see_only_their_own_events(hidden):
    """A Seer or Doctor event by another seat (say, a second Seer) stays hidden."""
    role = Role.SEER if hidden is EventType.SEER_CHECK else Role.DOCTOR
    state, events, seat = setup(True, "vote2", role)
    other = next(s for s in sorted(state.alive) if s != seat)
    forged = [
        e.model_copy(update={"actor": other}) if e.type is hidden and e.actor == seat else e
        for e in events
    ]
    stripped = [e for e in events if e.type is not hidden]
    assert observe(state, forged, seat) == observe(state, stripped, seat)


@matrix
def test_hidden_state_changes_nothing(reveal, checkpoint, role):
    state, events, seat = setup(reveal, checkpoint, role)
    other = copy.deepcopy(state)
    # Shuffle every other player's role (wolves keep their pack: they know it).
    keep = {seat} | (
        {s for s in state.seats if state.roles[s] is Role.WOLF} if role is Role.WOLF else set()
    )
    movable = [s for s in state.seats if s not in keep]
    rotated = movable[1:] + movable[:1]
    for a, b in zip(movable, rotated, strict=True):
        other.roles[a] = state.roles[b]
    assert other.roles != state.roles
    fake = next(s for s in sorted(state.alive) if s != seat)
    other.seer_results = {s: [(1, fake, "wolf")] for s in state.seats}
    if role is Role.SEER:
        other.seer_results[seat] = state.seer_results[seat]
    other.last_doctor_protect, other.last_doctor_protect_night = seat, 1
    assert observe(state, events, seat) == observe(other, events, seat)


@pytest.mark.parametrize("checkpoint", CHECKPOINTS)
def test_dead_players_get_no_observation(checkpoint):
    state, events = GAMES[True].checkpoints[checkpoint]
    dead = [s for s in state.seats if s not in state.alive]
    for seat in dead:
        with pytest.raises(ValueError, match="dead"):
            observe(state, events, seat)
    assert dead or checkpoint == "night1"


def test_unrevealed_roles_stay_hidden():
    game = GAMES[False]
    state, events = game.checkpoints["vote2"]
    obs = observe(state, events, game.cast["villager"])
    assert obs.count("role not revealed") >= 2
    assert "villager" not in obs  # both dead were villagers; nobody claimed villager


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize("vis", list(Visibility))
@pytest.mark.parametrize("own", [True, False])
def test_visible_policy(role, vis, own):
    """The filter itself, independent of what rendering happens to show."""
    event = Event(
        game_id="g",
        seq=0,
        t=0,
        day=1,
        phase=Phase.NIGHT,
        type=EventType.STATEMENT,
        visibility=vis,
        actor="p1" if own else "p2",
    )
    expected = {
        Visibility.PUBLIC: True,
        Visibility.WOLVES: role is Role.WOLF,
        Visibility.SEER: role is Role.SEER and own,
        Visibility.DOCTOR: role is Role.DOCTOR and own,
        Visibility.NARRATOR: False,
    }[vis]
    assert visible(event, "p1", role) is expected


@pytest.mark.parametrize("role", list(Role))
def test_game_end_is_never_visible(role):
    event = Event(
        game_id="g",
        seq=0,
        t=0,
        day=1,
        phase=Phase.VOTE,
        type=EventType.GAME_END,
        visibility=Visibility.PUBLIC,
        meta={"roles": {"p1": "wolf"}},
    )
    assert not visible(event, "p1", role)
