"""Pure action legality predicates for the current game state."""

from societysim.engine.state import GameState
from societysim.roles import Phase, Role


def legal_targets(state: GameState, actor: str, phase: Phase) -> list[str]:
    """Return legal targets in seat order, with optional ``abstain`` last.

    Night actions are selected by the actor's true role. Day statements have no
    target. A dead or unknown seat can never act.
    """
    if actor not in state.alive:
        return []

    role = state.roles[actor]
    if phase == Phase.NIGHT:
        if role is Role.WOLF:
            return [
                seat
                for seat in state.seats
                if seat in state.alive and state.roles[seat] is not Role.WOLF
            ]
        if role is Role.SEER:
            return [seat for seat in state.seats if seat in state.alive and seat != actor]
        if role is Role.DOCTOR:
            excluded = None
            if (
                state.config.rules.doctor_no_repeat
                and state.last_doctor_protect_night == state.day - 1
            ):
                excluded = state.last_doctor_protect
            return [
                seat
                for seat in state.seats
                if seat in state.alive
                and (state.config.rules.doctor_self_protect or seat != actor)
                and seat != excluded
            ]
        return []

    if phase == Phase.VOTE:
        # Votes name another living player. Self-votes are illegal; only the Doctor may self-target.
        targets = [seat for seat in state.seats if seat in state.alive and seat != actor]
        if state.config.rules.allow_abstain:
            targets.append("abstain")
        return targets

    return []
