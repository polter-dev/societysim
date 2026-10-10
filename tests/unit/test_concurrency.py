"""Concurrent turns (#161): the per-game call cap bounds calls in flight, and the log is the
same whatever the cap, even when calls finish out of order."""

import asyncio
import random
from functools import partial

import pytest

from societysim.agents.scripted import RandomAgent
from societysim.config import GameConfig
from societysim.logging.events import EventLogger
from societysim.orchestrator import Game, run_game


class SlowAgent(RandomAgent):
    """RandomAgent whose replies take a random time, so parallel calls finish out of order.
    The delays come from their own RNG, so they never touch the game's draws. Records the
    actions already in flight when each call starts."""

    def __init__(self, state, seat, stats, invalid_rate=0.0):
        super().__init__(state, seat, invalid_rate)
        self.delay = random.Random(f"delay-{seat}")
        self.stats = stats

    async def act(self, turn):
        flight = self.stats["flight"]
        self.stats["calls"].append((turn.action, list(flight)))
        flight.append(turn.action)
        self.stats["peak"] = max(self.stats["peak"], len(flight))
        try:
            await asyncio.sleep(self.delay.random() / 1000)
            return await super().act(turn)
        finally:
            flight.remove(turn.action)


def factory(invalid_rate=0.0):
    stats = {"flight": [], "calls": [], "peak": 0}
    return partial(SlowAgent, stats=stats, invalid_rate=invalid_rate), stats


def dump(game):
    """The log as JSON, without the only timing field (usage latency, 0 here anyway)."""
    rows = []
    for e in game.events:
        row = e.model_dump(mode="json")
        if usage := row["meta"].get("usage"):
            usage.pop("latency_ms", None)
        rows.append(row)
    return rows


@pytest.mark.parametrize("seed", range(8))
async def test_log_is_the_same_for_every_cap(seed):
    # invalid_rate sends some calls through retry and fallback, which draws state.rng.
    config = GameConfig(n_players=9, seed=seed)
    logs = []
    for cap in (1, 2, 4, 9):
        make, _ = factory(invalid_rate=0.5)
        logs.append(dump(await run_game(config, make, [], "g", max_calls=cap)))
    assert all(log == logs[0] for log in logs[1:])
    types = {row["type"] for row in logs[0]}
    assert {"vote", "fallback_action", "invalid_output"} <= types


@pytest.mark.parametrize("cap", [1, 2, 4])
async def test_cap_bounds_calls_in_flight(cap):
    make, stats = factory()
    await run_game(GameConfig(n_players=9, seed=1), make, [], "g", max_calls=cap)
    assert stats["peak"] == cap  # 9 voters fill any cap up to 9, and never go over


async def test_default_cap_is_four():
    make, stats = factory()
    await run_game(GameConfig(n_players=9, seed=1), make, [], "g")
    assert stats["peak"] == 4


async def test_only_simultaneous_decisions_overlap():
    make, stats = factory()
    await run_game(GameConfig(n_players=9, seed=2), make, [], "g", max_calls=9)
    for action, flight in stats["calls"]:
        if action == "statement":  # each speaker sees the earlier statements
            assert flight == []
        if action == "wolf_chat":  # each wolf sees the other's message; Seer/Doctor may run
            assert "wolf_chat" not in flight
        if action == "vote":  # votes are cast at once; no day or night call is pending
            assert set(flight) <= {"vote"}
    assert any(a == "vote" and f for a, f in stats["calls"])
    assert any(a in ("seer", "doctor") and "wolf_chat" in f for a, f in stats["calls"])


def test_cap_below_one_is_rejected():
    make, _ = factory()
    with pytest.raises(ValueError):
        Game(GameConfig(), make, EventLogger("g", []), max_calls=0)
