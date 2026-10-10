"""Phase loop: drives agents through night and day, validates every reply, retries once,
then falls back deterministically.

The engine owns state changes and emits the flow events (phase_start, seer_check,
night_kill, doctor_save, death_reveal, elimination, no_elimination, game_end). This module
emits the rest: game_start, wolf_chat, wolf_proposal, statement and its self-tags, vote,
invalid_output and fallback_action. Prompts come only from `observe` + `phase_prompt`.
All randomness (fallbacks, the wolf tie-break) is drawn from `state.rng` in a fixed order,
so a seed replays the same game: during the parallel night calls only the wolf channel
draws (its own fallbacks, in turn order); Seer and Doctor fallbacks and the tie-break are
drawn after the calls finish. Vote fallbacks abstain and draw nothing.
"""

import asyncio
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from pydantic import BaseModel

from societysim.agents.base import AgentFactory, Turn
from societysim.agents.observation import observe
from societysim.agents.prompts import Action, phase_prompt, player
from societysim.agents.schemas import ABSTAIN
from societysim.config import GameConfig
from societysim.engine.engine import (
    _emit,  # stamps t/day/phase and drops None meta; shared so both sides log alike
    finish_if_over,
    resolve_night,
    seer_check,
    speaking_orders,
    start_phase,
    tally,
)
from societysim.engine.rules import legal_targets
from societysim.engine.state import GameState
from societysim.logging.events import Event, EventLogger, EventSink, EventType, Suspicion
from societysim.logging.events import Visibility as V
from societysim.models.cache import CachedUsage
from societysim.models.client import Usage
from societysim.roles import Phase, Role, Winner

SILENT = "(silent)"
DEFAULT_COLOR = "#888888"
# Where each action keeps its choice; statements have none.
_CHOICE = {
    "vote": "vote",
    "wolf_chat": "proposed_target",
    "lone_wolf": "target",
    "seer": "target",
    "doctor": "target",
}


def _add(total: Usage | None, usage: Usage) -> Usage:
    if total is None:
        return usage
    all_cached = (
        isinstance(total, CachedUsage)
        and total.cached
        and isinstance(usage, CachedUsage)
        and usage.cached
    )
    usage_type = CachedUsage if all_cached else Usage
    return usage_type(
        model=usage.model,
        input_tokens=total.input_tokens + usage.input_tokens,
        output_tokens=total.output_tokens + usage.output_tokens,
        cost_usd=total.cost_usd + usage.cost_usd,
        latency_ms=total.latency_ms + usage.latency_ms,
        attempts=total.attempts + usage.attempts,
    )


@dataclass
class Reply:
    """Outcome of one call: a validated response, or None after two rejected attempts."""

    turn: Turn
    response: Any | None
    usage: Usage  # summed over both attempts
    failures: list[tuple[str, str]] = field(default_factory=list)  # (raw, error) per rejection
    truncated: bool = False

    @property
    def fallback(self) -> bool | None:
        return True if self.response is None else None  # None keeps the meta key out

    def fields(self) -> dict[str, Any]:
        """Scratchpad and usage for the event that carries this reply. A fallback has none;
        its usage goes on fallback_action."""
        if self.response is None:
            return {}
        pr = self.response.private_reasoning
        return {
            "private_reasoning": pr.thoughts,
            "suspicion": [Suspicion(player=e.player, p=e.p) for e in pr.suspicion],
            "usage": self.usage,
        }


