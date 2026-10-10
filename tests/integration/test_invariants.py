"""Invariant sweep: many seeded RandomAgent games, every invariant checked on every game.

No API calls. Each game runs through a probe agent that wraps RandomAgent, records every
prompt it is shown, and stamps unique markers into private thoughts and wolf chat so the
leak checks can search prompts for them. Kept self-contained on purpose; it can move to the
shared test helpers later.
"""

import itertools
import json
import re
from collections import Counter
from functools import partial
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from societysim.agents.base import Turn
from societysim.agents.scripted import RandomAgent
from societysim.config import GameConfig, RoleCounts, RuleFlags
from societysim.engine.state import GameState
from societysim.logging.events import EventType
from societysim.logging.sinks.jsonl import JsonlSink
from societysim.models.client import CompletionResult
from societysim.orchestrator import Game, run_game
from societysim.roles import Phase, Role

ROOT = Path(__file__).parents[2]
VALIDATOR = Draft202012Validator(json.loads((ROOT / "schema/events.schema.json").read_text()))

# Every combination RULES.md allows through config: 7-9 players, 2 or 3 wolves,
# abstain on/off, clean agents and agents that fail 30% of replies, and a 2-day cap so
# some games end in a draw (random players almost never reach day 6).
CONFIGS = [
    dict(n_players=n, wolves=w, allow_abstain=a, invalid_rate=r, max_days=d)
    for n, w, a, r, d in itertools.product((7, 8, 9), (2, 3), (True, False), (0.0, 0.3), (6, 2))
]
SEEDS = range(5)  # 48 configs x 5 seeds = 240 games
# (config index, seed) -> reason, for invariant failures awaiting a bug fix.
KNOWN_FAILURES: dict[tuple[int, int], str] = {}

# The event an action produces when its reply (or its fallback) is used.
ACTION_EVENT = {
    "statement": EventType.STATEMENT,
    "vote": EventType.VOTE,
    "wolf_chat": EventType.WOLF_CHAT,
    "lone_wolf": EventType.WOLF_PROPOSAL,
    "seer": EventType.SEER_CHECK,
    "doctor": EventType.DOCTOR_SAVE,
}
DEATHS = (EventType.DEATH_REVEAL, EventType.ELIMINATION)


def game_config(spec: dict, seed: int) -> GameConfig:
    return GameConfig(
        n_players=spec["n_players"],
        seed=seed,
        roles=RoleCounts(wolves=spec["wolves"]),
        rules=RuleFlags(allow_abstain=spec["allow_abstain"], max_days=spec["max_days"]),
    )


class Probe:
    """RandomAgent that records (seat, role, prompt) and marks its private text."""

    def __init__(self, state: GameState, seat: str, invalid_rate: float, prompts: list):
        self.inner = RandomAgent(state, seat, invalid_rate=invalid_rate)
        self.state, self.seat, self.prompts, self.n = state, seat, prompts, 0

    async def act(self, turn: Turn) -> CompletionResult:
        assert self.seat in self.state.alive, f"dead {self.seat} was asked to act"
        self.prompts.append((self.seat, self.state.roles[self.seat], turn.prompt))
        result = await self.inner.act(turn)
        if result.parsed is None:
            return result
        self.n += 1
        tag = f"{self.seat}-{self.n}"
        pr = result.parsed.private_reasoning.model_copy(update={"thoughts": f"thought-{tag}"})
        update = {"private_reasoning": pr}
        if turn.action == "wolf_chat":
            update["action"] = result.parsed.action.model_copy(update={"message": f"howl-{tag}"})
        return result.model_copy(update={"parsed": result.parsed.model_copy(update=update)})


