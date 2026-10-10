"""Leakage tests for agents/observation.py (hard rule 2).

A scripted two-day game logs every event type, with canaries in every private field. For
every role x every checkpoint (night, day, vote; with and without role reveal):
- no private_reasoning, suspicion value or narrator text reaches any observation;
- removing any event type the role may not see leaves the observation byte-identical;
- changing hidden state (other players' roles, other Seer results, Doctor marker) leaves it
  byte-identical too, so nothing leaks from GameState either.
"""

import copy
from dataclasses import dataclass

import pytest

from societysim.agents.observation import observe
from societysim.config import GameConfig, RuleFlags
from societysim.engine.engine import resolve_night, seer_check, start_phase, tally
from societysim.engine.state import GameState
from societysim.logging.events import Event, EventType, Suspicion, Visibility
from societysim.roles import Phase, Role

SECRET = "SECRET"  # every private canary contains this
WOLFCHAT = "WOLFCHAT"
CANARY_P = 0.4242

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


class MemoryLog:
    def __init__(self) -> None:
        self.events: list[Event] = []

    def log(self, **fields) -> Event:
        event = Event(game_id="leak", seq=len(self.events), **fields)
        self.events.append(event)
        return event


@dataclass
class Game:
    checkpoints: dict[str, tuple[GameState, list[Event]]]
    final: GameState
    cast: dict[str, str]  # wolf, wolf2, seer, doctor, v_night1, v_day1, villager


def secret(state: GameState, actor: str) -> dict:
    """Canary private fields for an agent-backed event."""
    others = [s for s in state.seats if s in state.alive and s != actor]
    return {
        "private_reasoning": f"{SECRET}-reason-{actor}-{state.t}",
        "suspicion": [Suspicion(player=s, p=CANARY_P) for s in others],
    }


def emit(state: GameState, log: MemoryLog, type_: EventType, vis: Visibility, **kw) -> Event:
    return log.log(t=state.t, day=state.day, phase=state.phase, type=type_, visibility=vis, **kw)


def statement_text(seat: str, day: int, rnd: int) -> str:
    """40 words: START and TAIL markers sit outside the 25-word summary cut."""
    return f"START-{seat}-{day}-{rnd} " + "blah " * 38 + f"TAIL-{seat}-{day}-{rnd}"


def wolf_night(state, log, wolves, victim):
    for rnd in (1, 2):
        for w in wolves:
            emit(
                state,
                log,
                EventType.WOLF_CHAT,
                Visibility.WOLVES,
                actor=w,
                public_text=f"{WOLFCHAT}-{state.day}-{rnd}-{w}",
                meta={"round": rnd},
                **secret(state, w),
            )
            emit(
                state,
                log,
                EventType.WOLF_PROPOSAL,
                Visibility.WOLVES,
                actor=w,
                target=victim,
                meta={"round": rnd},
            )


def failures(state, log, actor):
    emit(
        state,
        log,
        EventType.INVALID_OUTPUT,
        Visibility.NARRATOR,
        actor=actor,
        meta={"action": "vote", "attempt": 1, "error": f"{SECRET}-error", "raw": f"{SECRET}-raw"},
    )
    emit(
        state,
        log,
        EventType.FALLBACK_ACTION,
        Visibility.NARRATOR,
        actor=actor,
        meta={"action": "vote", "reason": f"{SECRET}-fallback"},
    )


