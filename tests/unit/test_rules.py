import os
import subprocess
import sys

import pytest

from societysim.config import GameConfig, RuleFlags
from societysim.engine.rules import legal_targets
from societysim.engine.state import GameState
from societysim.roles import Phase, Role


@pytest.fixture
def game():
    state = GameState(GameConfig(seed=7))
    state.day = 1
    state.phase = Phase.NIGHT
    return state


def seat_with_role(game, role):
    return next(seat for seat, actual in game.roles.items() if actual is role)


def ordered(game, targets):
    seats = [seat for seat in game.seats if seat in targets]
    return seats + (["abstain"] if "abstain" in targets else [])


def test_wolf_proposals_include_only_living_non_wolves(game):
    wolf = seat_with_role(game, Role.WOLF)
    expected = {seat for seat in game.alive if game.roles[seat] is not Role.WOLF}
    assert legal_targets(game, wolf, Phase.NIGHT) == ordered(game, expected)
    victim = next(iter(expected))
    game.alive.remove(victim)
    assert legal_targets(game, wolf, Phase.NIGHT) == ordered(game, expected - {victim})


def test_lone_wolf_can_still_propose(game):
    wolves = [seat for seat in game.seats if game.roles[seat] is Role.WOLF]
    game.alive.remove(wolves[1])
    assert legal_targets(game, wolves[0], Phase.NIGHT) == ordered(game, game.alive - {wolves[0]})
    assert legal_targets(game, wolves[1], Phase.NIGHT) == []


def test_seer_checks_other_living_players_including_wolves(game):
    seer = seat_with_role(game, Role.SEER)
    dead = seat_with_role(game, Role.VILLAGER)
    game.alive.remove(dead)
    assert legal_targets(game, seer, Phase.NIGHT) == ordered(game, game.alive - {seer})
    assert any(game.roles[target] is Role.WOLF for target in legal_targets(game, seer, Phase.NIGHT))


@pytest.mark.parametrize("self_protect", [True, False])
def test_doctor_can_protect_wolves_and_self_only_when_enabled(self_protect):
    game = GameState(GameConfig(rules=RuleFlags(doctor_self_protect=self_protect)))
    doctor = seat_with_role(game, Role.DOCTOR)
    expected = game.alive if self_protect else game.alive - {doctor}
    assert legal_targets(game, doctor, Phase.NIGHT) == ordered(game, expected)
    assert any(game.roles[target] is Role.WOLF for target in expected)


def test_doctor_no_repeat_applies_only_to_previous_night(game):
    doctor = seat_with_role(game, Role.DOCTOR)
    wolf = seat_with_role(game, Role.WOLF)
    game.last_doctor_protect = wolf
    game.last_doctor_protect_night = 1
    game.day = 2
    assert wolf not in legal_targets(game, doctor, Phase.NIGHT)
    game.day = 3  # no protection on night 2
    assert wolf in legal_targets(game, doctor, Phase.NIGHT)
    game.day = 4
    game.last_doctor_protect_night = 3
    assert wolf not in legal_targets(game, doctor, Phase.NIGHT)


def test_doctor_repeat_flag_can_disable_restriction(game):
    doctor = seat_with_role(game, Role.DOCTOR)
    game.config.rules.doctor_no_repeat = False
    game.last_doctor_protect = doctor
    game.last_doctor_protect_night = 1
    game.day = 2
    assert doctor in legal_targets(game, doctor, Phase.NIGHT)


def test_doctor_cannot_protect_dead_player(game):
    doctor = seat_with_role(game, Role.DOCTOR)
    wolf = seat_with_role(game, Role.WOLF)
    game.alive.remove(wolf)
    assert wolf not in legal_targets(game, doctor, Phase.NIGHT)


@pytest.mark.parametrize("allow_abstain", [True, False])
def test_vote_targets_follow_abstain_flag(allow_abstain):
    game = GameState(GameConfig(rules=RuleFlags(allow_abstain=allow_abstain)))
    actor = game.seats[0]
    dead = game.seats[1]
    game.alive.remove(dead)
    expected = game.alive - {actor}
    if allow_abstain:
        expected.add("abstain")
    assert legal_targets(game, actor, Phase.VOTE) == ordered(game, expected)


def test_dead_and_unknown_actors_and_non_action_phases_have_no_targets(game):
    actor = seat_with_role(game, Role.DOCTOR)
    game.alive.remove(actor)
    assert legal_targets(game, actor, Phase.NIGHT) == []
    assert legal_targets(game, actor, Phase.VOTE) == []
    assert legal_targets(game, "p9", Phase.NIGHT) == []
    living = next(iter(game.alive))
    assert legal_targets(game, living, Phase.DAY) == []
    assert legal_targets(game, living, Phase.SETUP) == []
    villager = seat_with_role(game, Role.VILLAGER)
    assert legal_targets(game, villager, Phase.NIGHT) == []


def test_legal_targets_are_a_list_in_seat_order(game):
    actor = seat_with_role(game, Role.DOCTOR)
    game.alive.remove("p1")
    targets = legal_targets(game, actor, Phase.NIGHT)
    assert isinstance(targets, list)
    assert targets == [seat for seat in game.seats if seat in game.alive]
    assert legal_targets(game, actor, Phase.VOTE) == [t for t in targets if t != actor] + [
        "abstain"
    ]


def test_vote_excludes_self(game):
    actor = seat_with_role(game, Role.VILLAGER)
    assert actor not in legal_targets(game, actor, Phase.VOTE)


def test_seeded_fallback_choices_ignore_python_hash_seed():
    script = """
import json
from societysim.config import GameConfig
from societysim.engine.rules import legal_targets
from societysim.engine.state import GameState
from societysim.roles import Phase, Role

state = GameState(GameConfig(seed=7))
wolf = next(seat for seat in state.seats if state.roles[seat] is Role.WOLF)
targets = legal_targets(state, wolf, Phase.NIGHT)
print(json.dumps([state.rng.choice(targets) for _ in range(30)]))
"""
    outputs = [
        subprocess.check_output(
            [sys.executable, "-c", script],
            env={**os.environ, "PYTHONHASHSEED": str(hash_seed)},
            text=True,
        )
        for hash_seed in (1, 2)
    ]
    assert outputs[0] == outputs[1]