def check_log(game: Game) -> None:
    st, events = game.state, game.events
    rules = st.config.rules
    seats = set(st.seats)

    # Schema, seq, seat ids.
    for e in events:
        VALIDATOR.validate(json.loads(e.model_dump_json()))
        assert {e.actor, e.target} - {None} <= seats, f"seq {e.seq}: not a seat id"
    assert [e.seq for e in events] == list(range(len(events)))

    roles = {s: seat.role for s, seat in events[0].meta.seats.items()}
    assert events[0].type is EventType.GAME_START and roles == st.roles

    alive, day = set(st.seats), 0
    protects: list[tuple[int, str]] = []
    checks: list[tuple[int, str, str]] = []
    for e in events:
        if e.type is EventType.PHASE_START and e.phase is Phase.NIGHT:
            day += 1
        assert e.day == day, f"seq {e.seq}: day {e.day}, expected {day}"
        if e.actor:
            assert e.actor in alive, f"seq {e.seq}: dead {e.actor} acts ({e.type})"
        if e.target and e.type is not EventType.SEER_CLAIM:  # a claim may name the dead
            assert e.target in alive, f"seq {e.seq}: dead {e.target} targeted ({e.type})"
        match e.type:
            case EventType.VOTE | EventType.ACCUSATION:
                assert e.target != e.actor, f"seq {e.seq}: self-{e.type}"
                if e.type is EventType.VOTE and not rules.allow_abstain:
                    assert e.target or e.meta.fallback, f"seq {e.seq}: abstain is off"
            case EventType.WOLF_PROPOSAL | EventType.NIGHT_KILL:
                assert roles[e.target] is not Role.WOLF, f"seq {e.seq}: wolf targets a wolf"
            case EventType.SEER_CHECK:
                assert roles[e.actor] is Role.SEER and e.target != e.actor
                assert e.meta.result == ("wolf" if roles[e.target] is Role.WOLF else "not_wolf")
                checks.append((e.day, e.target, e.meta.result))
            case EventType.DOCTOR_SAVE:
                assert roles[e.actor] is Role.DOCTOR
                if rules.doctor_no_repeat and protects and protects[-1][0] == e.day - 1:
                    assert e.target != protects[-1][1], f"seq {e.seq}: doctor repeat"
                protects.append((e.day, e.target))
            case EventType.DEATH_REVEAL | EventType.ELIMINATION if e.target:
                alive.discard(e.target)
    assert alive == st.alive

    # One death event per death.
    died = [e.target for e in events if e.type in DEATHS and e.target]
    assert len(died) == len(set(died)) and set(died) == seats - st.alive

    # Each night: one kill; a matching save gives one empty death_reveal, else the kill dies.
    nights = [e for e in events if e.phase is Phase.NIGHT]
    for night in range(1, day + 1):
        evs = [e for e in nights if e.day == night]
        (kill,) = [e.target for e in evs if e.type is EventType.NIGHT_KILL]
        saves = [e for e in evs if e.type is EventType.DOCTOR_SAVE]
        assert len(saves) <= 1
        for save in saves:
            assert save.meta.saved == (save.target == kill)
        reveals = [e.target for e in evs if e.type is EventType.DEATH_REVEAL]
        saved = any(s.meta.saved for s in saves)
        assert reveals == ([None] if saved else [kill]), f"night {night}: reveals {reveals}"

    # Phases: night, day, vote in turn; day only moves at night start.
    starts = [(e.day, e.phase) for e in events if e.type is EventType.PHASE_START]
    cycle = [Phase.NIGHT, Phase.DAY, Phase.VOTE]
    assert starts == [(i // 3 + 1, cycle[i % 3]) for i in range(len(starts))]

    # Seer results match the log, night for night.
    assert [c for results in st.seer_results.values() for c in results] == checks

    # Every invalid_output leads to a retry or a fallback, in order.
    for i, e in enumerate(events):
        if e.type not in (EventType.INVALID_OUTPUT, EventType.FALLBACK_ACTION):
            continue
        action = e.meta.action
        nxt = next(n for n in events[i + 1 :] if n.actor == e.actor)
        if e.type is EventType.FALLBACK_ACTION:
            assert nxt.type is ACTION_EVENT[action] and nxt.meta.fallback, f"seq {e.seq}"
        elif e.meta.attempt == 1:
            retry = nxt.type is EventType.INVALID_OUTPUT and nxt.meta.attempt == 2
            ok = nxt.type is ACTION_EVENT[action] and not nxt.meta.fallback
            assert (retry and nxt.meta.action == action) or ok, f"seq {e.seq}: no retry"
        else:
            assert nxt.type is EventType.FALLBACK_ACTION and nxt.meta.action == action
    # One fallback_action per fallback-marked action; a wolf_chat fallback also marks the
    # wolf_proposal logged right after it, so that pair counts once.
    fallbacks = sum(e.type is EventType.FALLBACK_ACTION for e in events)
    marked = [
        e
        for prev, e in itertools.pairwise(events)
        if e.meta.fallback
        and not (e.type is EventType.WOLF_PROPOSAL and prev.type is EventType.WOLF_CHAT)
    ]
    assert fallbacks == len(marked)

    # Termination with a winner that matches who is alive.
    end = events[-1]
    assert Counter(e.type for e in events)[EventType.GAME_END] == 1
    assert end.type is EventType.GAME_END and end.meta.winner == game.winner
    assert end.meta.roles == roles
    wolves = sum(roles[s] is Role.WOLF for s in st.alive)
    if wolves == 0:
        expected = "villagers"
    elif wolves >= len(st.alive) - wolves:
        expected = "wolves"
    else:
        expected = "draw"
        assert end.day == rules.max_days and end.phase is Phase.VOTE
    assert game.winner == expected


def check_leaks(game: Game, prompts: list) -> None:
    chat = [e.public_text for e in game.events if e.type is EventType.WOLF_CHAT]
    assert all(t.startswith("howl-") or t == "(silent)" for t in chat)
    for seat, role, prompt in prompts:
        thoughts = set(re.findall(r"thought-(p\d)-\d+", prompt))
        assert thoughts <= {seat}, f"{seat} sees private reasoning of {thoughts - {seat}}"
        if role is not Role.WOLF:
            assert "howl-" not in prompt and "(wolf channel)" not in prompt, f"{seat} sees wolves"


def case(i: int, spec: dict, seed: int):
    reason = KNOWN_FAILURES.get((i, seed))
    marks = [pytest.mark.xfail(reason=reason, strict=True)] if reason else []
    name = "-".join(f"{k}={v}" for k, v in spec.items())
    return pytest.param(spec, seed, id=f"{i}-{name}-seed={seed}", marks=marks)


@pytest.mark.parametrize(
    ("spec", "seed"),
    [case(i, spec, seed) for (i, spec), seed in itertools.product(enumerate(CONFIGS), SEEDS)],
)
async def test_invariants_hold(spec, seed):
    prompts: list = []
    factory = partial(Probe, invalid_rate=spec["invalid_rate"], prompts=prompts)
    game = await run_game(game_config(spec, seed), factory, [], "g")
    check_log(game)
    check_leaks(game, prompts)


@pytest.mark.parametrize("i", range(0, len(CONFIGS), 5))
async def test_same_seed_gives_byte_identical_log(tmp_path, i):
    spec = CONFIGS[i]

    async def log(out: Path) -> bytes:
        agent = partial(RandomAgent, invalid_rate=spec["invalid_rate"])
        await run_game(game_config(spec, 11), agent, [JsonlSink("b", runs_dir=out)], "g")
        return (out / "b" / "g.jsonl").read_bytes()

    first, second = await log(tmp_path / "a"), await log(tmp_path / "b")
    assert first and first == second
