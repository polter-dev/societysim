from collections import Counter

import pytest

from societysim.config import GameConfig, RoleCounts
from societysim.engine.state import NAME_POOLS, NAMES, GameState
from societysim.roles import Phase, Role


@pytest.mark.parametrize(("n", "villagers"), [(7, 3), (8, 4), (9, 5), (10, 6)])
def test_default_setup_counts_and_seats(n, villagers):
    state = GameState(GameConfig(n_players=n, seed=42))
    assert state.seats == tuple(f"p{i}" for i in range(n))
    assert len(set(state.names.values())) == n
    assert state.alive == set(state.seats)
    assert Counter(state.roles.values()) == {
        Role.WOLF: 2,
        Role.SEER: 1,
        Role.DOCTOR: 1,
        Role.VILLAGER: villagers,
    }
    assert state.day == state.t == 0
    assert state.phase is Phase.SETUP
    assert state.last_doctor_protect is None
    assert state.last_doctor_protect_night is None
    assert len(state.seer_results) == 1


def test_three_wolves_and_optional_special_roles():
    state = GameState(GameConfig(n_players=9, roles=RoleCounts(wolves=3)))
    assert Counter(state.roles.values()) == {
        Role.WOLF: 3,
        Role.SEER: 1,
        Role.DOCTOR: 1,
        Role.VILLAGER: 4,
    }
    without_specials = GameState(GameConfig(n_players=7, roles=RoleCounts(seers=0, doctors=0)))
    assert Counter(without_specials.roles.values()) == {Role.WOLF: 2, Role.VILLAGER: 5}
    assert without_specials.seer_results == {}


def test_same_seed_repeats_setup_and_random_draws():
    config = GameConfig(seed=123, models=[f"model-{i}" for i in range(9)], n_players=9)
    first, second = GameState(config), GameState(config)
    assert first.names == second.names
    assert first.roles == second.roles
    assert first.models == second.models
    assert first.speaking_order() == second.speaking_order()
    assert first.rng.choice(first.seats) == second.rng.choice(second.seats)
    assert first.rng is not second.rng


def test_speaking_order_excludes_dead_and_uses_seat_order_as_input():
    state = GameState(GameConfig(seed=8))
    state.alive.remove("p2")
    assert set(state.speaking_order()) == state.alive
    assert len(state.speaking_order()) == len(state.alive)


def test_one_model_is_repeated_at_every_seat():
    state = GameState(GameConfig(models=["same"]))
    assert state.models == dict.fromkeys(state.seats, "same")


def test_distinct_models_keep_configured_seats_without_consuming_randomness():
    models = [f"model-{i}" for i in range(7)]
    state = GameState(GameConfig(n_players=7, seed=17, models=models))
    without_models = GameState(GameConfig(n_players=7, seed=17))
    assert state.models == dict(zip(state.seats, models, strict=True))
    assert state.roles == without_models.roles
    assert state.speaking_order() == without_models.speaking_order()


def test_name_pools_are_big_short_and_distinct():
    everyone = [n for pool in NAME_POOLS.values() for n in pool]
    assert len(everyone) == len(set(everyone))  # a name tells you its pool
    for pool in NAME_POOLS.values():
        assert len(pool) >= GameConfig.model_fields["n_players"].metadata[1].le
        assert len({n[0] for n in pool}) == len(pool)  # distinct initials within a pool
        assert max(map(len, pool)) <= 5
    assert set(NAMES) <= set(everyone)  # old logs' names are still known names


PRESENTATIONS = {"f": "feminine", "m": "masculine", "n": "neutral"}
MIXED = ["f", "m", "n", "f", "m", "x", "human", "f", "m", "f"]  # x and human: not in the map


@pytest.mark.parametrize("seed", range(20))
def test_each_seat_name_matches_its_models_presentation(seed):
    state = GameState(GameConfig(n_players=10, seed=seed, models=MIXED), PRESENTATIONS)
    for seat, model in state.models.items():
        assert state.names[seat] in NAME_POOLS[PRESENTATIONS.get(model, "neutral")]
    assert len(set(state.names.values())) == 10


@pytest.mark.parametrize("kind", ["feminine", "masculine", "neutral"])
def test_one_model_at_every_seat_gets_ten_different_names_from_its_pool(kind):
    state = GameState(GameConfig(n_players=10, seed=5, models=["solo"]), {"solo": kind})
    assert sorted(state.names.values()) == sorted(NAME_POOLS[kind])


def test_names_are_seeded_and_presentations_change_nothing_else():
    config = GameConfig(n_players=10, seed=11, models=MIXED)
    first, second = GameState(config, PRESENTATIONS), GameState(config, PRESENTATIONS)
    assert first.names == second.names
    plain = GameState(config)
    assert set(plain.names.values()) == set(NAME_POOLS["neutral"])  # no map: neutral names
    assert first.roles == plain.roles
    assert first.speaking_order() == plain.speaking_order()
    p0 = {GameState(GameConfig(seed=s, models=MIXED), PRESENTATIONS).names["p0"] for s in range(30)}
    assert len(p0) > 1  # the seed picks the name, not the seat
