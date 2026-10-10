"""Pure action legality predicates for the current game state."""

from societysim.engine.state import GameState
from societysim.roles import Phase, Role


def legal_targets(state: GameState, actor: str, phase: Phase) -> set[str]:
    """Return legal seat ids (and possibly ``abstain``) for an actor's action.

    Night actions are selected by the actor's true role. Day statements have no
    target. A dead or unknown seat can never act.
    """
    if actor not in state.alive:
        return set()

    role = state.roles[actor]
    if phase == Phase.NIGHT:
        if role is Role.WOLF:
            return {seat for seat in state.alive if state.roles[seat] is not Role.WOLF}
        if role is Role.SEER:
            return state.alive - {actor}
        if role is Role.DOCTOR:
            targets = set(state.alive)
            if not state.config.rules.doctor_self_protect:
                targets.discard(actor)
            if (
                state.config.rules.doctor_no_repeat
                and state.last_doctor_protect_night == state.day - 1
            ):
                targets.discard(state.last_doctor_protect)
            return targets
        return set()

    if phase == Phase.VOTE:
        targets = set(state.alive)
        if state.config.rules.allow_abstain:
            targets.add("abstain")
        return targets

    return set()
