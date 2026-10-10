from collections import Counter

import pytest

from societysim.batch.assign import assign_games
from societysim.config import BatchConfig
from societysim.engine.state import GameState

ROLES = ["wolf", "seer", "doctor", "villager"]


def cfg(**kw) -> BatchConfig:
    base = dict(batch_id="b1", seed=7, n_games=36, models=["a", "b", "c"], budget_usd=1.0)
    return BatchConfig(**(base | kw))


def test_deterministic_and_stable_ids():
    a = assign_games(cfg())
    assert a == assign_games(cfg())
    assert len({x.game_id for x in a}) == 36 and len({x.seed for x in a}) == 36
    # A longer batch keeps the same ids and seeds for the shared prefix (resume-safe).
    longer = assign_games(cfg(n_games=40))
    assert [(x.game_id, x.seed) for x in longer[:36]] == [(x.game_id, x.seed) for x in a]
    assert assign_games(cfg(seed=8))[0].seed != a[0].seed


def test_mixed_balance_seats_and_roles():
    c = cfg()
    seats: Counter = Counter()
    roles: Counter = Counter()
    for a in assign_games(c):
        gc = a.game_config(c.game)
        assert len(gc.seat_models()) == c.game.n_players
        for seat, role in GameState(gc).roles.items():
            roles[(a.models[int(seat[1:])], role.value)] += 1
        for k, m in enumerate(a.models):
            seats[(m, f"p{k}")] += 1
    seat_ids = [f"p{k}" for k in range(c.game.n_players)]
    # For each seat and each role, every model plays it about equally often.
    for domain, counter in ((seat_ids, seats), (ROLES, roles)):
        for d in domain:
            vals = [counter[(m, d)] for m in c.models]
            assert max(vals) - min(vals) <= 1, (d, vals)


def test_selfplay_round_robin():
    games = assign_games(cfg(mode="selfplay", n_games=9))
    assert all(len(set(a.models)) == 1 for a in games)
    assert set(Counter(a.models[0] for a in games).values()) == {3}


def test_model_game_limits():
    games = assign_games(cfg(n_games=30, model_game_limits={"c": 5}))
    assert sum("c" in a.models for a in games) == 5
    assert all(len(a.models) == 7 for a in games)
    assert all(set(a.models) == {"a", "b"} for a in games[10:])


def test_limits_exhausted_raises():
    with pytest.raises(ValueError):
        assign_games(cfg(models=["a"], n_games=3, model_game_limits={"a": 1}))
