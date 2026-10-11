from collections import Counter
from pathlib import Path

import pytest

from societysim.batch.assign import assign_games, derive_seed
from societysim.batch.runner import fingerprint, plan
from societysim.config import BatchConfig, ModelSpec, load_yaml
from societysim.engine.state import GameState
from societysim.models.registry import load_registry

ROLES = ["wolf", "seer", "doctor", "villager"]
ROOT = Path(__file__).resolve().parents[2]


def cfg(**kw) -> BatchConfig:
    base = dict(
        batch_id="b1",
        seed=7,
        n_games=36,
        models=["a", "b", "c"],
        budget_usd=1.0,
        game={"n_players": 7},
    )
    return BatchConfig(**(base | kw))


def test_unsalted_baseline_is_unchanged():
    small = cfg(n_games=3)
    specs = {m: ModelSpec(name=m, litellm_model=f"openai/{m}") for m in small.models}
    assert [derive_seed(small.seed, i) for i in range(3)] == [4067440343, 3452413450, 1070041810]
    assert [(a.game_id, a.seed, a.models) for a in assign_games(small)] == [
        ("b1-g0000", 4067440343, ["a", "b", "c", "a", "b", "c", "a"]),
        ("b1-g0001", 3140586401, ["b", "c", "a", "b", "c", "a", "b"]),
        ("b1-g0002", 4026708158, ["c", "a", "b", "c", "a", "b", "c"]),
    ]
    assert fingerprint(small, specs, "llm") == "4ac334c73e2169c1"
    assert [
        (r["game_id"], r["seed"], r["seats"], r["est_usd"]) for r in plan(small, specs)[:2]
    ] == [
        (
            "b1-g0000",
            4067440343,
            [
                ("p0", "a", "seer"),
                ("p1", "b", "doctor"),
                ("p2", "c", "wolf"),
                ("p3", "a", "villager"),
                ("p4", "b", "villager"),
                ("p5", "c", "villager"),
                ("p6", "a", "wolf"),
            ],
            0.0,
        ),
        (
            "b1-g0001",
            3140586401,
            [
                ("p0", "b", "wolf"),
                ("p1", "c", "doctor"),
                ("p2", "a", "wolf"),
                ("p3", "b", "seer"),
                ("p4", "c", "villager"),
                ("p5", "a", "villager"),
                ("p6", "b", "villager"),
            ],
            0.0,
        ),
    ]

    committed = load_yaml(ROOT / "configs/selfplay_baseline_10p.yaml", BatchConfig)
    registry = {s.name: s for s in load_registry(ROOT / "configs/models.yaml").models}
    assert [derive_seed(committed.seed, i) for i in range(3)] == [
        1786861591,
        3131472587,
        1947831253,
    ]
    assert [(a.game_id, a.seed, a.models[0]) for a in assign_games(committed)[:3]] == [
        ("selfplay_baseline_10p-g0000", 1786861591, "gemma4-26b"),
        ("selfplay_baseline_10p-g0001", 3131472587, "qwen3-235b"),
        ("selfplay_baseline_10p-g0002", 1947831253, "llama4-maverick"),
    ]
    assert fingerprint(committed, registry, "llm") == "ca7672fc8070dc4d"
    assert [
        (r["game_id"], r["seed"], [role for _, _, role in r["seats"]], r["est_usd"])
        for r in plan(committed, registry)[:2]
    ] == [
        (
            "selfplay_baseline_10p-g0000",
            1786861591,
            [
                "villager",
                "villager",
                "villager",
                "seer",
                "wolf",
                "villager",
                "villager",
                "doctor",
                "villager",
                "wolf",
            ],
            0.03312,
        ),
        (
            "selfplay_baseline_10p-g0001",
            3131472587,
            [
                "wolf",
                "villager",
                "villager",
                "doctor",
                "villager",
                "villager",
                "seer",
                "wolf",
                "villager",
                "villager",
            ],
            0.048,
        ),
    ]


def test_private_salt_changes_seeds_and_roles():
    committed = load_yaml(ROOT / "configs/selfplay_baseline_10p.yaml", BatchConfig)
    plain = assign_games(committed)
    first = assign_games(committed, salt="private-one")
    second = assign_games(committed, salt="private-two")
    assert assign_games(committed, salt="") == plain
    assert derive_seed(committed.seed, 0, salt="") == derive_seed(committed.seed, 0)
    assert first == assign_games(committed, salt="private-one")
    assert [a.seed for a in first] != [a.seed for a in second]
    assert [a.seed for a in first] != [a.seed for a in plain]

    def deals(assignments):
        return [GameState(a.game_config(committed.game)).roles for a in assignments]

    assert deals(first) != deals(second)
    assert deals(first) != deals(plain)
    registry = {s.name: s for s in load_registry(ROOT / "configs/models.yaml").models}
    public_plan = plan(committed, registry)
    private_plan = plan(committed, registry, salt="private-one")
    assert [(r["seed"], r["seats"]) for r in public_plan] != [
        (r["seed"], r["seats"]) for r in private_plan
    ]
    assert fingerprint(committed, registry, "llm", salt="") == fingerprint(
        committed, registry, "llm"
    )
    assert fingerprint(committed, registry, "llm", salt="private-one") != fingerprint(
        committed, registry, "llm"
    )


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