class Game:
    def __init__(
        self,
        config: GameConfig,
        make_agent: AgentFactory,
        logger: EventLogger,
        colors: Mapping[str, str | None] | None = None,
    ):
        self.state = GameState(config)
        self.agents = {seat: make_agent(self.state, seat) for seat in self.state.seats}
        self.logger = logger
        self.colors = colors or {}  # model name -> viewer color (None falls back)
        self.events: list[Event] = []
        self.winner: Winner | None = None

    # --- logging ---------------------------------------------------------------------------

    def log(self, **fields: Any) -> Event:
        """EventLog for the engine too, so every event lands in `events` for observe()."""
        event = self.logger.log(**fields)
        self.events.append(event)
        return event

    def emit(self, type_: EventType, visibility: V, **kw: Any) -> Event:
        return _emit(self.state, self, type_, visibility, **kw)

    # --- the loop --------------------------------------------------------------------------

    async def run(self) -> Winner:
        st = self.state
        seats = {
            s: {
                "name": st.names[s],
                "model": st.models.get(s, ""),
                "color": self.colors.get(st.models.get(s, "")) or DEFAULT_COLOR,
                "role": st.roles[s],
            }
            for s in st.seats
        }
        self.emit(EventType.GAME_START, V.NARRATOR, seed=st.config.seed, seats=seats)
        while True:  # finish_if_over ends it by day max_days at the latest
            if winner := await self.night():
                break
            if winner := await self.day():
                break
        self.winner = winner
        return winner

    async def night(self) -> Winner | None:
        st = self.state
        start_phase(st, self, Phase.NIGHT)
        seer, doctor = self._living(Role.SEER), self._living(Role.DOCTOR)
        proposals, seer_reply, doctor_reply = await asyncio.gather(
            self._wolf_channel(), self._night_call(seer, "seer"), self._night_call(doctor, "doctor")
        )
        # Seer and Doctor failures are logged after the parallel calls, in a fixed order.
        if seer_reply:
            target = self._settle(seer, seer_reply)
            fallback = seer_reply.response is None
            seer_check(st, self, seer, target, fallback=fallback, **seer_reply.fields())

        counts = Counter(proposals.values())
        top = max(counts.values())
        leaders = [s for s in st.seats if counts[s] == top]
        if len(counts) == 1:
            method = "consensus"
        elif len(leaders) == 1:
            method = "plurality"
        else:
            method = "tiebreak"
        kill = st.rng.choice(leaders) if method == "tiebreak" else leaders[0]

        save, extra = None, {}
        if doctor_reply:
            save = self._settle(doctor, doctor_reply)
            extra = {"fallback": doctor_reply.response is None, **doctor_reply.fields()}
        tally_ = {s: counts[s] for s in st.seats if counts[s]}
        resolve_night(
            st, self, kill, method=method, tally=tally_, save=save, doctor=doctor, **extra
        )
        return finish_if_over(st, self)

    async def day(self) -> Winner | None:
        st = self.state
        start_phase(st, self, Phase.DAY)
        for rnd, order in enumerate(speaking_orders(st), 1):
            for seat in order:  # sequential: each speaker sees the earlier statements
                await self._speak(seat, rnd)

        start_phase(st, self, Phase.VOTE)
        voters = [s for s in st.seats if s in st.alive]
        replies = await asyncio.gather(
            *(self._ask(s, "vote", legal_targets(st, s, Phase.VOTE)) for s in voters)
        )
        votes: dict[str, str | None] = {}
        for seat, reply in zip(voters, replies, strict=True):  # revealed together, seat order
            choice = self._settle(seat, reply)
            votes[seat] = choice
            target = None if choice in (None, ABSTAIN) else choice
            self.emit(
                EventType.VOTE,
                V.PUBLIC,
                actor=seat,
                target=target,
                fallback=reply.fallback,
                **reply.fields(),
            )
        tally(st, self, votes)
        return finish_if_over(st, self)

    # --- phases ----------------------------------------------------------------------------

    async def _wolf_channel(self) -> dict[str, str]:
        """Rounds of message + proposal, sequential within a round. Returns final proposals.
        A lone wolf gets one `lone_wolf` call (a target, no message) and logs only
        wolf_proposal."""
        st = self.state
        wolves = [s for s in st.seats if s in st.alive and st.roles[s] is Role.WOLF]
        lone = len(wolves) == 1
        rounds = 1 if lone else st.config.rules.wolf_chat_rounds
        action: Action = "lone_wolf" if lone else "wolf_chat"
        proposals: dict[str, str] = {}
        for rnd in range(1, rounds + 1):
            for wolf in wolves:
                reply = await self._ask(wolf, action, legal_targets(st, wolf, Phase.NIGHT))
                target = self._settle(wolf, reply)
                proposals[wolf] = target
                fields = {"round": rnd, "fallback": reply.fallback, **reply.fields()}
                if not lone:  # a lone wolf has no message; its proposal carries the reply
                    text = reply.response.action.message if reply.response else SILENT
                    self.emit(EventType.WOLF_CHAT, V.WOLVES, actor=wolf, public_text=text, **fields)
                    fields = {"round": rnd, "fallback": reply.fallback}
                self.emit(EventType.WOLF_PROPOSAL, V.WOLVES, actor=wolf, target=target, **fields)
        return proposals

    async def _night_call(self, seat: str | None, action: Action) -> Reply | None:
        if seat is None or not (targets := legal_targets(self.state, seat, Phase.NIGHT)):
            return None
        return await self._ask(seat, action, targets)

    async def _speak(self, seat: str, rnd: int) -> None:
        reply = await self._ask(seat, "statement", self._others(seat))
        self._settle(seat, reply)
        act = reply.response.action if reply.response else None
        statement = self.emit(
            EventType.STATEMENT,
            V.PUBLIC,
            actor=seat,
            public_text=act.text if act else SILENT,
            round=rnd,
            truncated=reply.truncated or None,
            fallback=reply.fallback,
            **reply.fields(),
        )
        if act is None:
            return
        tag = {"actor": seat, "statement_seq": statement.seq}
        for target in dict.fromkeys(act.accuse):
            self.emit(EventType.ACCUSATION, V.PUBLIC, target=target, **tag)
        if act.claim_role:
            self.emit(EventType.ROLE_CLAIM, V.PUBLIC, role=act.claim_role, **tag)
        if report := act.seer_report:
            self.emit(
                EventType.SEER_CLAIM,
                V.PUBLIC,
                target=report.target,
                result=report.result,
                night=report.night,
                **tag,
            )

    # --- one call: ask, validate, retry once ------------------------------------------------

    async def _ask(self, seat: str, action: Action, targets: list[str]) -> Reply:
        st = self.state
        others = self._others(seat)
        prompt = phase_prompt(
            action,
            observe(st, self.events, seat),
            {s: st.names[s] for s in others},
            {s: st.names[s] for s in targets if s != ABSTAIN},
            st.config.rules,
        )
        turn = Turn(action, prompt, others, targets)
        usage: Usage | None = None
        failures: list[tuple[str, str]] = []
        for _ in range(2):
            result = await self.agents[seat].act(turn)
            usage = _add(usage, result.usage)
            if result.parsed is None:
                error = result.error or "reply could not be parsed"
            else:
                error = self._invalid(turn, result.parsed)
            if error is None:
                response, truncated = self._truncate(result.parsed)
                return Reply(turn, response, usage, failures, truncated)
            failures.append((result.raw, error))
            turn = replace(
                turn,
                prompt=f"{prompt}\n\nYour previous reply was rejected: {error}\n"
                "Reply again with a corrected JSON object.",
            )
        return Reply(turn, None, usage, failures)

    def _invalid(self, turn: Turn, parsed: Any) -> str | None:
        """Why a parsed reply breaks the rules, or None if it is valid."""
        st = self.state

        def names(seats: Sequence[str]) -> str:
            return ", ".join(player(s, st.names[s]) if s in st.names else repr(s) for s in seats)

        errors = []
        rated = [e.player for e in parsed.private_reasoning.suspicion]
        if missing := [s for s in turn.others if s not in rated]:
            errors.append(f"suspicion is missing {names(missing)}")
        if extra := [s for s in dict.fromkeys(rated) if s not in turn.others]:
            errors.append(f"suspicion must not include {names(extra)} (only other living players)")
        if dupes := [s for s in dict.fromkeys(rated) if rated.count(s) > 1]:
            errors.append(f"suspicion lists {names(dupes)} more than once")

        act = parsed.action
        if turn.action == "statement":
            if bad := [s for s in act.accuse if s not in turn.targets]:
                errors.append(f"accuse can only name {names(turn.targets)}, not {names(bad)}")
            report = act.seer_report
            if report and (report.target not in st.seats or not 1 <= report.night <= st.day):
                errors.append(
                    f"seer_report needs a player id as target and a night from 1 to {st.day}"
                )
        elif (choice := getattr(act, _CHOICE[turn.action])) not in turn.targets:
            errors.append(f"{choice!r} is not allowed; choose one of {names(turn.targets)}")
        return "; ".join(errors) or None

    def _truncate(self, response: BaseModel) -> tuple[BaseModel, bool]:
        """Cut statement text to the word cap; longer text is truncated, not rejected."""
        cap = self.state.config.rules.max_statement_words
        text = getattr(response.action, "text", None)  # only statements have text
        if text is None or len(words := text.split()) <= cap:
            return response, False
        action = response.action.model_copy(update={"text": " ".join(words[:cap])})
        return response.model_copy(update={"action": action}), True

    def _settle(self, seat: str, reply: Reply) -> str | None:
        """Log rejected attempts and, after two, the fallback. Returns the call's choice:
        the reply's target, or a random legal target at night, abstain for a vote, None
        for a statement."""
        action = reply.turn.action
        for attempt, (raw, error) in enumerate(reply.failures, 1):
            self.emit(
                EventType.INVALID_OUTPUT,
                V.NARRATOR,
                actor=seat,
                action=action,
                attempt=attempt,
                error=error,
                raw=raw,
            )
        if reply.response is not None:
            return getattr(reply.response.action, _CHOICE[action]) if action in _CHOICE else None
        choice = None
        if action in ("wolf_chat", "lone_wolf", "seer", "doctor") and reply.turn.targets:
            choice = self.state.rng.choice(reply.turn.targets)
        self.emit(
            EventType.FALLBACK_ACTION,
            V.NARRATOR,
            actor=seat,
            target=choice,
            action=action,
            reason="invalid_output",
            usage=reply.usage,
        )
        return ABSTAIN if action == "vote" else choice

    def _others(self, seat: str) -> list[str]:
        return [s for s in self.state.seats if s in self.state.alive and s != seat]

    def _living(self, role: Role) -> str | None:
        st = self.state
        return next((s for s in st.seats if s in st.alive and st.roles[s] is role), None)


async def run_game(
    config: GameConfig,
    make_agent: AgentFactory,
    sinks: Sequence[EventSink],
    game_id: str,
    colors: Mapping[str, str | None] | None = None,
) -> Game:
    """Play one game to the end. Pass fresh sinks per game; returns the finished Game."""
    logger = EventLogger(game_id, sinks)
    try:
        game = Game(config, make_agent, logger, colors)
        await logger.start_game(config.model_dump(mode="json"), config.seed, config.seat_models())
        await logger.end_game(await game.run())
    finally:
        await logger.close()
    return game
