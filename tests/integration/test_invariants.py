"""Pairwise rule/config sweep of seeded RandomAgent games and their invariants.

No API calls. Each game runs through a probe agent that wraps RandomAgent, records every
prompt it is shown, and stamps unique markers into private thoughts and wolf chat so the
leak checks can search prompts for them. The scripted observation case uses tests/helpers.py.
"""

import itertools
import json
import os
import re
from collections import Counter
from functools import partial
from pathlib import Path

import pytest
from helpers import MemoryLog, build
from jsonschema import Draft202012Validator

from societysim.agents.base import Turn
from societysim.agents.observation import observe
from societysim.agents.prompts import player
from societysim.agents.scripted import RandomAgent
from societysim.config import GameConfig, RoleCounts, RuleFlags
from societysim.engine.engine import resolve_night, start_phase
from societysim.engine.rules import legal_targets
from societysim.engine.state import GameState
from societysim.logging.events import EventType
from societysim.logging.sinks.jsonl import JsonlSink
from societysim.models.client import CompletionResult
from societysim.orchestrator import Game, run_game
from societysim.roles import Phase, Role

ROOT = Path(__file__).parents[2]
VALIDATOR = Draft202012Validator(json.loads((ROOT / "schema/events.schema.json").read_text()))

# Each pair of settings occurs together at least once, including pairs among the new
# flags. The full Cartesian product would be 1536 configurations.
DIMENSIONS = {
    "n_players": (7, 8, 9),
    "wolves": (2, 3),
    "allow_abstain": (True, False),
    "invalid_rate": (0.0, 0.3),
    "max_days": (6, 2),
    "doctor_self_protect": (True, False),
    "doctor_no_repeat": (True, False),
    "reveal_role_on_death": (True, False),
    "wolf_chat_rounds": (2, 1),
    "discussion_rounds": (2, 1),
}


def pairwise_configs() -> list[dict]:
    keys = tuple(DIMENSIONS)
    candidates = [
        dict(zip(keys, values, strict=True)) for values in itertools.product(*DIMENSIONS.values())
    ]
    coverage = [
        {
            (i, spec[keys[i]], j, spec[keys[j]])
            for i, j in itertools.combinations(range(len(keys)), 2)
        }
        for spec in candidates
    ]
    uncovered = set().union(*coverage)
    selected = []
    while uncovered:
        index = max(range(len(candidates)), key=lambda i: len(coverage[i] & uncovered))
        selected.append(candidates[index])
        uncovered -= coverage[index]
    return selected


CONFIGS = pairwise_configs()
# Nine pairwise configs x 27 seeds = 243 games in the default sweep.
SEEDS = range(27)
SLOW_SEEDS = range(27, 100)
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


def test_matrix_covers_each_pair_of_settings():
    for left, right in itertools.combinations(DIMENSIONS, 2):
        expected = set(itertools.product(DIMENSIONS[left], DIMENSIONS[right]))
        actual = {(spec[left], spec[right]) for spec in CONFIGS}
        assert actual == expected, (left, right)


def game_config(spec: dict, seed: int) -> GameConfig:
    return GameConfig(
        n_players=spec["n_players"],
        seed=seed,
        roles=RoleCounts(wolves=spec["wolves"]),
        rules=RuleFlags(**{key: spec[key] for key in RuleFlags.model_fields if key in spec}),
    )


class Probe:
    """RandomAgent that records prompts and marks its private text."""

    def __init__(self, state: GameState, seat: str, invalid_rate: float, prompts: list):
        self.inner = RandomAgent(state, seat, invalid_rate=invalid_rate)
        self.state, self.seat, self.prompts, self.n = state, seat, prompts, 0

    async def act(self, turn: Turn) -> CompletionResult:
        assert self.seat in self.state.alive, f"dead {self.seat} was asked to act"
        if turn.action == "doctor":
            rules = self.state.config.rules
            assert (self.seat in turn.targets) is (
                rules.doctor_self_protect
                and not (
                    rules.doctor_no_repeat
                    and self.state.last_doctor_protect_night == self.state.day - 1
                    and self.state.last_doctor_protect == self.seat
                )
            )
            previous = self.state.last_doctor_protect
            if (
                self.state.last_doctor_protect_night == self.state.day - 1
                and previous in self.state.alive
            ):
                assert (previous in turn.targets) is (not rules.doctor_no_repeat)
        self.prompts.append(
            (
                self.seat,
                self.state.roles[self.seat],
                turn.prompt,
                {s: self.state.roles[s] for s in self.state.seats if s not in self.state.alive},
                dict(self.state.names),
            )
        )
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
    phase_events = []
    phase_alive = set(alive)
    phase = Phase.SETUP
    for e in events:
        if e.type is EventType.PHASE_START and e.phase is Phase.NIGHT:
            day += 1
        if e.type in (EventType.PHASE_START, EventType.GAME_END):
            check_rounds(phase, phase_alive, phase_events, roles, rules)
            phase, phase_alive, phase_events = e.phase, set(alive), []
        phase_events.append(e)
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
                assert rules.doctor_self_protect or e.target != e.actor
                if rules.doctor_no_repeat and protects and protects[-1][0] == e.day - 1:
                    assert e.target != protects[-1][1], f"seq {e.seq}: doctor repeat"
                protects.append((e.day, e.target))
            case EventType.DEATH_REVEAL | EventType.ELIMINATION if e.target:
                assert e.meta.role == (roles[e.target] if rules.reveal_role_on_death else None)
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


