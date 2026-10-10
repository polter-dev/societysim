"""Pure state transitions: night resolution, vote tally, win and draw checks.

Nothing here does I/O or calls a model. Each transition logs its events through an
`EventLog` (anything with `log(**fields) -> Event`, e.g. EventLogger). Agent-chosen values
(target, vote, usage, reasoning) are passed in by the orchestrator.
"""

from collections import Counter
from collections.abc import Mapping
from typing import Any, Protocol

from societysim.engine.rules import legal_targets
from societysim.engine.state import GameState
from societysim.logging.events import Event, EventType, Suspicion, Visibility
from societysim.models.client import Usage
from societysim.roles import Phase, Role, SeerResult, Winner


class EventLog(Protocol):
    def log(self, **fields: Any) -> Event: ...


def _emit(
    state: GameState,
    log: EventLog,
    type_: EventType,
    visibility: Visibility,
    *,
    actor: str | None = None,
    target: str | None = None,
    public_text: str | None = None,
    private_reasoning: str | None = None,
    suspicion: list[Suspicion] | None = None,
    **meta: Any,
) -> Event:
    """Log one event stamped with the current t, day and phase. None meta values are dropped."""
    return log.log(
        t=state.t,
        day=state.day,
        phase=state.phase,
        type=type_,
        visibility=visibility,
        actor=actor,
        target=target,
        public_text=public_text,
        private_reasoning=private_reasoning,
        suspicion=suspicion,
        meta={k: v for k, v in meta.items() if v is not None},
    )


def start_phase(state: GameState, log: EventLog, phase: Phase) -> None:
    """Enter a phase. Night start is the only place `day` increments."""
    if phase is Phase.NIGHT:
        state.day += 1
        text = f"Night {state.day}"
    elif phase is Phase.DAY:
        text = f"Day {state.day}"
    else:
        text = f"Day {state.day} vote"
    state.phase = phase
    state.t += 1
    _emit(state, log, EventType.PHASE_START, Visibility.PUBLIC, public_text=text)


def speaking_orders(state: GameState) -> list[list[str]]:
    """One seeded speaking order per discussion round. Call once, right after the day starts."""
    return [state.speaking_order() for _ in range(state.config.rules.discussion_rounds)]


def seer_check(
    state: GameState,
    log: EventLog,
    seer: str,
    target: str,
    *,
    private_reasoning: str | None = None,
    suspicion: list[Suspicion] | None = None,
    usage: Usage | None = None,
) -> SeerResult | None:
    """Record the Seer's check. Call before `resolve_night`, so a target killed tonight still
    gets a result. A dead Seer gets no check and no event."""
    if target not in legal_targets(state, seer, Phase.NIGHT):
        return None
    result: SeerResult = "wolf" if state.roles[target] is Role.WOLF else "not_wolf"
    state.seer_results[seer].append((state.day, target, result))
    _emit(
        state,
        log,
        EventType.SEER_CHECK,
        Visibility.SEER,
        actor=seer,
        target=target,
        private_reasoning=private_reasoning,
        suspicion=suspicion,
        result=result,
        usage=usage,
    )
    return result


def resolve_night(
    state: GameState,
    log: EventLog,
    kill: str,
    *,
    method: str = "consensus",
    tally: Mapping[str, int] | None = None,
    save: str | None = None,
    doctor: str | None = None,
    private_reasoning: str | None = None,
    suspicion: list[Suspicion] | None = None,
    usage: Usage | None = None,
) -> list[str]:
    """Apply the wolves' kill and the Doctor's save. Returns the seats that died tonight.

    Logs night_kill, then doctor_save (if a living Doctor protected someone), then
    death_reveal: one per death, or one with target None when nobody died. A save on the
    kill target prevents the death. The save also sets the Doctor's no-repeat marker.
    """
    _emit(
        state,
        log,
        EventType.NIGHT_KILL,
        Visibility.WOLVES,
        target=kill,
        method=method,
        tally=dict(tally) if tally is not None else None,
    )
    saved = False
    if (
        doctor is not None
        and save is not None
        and save in legal_targets(state, doctor, Phase.NIGHT)
    ):
        saved = save == kill
        state.last_doctor_protect = save
        state.last_doctor_protect_night = state.day
        _emit(
            state,
            log,
            EventType.DOCTOR_SAVE,
            Visibility.DOCTOR,
            actor=doctor,
            target=save,
            private_reasoning=private_reasoning,
            suspicion=suspicion,
            saved=saved,
            usage=usage,
        )

    deaths = [] if saved else [kill]
    for seat in deaths:
        state.alive.discard(seat)
    if not deaths:
        _emit(state, log, EventType.DEATH_REVEAL, Visibility.PUBLIC)
    for seat in deaths:
        role = state.roles[seat] if state.config.rules.reveal_role_on_death else None
        _emit(state, log, EventType.DEATH_REVEAL, Visibility.PUBLIC, target=seat, role=role)
    return deaths


def tally(state: GameState, log: EventLog, votes: Mapping[str, str | None]) -> str | None:
    """Count the day vote and eliminate the plurality. Returns the eliminated seat or None.

    `votes` maps voter seat to a target seat, "abstain" or None (also an abstain). Only
    non-abstain votes count. All-abstain and a tied top give `no_elimination`. `meta.tally`
    is always logged, in seat order.
    """
    counts = Counter(choice for choice in votes.values() if choice not in (None, "abstain"))
    counted = {seat: counts[seat] for seat in state.seats if counts[seat]}
    if not counted:
        _emit(
            state,
            log,
            EventType.NO_ELIMINATION,
            Visibility.PUBLIC,
            tally=counted,
            reason="all_abstain",
        )
        return None
    top = max(counted.values())
    leaders = [seat for seat, n in counted.items() if n == top]
    if len(leaders) > 1:
        _emit(
            state,
            log,
            EventType.NO_ELIMINATION,
            Visibility.PUBLIC,
            tally=counted,
            reason="tie",
        )
        return None

    eliminated = leaders[0]
    state.alive.discard(eliminated)
    role = state.roles[eliminated] if state.config.rules.reveal_role_on_death else None
    _emit(
        state,
        log,
        EventType.ELIMINATION,
        Visibility.PUBLIC,
        target=eliminated,
        tally=counted,
        role=role,
    )
    return eliminated


def check_win(state: GameState) -> Winner | None:
    """Villagers win when no wolves live. Wolves win when they are at least half the living."""
    wolves = sum(1 for seat in state.alive if state.roles[seat] is Role.WOLF)
    others = len(state.alive) - wolves
    if wolves == 0:
        return "villagers"
    if wolves >= others:
        return "wolves"
    return None


def finish_if_over(state: GameState, log: EventLog) -> Winner | None:
    """Run after night resolution and after each vote. Logs game_end and returns the winner.

    Returns None if the game goes on. After the vote on day `max_days` with no winner, the
    game is a draw.
    """
    winner = check_win(state)
    if winner == "villagers":
        reason = "no_wolves"
    elif winner == "wolves":
        reason = "wolf_parity"
    elif state.phase is Phase.VOTE and state.day >= state.config.rules.max_days:
        winner, reason = "draw", "max_days"
    else:
        return None
    _emit(
        state,
        log,
        EventType.GAME_END,
        Visibility.PUBLIC,
        winner=winner,
        reason=reason,
        roles=dict(state.roles),
    )
    return winner
