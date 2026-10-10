"""Shared scripted two-day game builder for the observation and leakage tests."""

import copy
from dataclasses import dataclass

from societysim.config import GameConfig, RuleFlags
from societysim.engine.engine import resolve_night, seer_check, start_phase, tally
from societysim.engine.state import GameState
from societysim.logging.events import Event, EventType, Suspicion, Visibility
from societysim.roles import Phase, Role

SECRET = "SECRET"  # every private canary contains this
WOLFCHAT = "WOLFCHAT"
CANARY_P = 0.4242


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


def build(reveal: bool = True, seed: int = 3, n_players: int = 7) -> Game:
    config = GameConfig(
        seed=seed, n_players=n_players, rules=RuleFlags(reveal_role_on_death=reveal)
    )
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
GAMES10 = {reveal: build(reveal, n_players=10) for reveal in (True, False)}  # p0..p9
