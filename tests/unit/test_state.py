from collections import Counter

import pytest

from societysim.config import GameConfig, RoleCounts
from societysim.engine.state import GameState
from societysim.roles import Phase, Role


@pytest.mark.parametrize(("n", "villagers"), [(7, 3), (8, 4), (9, 5)])
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
    without_specials = GameState(GameConfig(roles=RoleCounts(seers=0, doctors=0)))
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
    state = GameState(GameConfig(seed=17, models=models))
    without_models = GameState(GameConfig(seed=17))
    assert state.models == dict(zip(state.seats, models, strict=True))
    assert state.roles == without_models.roles
    assert state.speaking_order() == without_models.speaking_order()
