"""HumanAgent: a person plays one seat from the terminal.

It sees only what an LLM in that seat would see: the same system prompt once, then each
turn's prompt (the observation from agents/observation.py plus the task), and nothing else.
Its replies use the same response models and go through the orchestrator's validator; a
rejected reply comes back as a retry turn with the error, like for any agent.

Input runs in a daemon thread, so AI seats in parallel calls keep running while the human
thinks. There is no timeout on the human seat.

Suspicion the human leaves out defaults to 0.5. Such replies carry DEFAULTED as their
thoughts, and `MarkDefaults` adds `meta.suspicion_default: true` to those events.
"""

import asyncio
import json
import threading
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from societysim.agents.base import Agent, AgentFactory, Turn
from societysim.agents.prompts import RESPONSE_MODELS, system_prompt
from societysim.agents.schemas import StatementAction, TargetResponse
from societysim.engine.state import GameState
from societysim.logging.events import Event, EventSink
from societysim.models.client import CompletionResult, Usage
from societysim.roles import Role

MODEL = "human"  # model name of the human seat in the log
NEUTRAL = 0.5
THOUGHTS = "human player"
DEFAULTED = "human player; suspicion defaulted to 0.5"
_JSON_LINE = "\nReply with the JSON object only."  # the human answers questions instead


class Retry(ValueError):
    """Bad input; the message is shown and the question asked again."""


class HumanAgent:
    def __init__(
        self,
        state: GameState,
        seat: str,
        read: Callable[[str], str] | None = None,  # default: input
        write: Callable[[str], Any] | None = None,  # default: print
    ):
        cfg, role = state.config, state.roles[seat]
        self.system = system_prompt(seat, state.names[seat], role, cfg.rules, cfg.roles)
        self.read, self.write = read or input, write or print

    async def act(self, turn: Turn) -> CompletionResult:
        # A daemon thread, not asyncio.to_thread: on Ctrl-C, asyncio.run would wait for a
        # pool thread still blocked in input().
        loop = asyncio.get_running_loop()
        future = loop.create_future()

        def settle(fn: Callable[[Any], None], value: Any) -> None:
            if not future.done():
                fn(value)

        def run() -> None:
            try:
                result = self._act(turn)
            except BaseException as e:  # EOF on stdin ends the game
                loop.call_soon_threadsafe(settle, future.set_exception, e)
            else:
                loop.call_soon_threadsafe(settle, future.set_result, result)

        threading.Thread(target=run, daemon=True).start()
        return await future

    def _act(self, turn: Turn) -> CompletionResult:
        if self.system:
            self.write(f"{self.system}\n")
            self.system = ""  # shown once, like a cached system prompt
        self.write(f"\n=== your turn: {turn.action} ===\n{turn.prompt.replace(_JSON_LINE, '')}\n")
        action = self._ask(lambda: _action(turn, self.read))
        suspicion, defaulted = self._ask(lambda: _suspicion(turn.others, self.read))
        reasoning = dict(thoughts=DEFAULTED if defaulted else THOUGHTS, suspicion=suspicion)
        reply = dict(private_reasoning=reasoning, action=action)
        parsed = RESPONSE_MODELS.get(turn.action, TargetResponse).model_validate(reply)
        return CompletionResult(parsed=parsed, raw=json.dumps(reply), usage=Usage(model=MODEL))

    def _ask[T](self, question: Callable[[], T]) -> T:
        while True:
            try:
                return question()
            except (Retry, ValidationError) as e:
                self.write(f"  invalid: {e}")


def _pick(read: Callable[[str], str], label: str, options: list[str]) -> str:
    choice = read(f"{label} ({' '.join(options)}): ").strip()
    if choice not in options:
        raise Retry(f"choose one of {', '.join(options)}")
    return choice


def _text(read: Callable[[str], str], label: str) -> str:
    if not (text := read(f"{label}: ").strip()):
        raise Retry("say something")
    return text


def _action(turn: Turn, read: Callable[[str], str]) -> dict:
    match turn.action:
        case "statement":
            return _statement(turn, read)
        case "vote":
            return dict(vote=_pick(read, "vote", turn.targets))
        case "wolf_chat":
            message = _text(read, "message to the wolves")
            return dict(message=message, proposed_target=_pick(read, "kill", turn.targets))
        case _:  # seer, doctor, lone_wolf
            return dict(target=_pick(read, "target", turn.targets))


def _statement(turn: Turn, read: Callable[[str], str]) -> dict:
    text = _text(read, "statement")
    accuse = read(f"accuse, blank for none ({' '.join(turn.targets)}): ").split()
    if bad := [s for s in accuse if s not in turn.targets]:
        raise Retry(f"cannot accuse {', '.join(bad)}")
    roles = [r.value for r in Role]
    claim = read(f"claim a role, blank for none ({' '.join(roles)}): ").strip() or None
    if claim is not None and claim not in roles:
        raise Retry(f"role must be one of {', '.join(roles)}")
    report = None
    if claim == Role.SEER:
        line = read("seer report as '<id> wolf|not_wolf <night>', blank for none: ").split()
        if line:
            if len(line) != 3 or not line[2].isdigit():
                raise Retry("write it like 'p3 wolf 1'")
            report = dict(target=line[0], result=line[1], night=int(line[2]))
    accuse = list(dict.fromkeys(accuse))
    action = dict(text=text, accuse=accuse, claim_role=claim, seer_report=report)
    StatementAction.model_validate(action)  # a bad seer result asks again
    return action


def _suspicion(others: list[str], read: Callable[[str], str]) -> tuple[list[dict], bool]:
    """Returns the suspicion list and whether any entry was defaulted to NEUTRAL."""
    line = read(f"suspicion P(wolf) as id=p, unlisted get {NEUTRAL} ({' '.join(others)}): ")
    given: dict[str, float] = {}
    for pair in line.split():
        seat, _, p = pair.partition("=")
        if seat not in others:
            raise Retry(f"{seat!r} is not one of {', '.join(others)}")
        try:
            given[seat] = float(p)
        except ValueError:
            raise Retry(f"{pair!r}: write it like p3=0.8") from None
        if not 0 <= given[seat] <= 1:
            raise Retry(f"{pair!r}: p must be between 0 and 1")
    suspicion = [dict(player=s, p=given.get(s, NEUTRAL)) for s in others]
    return suspicion, len(given) < len(others)


def with_human(make_agent: AgentFactory, seat: str) -> AgentFactory:
    """Factory that seats a HumanAgent at `seat` and `make_agent`'s agents everywhere else."""

    def make(state: GameState, s: str) -> Agent:
        return HumanAgent(state, s) if s == seat else make_agent(state, s)

    return make


class MarkDefaults:
    """Sink wrapper: adds meta.suspicion_default to the human seat's defaulted replies."""

    def __init__(self, sink: EventSink, seat: str):
        self.sink, self.seat = sink, seat

    async def write(self, event: Event) -> None:
        if event.actor == self.seat and event.private_reasoning == DEFAULTED:
            meta = event.meta.model_copy(update={"suspicion_default": True})
            event = event.model_copy(update={"meta": meta})
        await self.sink.write(event)

    def __getattr__(self, name: str) -> Any:  # start_game, end_game, close
        return getattr(self.sink, name)