def check_rounds(phase, alive, events, roles, rules):
    if phase is Phase.NIGHT:
        wolves = {seat for seat in alive if roles[seat] is Role.WOLF}
        rounds = rules.wolf_chat_rounds if len(wolves) > 1 else 1
        for kind in (EventType.WOLF_PROPOSAL, EventType.WOLF_CHAT):
            expected = wolves if kind is EventType.WOLF_PROPOSAL or len(wolves) > 1 else set()
            actual = [(e.meta.round, e.actor) for e in events if e.type is kind]
            assert Counter(actual) == Counter(
                (rnd, seat) for rnd in range(1, rounds + 1) for seat in expected
            )
    elif phase is Phase.DAY:
        actual = [(e.meta.round, e.actor) for e in events if e.type is EventType.STATEMENT]
        assert Counter(actual) == Counter(
            (rnd, seat) for rnd in range(1, rules.discussion_rounds + 1) for seat in alive
        )


def check_leaks(game: Game, prompts: list) -> None:
    chat = [e.public_text for e in game.events if e.type is EventType.WOLF_CHAT]
    assert all(t.startswith("howl-") or t == "(silent)" for t in chat)
    for seat, role, prompt, dead, names in prompts:
        thoughts = set(re.findall(r"thought-(p\d)-\d+", prompt))
        assert thoughts <= {seat}, f"{seat} sees private reasoning of {thoughts - {seat}}"
        if role is not Role.WOLF:
            assert "howl-" not in prompt and "(wolf channel)" not in prompt, f"{seat} sees wolves"
        for victim, dead_role in dead.items():
            shown = (
                dead_role.value
                if game.state.config.rules.reveal_role_on_death
                else "role not revealed"
            )
            assert f"{player(victim, names[victim])}, {shown}" in prompt
            if not game.state.config.rules.reveal_role_on_death:
                label = player(victim, names[victim])
                assert f"{label} was killed in the night ({dead_role.value})" not in prompt
                assert f"{label} was voted out ({dead_role.value})" not in prompt


@pytest.mark.parametrize("reveal", (True, False))
def test_scripted_death_observation(reveal):
    scripted = build(reveal=reveal)
    state, events = scripted.checkpoints["vote2"]
    for e in events:
        if e.type in DEATHS and e.target:
            assert e.meta.role == (state.roles[e.target] if reveal else None)
    observation = observe(state, events, scripted.cast["villager"])
    for victim in (scripted.cast["v_night1"], scripted.cast["v_day1"]):
        shown = state.roles[victim].value if reveal else "role not revealed"
        assert f"{player(victim, state.names[victim])}, {shown}" in observation


def test_doctor_can_repeat_protect_when_enabled():
    state = GameState(GameConfig(seed=3, rules=RuleFlags(doctor_no_repeat=False)))
    doctor = next(s for s in state.seats if state.roles[s] is Role.DOCTOR)
    victims = [s for s in state.seats if state.roles[s] is Role.VILLAGER][:2]
    log = MemoryLog()
    for victim in victims:
        start_phase(state, log, Phase.NIGHT)
        assert doctor in legal_targets(state, doctor, Phase.NIGHT)
        resolve_night(state, log, victim, save=doctor, doctor=doctor)
    saves = [e.target for e in log.events if e.type is EventType.DOCTOR_SAVE]
    assert saves == [doctor, doctor]


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


@pytest.mark.slow
@pytest.mark.skipif(not os.environ.get("SOCIETYSIM_SLOW"), reason="set SOCIETYSIM_SLOW=1")
@pytest.mark.parametrize(
    ("spec", "seed"),
    [case(i, spec, seed) for (i, spec), seed in itertools.product(enumerate(CONFIGS), SLOW_SEEDS)],
)
async def test_invariants_hold_slow(spec, seed):
    await test_invariants_hold(spec, seed)


@pytest.mark.parametrize("i", range(0, len(CONFIGS), 5))
async def test_same_seed_gives_byte_identical_log(tmp_path, i):
    spec = CONFIGS[i]

    async def log(out: Path) -> bytes:
        agent = partial(RandomAgent, invalid_rate=spec["invalid_rate"])
        await run_game(game_config(spec, 11), agent, [JsonlSink("b", runs_dir=out)], "g")
        return (out / "b" / "g.jsonl").read_bytes()

    first, second = await log(tmp_path / "a"), await log(tmp_path / "b")
    assert first and first == second
