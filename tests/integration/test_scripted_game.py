"""Full games played by RandomAgent. No API calls: checks schema, termination and replay."""

import json
from functools import partial
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from societysim.agents.base import Turn
from societysim.agents.scripted import RandomAgent
from societysim.config import GameConfig
from societysim.engine.state import GameState
from societysim.logging.events import Event, EventType
from societysim.logging.sinks.jsonl import JsonlSink
from societysim.orchestrator import run_game

ROOT = Path(__file__).parents[2]
SCHEMA = json.loads((ROOT / "schema/events.schema.json").read_text())
VALIDATOR = Draft202012Validator(SCHEMA)
WINNERS = ("wolves", "villagers", "draw")


async def play(seed: int, out: Path, **agent_kw):
    """Play one 7-player game with a JsonlSink under `out`. Returns (game, JSONL bytes)."""
    sink = JsonlSink("batch", runs_dir=out)
    game = await run_game(
        GameConfig(n_players=7, seed=seed), partial(RandomAgent, **agent_kw), [sink], "g"
    )
    return game, (out / "batch" / "g.jsonl").read_bytes()


async def test_random_agent_picks_legal_actions_and_rates_everyone_else():
    state = GameState(GameConfig(n_players=7, seed=1))
    others = [s for s in state.seats if s != "p0"]
    agent = RandomAgent(state, "p0")
    for action in ("statement", "vote", "wolf_chat", "seer", "doctor"):
        targets = others + (["abstain"] if action == "vote" else [])
        result = await agent.act(Turn(action, "prompt", others, targets))
        assert result.parsed is not None and result.error is None
        assert result.usage.model == "scripted"
        rated = [e.player for e in result.parsed.private_reasoning.suspicion]
        assert sorted(rated) == sorted(others)  # each other living player, exactly once
        act = result.parsed.action
        if action == "statement":
            assert set(act.accuse) <= set(others)
        elif action == "vote":
            assert act.vote in targets
        elif action == "wolf_chat":
            assert act.proposed_target in targets
        else:
            assert act.target in targets


async def test_agent_rng_is_seeded_per_seat_and_does_not_advance_engine_rng():
    state = GameState(GameConfig(n_players=7, seed=1))
    other_state = GameState(GameConfig(n_players=7, seed=1))
    agent = RandomAgent(state, "p0")
    other_agent = RandomAgent(other_state, "p0")
    turn = Turn("vote", "prompt", ["p1", "p2"], ["p1", "p2", "abstain"])
    before = state.rng.getstate()
    other_state.rng.random()  # engine draws cannot change this agent's choices

    for _ in range(10):
        first = await agent.act(turn)
        second = await other_agent.act(turn)
        assert first.raw == second.raw
    assert state.rng.getstate() == before


async def test_invalid_rate_gives_unparsed_reply():
    state = GameState(GameConfig(n_players=7, seed=1))
    agent = RandomAgent(state, "p0", invalid_rate=1.0)
    result = await agent.act(Turn("vote", "prompt", ["p1"], ["p1", "abstain"]))
    assert result.parsed is None and result.error


@pytest.mark.parametrize("seed", range(5))
async def test_full_game_validates_against_schema_and_terminates(tmp_path, seed):
    game, data = await play(seed, tmp_path)
    assert game.winner in WINNERS
    lines = data.decode().splitlines()
    for line in lines:
        VALIDATOR.validate(json.loads(line))
    events = [Event.model_validate_json(line) for line in lines]
    assert [e.seq for e in events] == list(range(len(events)))
    assert events[0].type is EventType.GAME_START
    assert events[-1].type is EventType.GAME_END
    assert events[-1].meta.winner == game.winner


async def test_invalid_replies_exercise_retry_and_fallback(tmp_path):
    game, data = await play(3, tmp_path, invalid_rate=0.5)
    assert game.winner in WINNERS
    lines = data.decode().splitlines()
    types = set(json.loads(line)["type"] for line in lines)
    assert types >= set(["invalid_output", "fallback_action"])
    for line in lines:
        VALIDATOR.validate(json.loads(line))


async def test_same_seed_replays_byte_identical(tmp_path):
    _, first = await play(7, tmp_path.joinpath("a"))
    _, second = await play(7, tmp_path.joinpath("b"))
    assert first == second


async def test_different_seeds_diverge(tmp_path):
    _, first = await play(7, tmp_path.joinpath("a"))
    _, other = await play(8, tmp_path.joinpath("b"))
    assert first != other

    # The game_start event records the seed, so compare actual choices too.
    def choices(data):
        return [
            (event["type"], event.get("actor"), event.get("target"))
            for event in map(json.loads, data.splitlines())
            if event["type"] in {"vote", "wolf_proposal", "seer_check"}
        ]

    assert choices(first) != choices(other)


async def test_zero_sinks_plays_a_game():
    game = await run_game(GameConfig(n_players=7, seed=2), RandomAgent, [], "g")
    assert game.winner in WINNERS
    assert game.events[-1].type is EventType.GAME_END


async def test_unknown_target_style_action_gets_a_target():
    state = GameState(GameConfig(n_players=7, seed=1))
    agent = RandomAgent(state, "p0")
    result = await agent.act(Turn("lone_wolf", "prompt", ["p1"], ["p1", "p2"]))
    assert result.parsed is not None and result.error is None
    assert result.parsed.action.target in ("p1", "p2")
