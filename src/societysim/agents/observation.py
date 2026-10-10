"""The only path from game state to an agent prompt.

`observe(state, events, seat)` renders what one living player knows, as one string that
`prompts.phase_prompt` inserts verbatim: the current phase, role-private knowledge, the dead
with their revealed roles, a deterministic summary of earlier days and today's full history.

Two guards keep private information out:
- every event passes through `visible()` before anything reads it;
- rendering is a whitelist of public fields. `private_reasoning`, `suspicion` and narrator
  meta are never read, and unknown event types render nothing.
From `state` only the viewer's own role, names, `alive`, the clock, the wolf partners (for a
wolf) and the viewer's own Seer results are read. Revealed roles come from death events.
"""

from collections.abc import Callable, Iterable, Sequence

from societysim.agents.prompts import player
from societysim.engine.state import GameState
from societysim.logging.events import Event, EventType, Visibility
from societysim.roles import Phase, Role

# ponytail: ~3k tokens at 4 chars per token; count with a tokenizer if a model overflows.
MAX_CHARS = 12_000
LAST_WORDS = 25  # earlier days keep each player's last statement, cut to this many words
OMITTED = "(Older history left out for length.)"

type Name = Callable[[str], str]


def visible(event: Event, seat: str, role: Role) -> bool:
    """Could `seat`, holding `role`, see this event during the game?"""
    match event.visibility:
        case Visibility.PUBLIC:
            # game_end carries every role; nobody is observed after it anyway.
            return event.type is not EventType.GAME_END
        case Visibility.WOLVES:
            return role is Role.WOLF
        case Visibility.SEER:
            return role is Role.SEER and event.actor == seat
        case Visibility.DOCTOR:
            return role is Role.DOCTOR and event.actor == seat
        case _:  # narrator: nobody in-game
            return False


def observe(state: GameState, events: Sequence[Event], seat: str) -> str:
    """Render `seat`'s observation from the state and the full event log so far.

    Raises ValueError for a dead seat: dead players get no observation.
    """
    if seat not in state.alive:
        raise ValueError(f"{seat} is dead; dead players get no observation")
    role = state.roles[seat]
    seen = [e for e in events if visible(e, seat, role)]

    def name(s: str) -> str:
        return player(s, state.names[s])

    head = [f"Now: {_now(state)}.", *_knowledge(state, seat, role, seen, name), *_dead(seen, name)]
    days = [_summary(d, [e for e in seen if e.day == d], name) for d in range(1, state.day)]
    today = [line for e in seen if e.day == state.day for line in _line(e, name)]
    return _fit("\n".join(head), days, today)


def _now(state: GameState) -> str:
    if state.phase is Phase.NIGHT:
        return f"Night {state.day}"
    if state.phase is Phase.VOTE:
        return f"Day {state.day}, vote"
    return f"Day {state.day}, discussion"


def _knowledge(state: GameState, seat: str, role: Role, seen: list[Event], name: Name) -> list[str]:
    if role is Role.WOLF:
        partners = [s for s in state.seats if s != seat and state.roles[s] is Role.WOLF]
        if not partners:
            return ["You are the only wolf."]
        shown = [name(s) + ("" if s in state.alive else ", dead") for s in partners]
        return ["Your fellow wolves: " + "; ".join(shown) + "."]
    if role is Role.SEER:
        checks = [
            f"night {night}: {name(target)} is {'a wolf' if result == 'wolf' else 'not a wolf'}"
            for night, target, result in state.seer_results.get(seat, [])
        ]
        return ["Your Seer checks: " + ("; ".join(checks) if checks else "none yet") + "."]
    if role is Role.DOCTOR:
        protects = [
            f"night {e.day}: {name(e.target)}"
            for e in seen
            if e.type is EventType.DOCTOR_SAVE and e.target
        ]
        return ["Your protects: " + ("; ".join(protects) if protects else "none yet") + "."]
    return []


def _revealed(event: Event) -> str:
    return event.meta.role.value if event.meta.role else "role not revealed"


def _dead(seen: list[Event], name: Name) -> list[str]:
    dead = [
        f"{name(e.target)}, {_revealed(e)}"
        for e in seen
        if e.type in (EventType.DEATH_REVEAL, EventType.ELIMINATION) and e.target
    ]
    return ["Dead: " + "; ".join(dead) + "."] if dead else []


def _death(e: Event, name: Name) -> str:
    if e.target is None:
        return "Nobody died in the night."
    return f"{name(e.target)} was killed in the night ({_revealed(e)})."