def discussion(state, log, cast, day, snap):
    for rnd in (1, 2):
        for s in sorted(state.alive):
            st = emit(
                state,
                log,
                EventType.STATEMENT,
                Visibility.PUBLIC,
                actor=s,
                public_text=statement_text(s, day, rnd),
                meta={"round": rnd},
                **secret(state, s),
            )
            ref = {"statement_seq": st.seq}
            if s == cast["seer"]:
                emit(
                    state,
                    log,
                    EventType.ACCUSATION,
                    Visibility.PUBLIC,
                    actor=s,
                    target=cast["wolf"],
                    meta=ref,
                )
            if s == cast["wolf2"]:  # a wolf fake-claims Seer and reports the real Seer
                emit(
                    state,
                    log,
                    EventType.ROLE_CLAIM,
                    Visibility.PUBLIC,
                    actor=s,
                    meta={**ref, "role": "seer"},
                )
                emit(
                    state,
                    log,
                    EventType.SEER_CLAIM,
                    Visibility.PUBLIC,
                    actor=s,
                    target=cast["seer"],
                    meta={**ref, "result": "wolf", "night": 1},
                )
        if rnd == 1:
            snap(f"day{day}")


def build(reveal: bool = True, seed: int = 3) -> Game:
    config = GameConfig(seed=seed, rules=RuleFlags(reveal_role_on_death=reveal))
    state = GameState(config)
    log = MemoryLog()
    by_role = {r: [s for s in state.seats if state.roles[s] is r] for r in Role}
    wolves = by_role[Role.WOLF]
    villagers = by_role[Role.VILLAGER]
    cast = {
        "wolf": wolves[0],
        "wolf2": wolves[1],
        "seer": by_role[Role.SEER][0],
        "doctor": by_role[Role.DOCTOR][0],
        "v_night1": villagers[0],
        "v_day1": villagers[1],
        "villager": villagers[2],
    }
    checkpoints = {}

    def snap(name):
        checkpoints[name] = (copy.deepcopy(state), list(log.events))

    log.log(
        t=0,
        day=0,
        phase=Phase.SETUP,
        type=EventType.GAME_START,
        visibility=Visibility.NARRATOR,
        public_text=f"{SECRET}-narrator",
        meta={
            "seed": seed,
            "seats": {
                s: {"name": state.names[s], "model": "m", "color": "#000", "role": state.roles[s]}
                for s in state.seats
            },
        },
    )

    # Night 1: the Doctor misses, a villager dies.
    start_phase(state, log, Phase.NIGHT)
    wolf_night(state, log, wolves, cast["v_night1"])
    seer_check(state, log, cast["seer"], cast["wolf"], **secret(state, cast["seer"]))
    snap("night1")
    failures(state, log, cast["villager"])
    resolve_night(
        state,
        log,
        cast["v_night1"],
        save=cast["doctor"],
        doctor=cast["doctor"],
        **secret(state, cast["doctor"]),
    )

    # Day 1: a villager is voted out.
    start_phase(state, log, Phase.DAY)
    discussion(state, log, cast, 1, snap)
    start_phase(state, log, Phase.VOTE)
    snap("vote1")
    votes = {s: cast["v_day1"] for s in sorted(state.alive) if s != cast["v_day1"]}
    votes[cast["v_day1"]] = "abstain"
    for s, choice in votes.items():
        target = None if choice == "abstain" else choice
        emit(
            state,
            log,
            EventType.VOTE,
            Visibility.PUBLIC,
            actor=s,
            target=target,
            **secret(state, s),
        )
    tally(state, log, votes)

    # Night 2: the Doctor saves the wolves' target.
    start_phase(state, log, Phase.NIGHT)
    wolf_night(state, log, wolves, cast["villager"])
    seer_check(state, log, cast["seer"], cast["villager"], **secret(state, cast["seer"]))
    snap("night2")
    failures(state, log, cast["seer"])
    resolve_night(
        state,
        log,
        cast["villager"],
        save=cast["villager"],
        doctor=cast["doctor"],
        **secret(state, cast["doctor"]),
    )

    # Day 2.
    start_phase(state, log, Phase.DAY)
    discussion(state, log, cast, 2, snap)
    start_phase(state, log, Phase.VOTE)
    snap("vote2")
    return Game(checkpoints, state, cast)


GAMES = {reveal: build(reveal) for reveal in (True, False)}
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
    other.seer_results = {s: [(1, seat, "wolf")] for s in state.seats if s != seat}
    other.seer_results[seat] = state.seer_results.get(seat, [])
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