def _vote_result(e: Event, name: Name) -> str:
    if e.type is EventType.ELIMINATION:
        return f"{name(e.target)} was voted out ({_revealed(e)})."
    return f"Nobody was voted out ({e.meta.reason or 'no votes'})."


def _vote(e: Event, name: Name) -> str:
    return name(e.target) if e.target else "abstain"


def _claim(e: Event, name: Name, tense: str) -> str:
    """A role or Seer claim; `tense` is "s" (today) or "ed" (earlier days)."""
    if e.type is EventType.ROLE_CLAIM:
        return f"claim{tense} to be {e.meta.role.value if e.meta.role else 'nothing'}"
    result = "a wolf" if e.meta.result == "wolf" else "not a wolf"
    return f"report{tense} {name(e.target)} as {result} (night {e.meta.night})"


def _line(e: Event, name: Name) -> Iterable[str]:
    """Today's history: one line per event, public fields only."""
    match e.type:
        case EventType.PHASE_START:
            yield f"[{e.public_text}]"
        case EventType.WOLF_CHAT:
            yield f"(wolf channel) {name(e.actor)}: {e.public_text}"
        case EventType.WOLF_PROPOSAL:
            yield f"(wolf channel) {name(e.actor)} proposes killing {name(e.target)}."
        case EventType.NIGHT_KILL:
            yield f"(wolf channel) The pack attacks {name(e.target)}."
        case EventType.DEATH_REVEAL:
            yield _death(e, name)
        case EventType.STATEMENT:
            yield f'{name(e.actor)}: "{e.public_text}"'
        case EventType.ACCUSATION:
            yield f"  {name(e.actor)} accuses {name(e.target)}."
        case EventType.ROLE_CLAIM | EventType.SEER_CLAIM:
            yield f"  {name(e.actor)} {_claim(e, name, 's')}."
        case EventType.VOTE:
            yield f"{name(e.actor)} votes: {_vote(e, name)}."
        case EventType.ELIMINATION | EventType.NO_ELIMINATION:
            yield _vote_result(e, name)
        # Seer checks and Doctor protects are in the knowledge block; the rest is never shown.


def _summary(day: int, evs: list[Event], name: Name) -> str:
    """Deterministic summary of one earlier day (night, discussion and vote)."""
    lines = [f"Day {day} summary:"]
    lines += [f"- {_death(e, name)}" for e in evs if e.type is EventType.DEATH_REVEAL]
    lines += [
        f"- (wolf channel) The pack attacked {name(e.target)}."
        for e in evs
        if e.type is EventType.NIGHT_KILL
    ]

    said: dict[str, list[str]] = {}
    last: dict[str, str] = {}
    for e in evs:
        if e.type is EventType.STATEMENT and e.actor:
            last[e.actor] = e.public_text or ""
            said.setdefault(e.actor, [])
        elif e.type is EventType.ACCUSATION and e.actor:
            said.setdefault(e.actor, []).append(f"accused {name(e.target)}")
        elif e.type in (EventType.ROLE_CLAIM, EventType.SEER_CLAIM) and e.actor:
            said.setdefault(e.actor, []).append(_claim(e, name, "ed"))
    for actor in sorted(said):
        words = last.get(actor, "").split()
        quote = " ".join(words[:LAST_WORDS]) + ("..." if len(words) > LAST_WORDS else "")
        parts = [*dict.fromkeys(said[actor]), f'last said: "{quote}"']  # dedupe, keep order
        lines.append(f"- {name(actor)}: " + "; ".join(parts))

    votes = [f"{name(e.actor)} -> {_vote(e, name)}" for e in evs if e.type is EventType.VOTE]
    if votes:
        lines.append("- Votes: " + ", ".join(votes) + ".")
    lines += [
        f"- {_vote_result(e, name)}"
        for e in evs
        if e.type in (EventType.ELIMINATION, EventType.NO_ELIMINATION)
    ]
    return "\n".join(lines)


def _fit(head: str, days: list[str], today: list[str]) -> str:
    """Join the sections; over budget, drop the oldest days first, then the oldest of today."""
    cut = False
    while True:
        parts = [head, *([OMITTED] if cut else []), *days]
        if today:
            parts.append("Today so far:\n" + "\n".join(today))
        text = "\n\n".join(parts)
        if len(text) <= MAX_CHARS:
            return text
        if days:
            days = days[1:]
        elif len(today) > 1:
            today = today[1:]
        else:
            return text[:MAX_CHARS]
        cut = True
