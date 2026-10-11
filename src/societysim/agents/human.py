"""HumanAgent: a person plays one seat from the terminal.

It sees only what an LLM in that seat would see: the rules and its role from the system
prompt once, then each turn's observation from agents/observation.py, with the lines about
the JSON reply format left out. It answers plain questions instead: numbered choices, free
text for speech. Its replies use the same response models and go through the orchestrator's
validator; a rejected reply comes back as a retry turn with the error, like for any agent.

Input runs in a daemon thread, so AI seats in parallel calls keep running while the human
thinks. There is no timeout on the human seat.

Suspicion is optional for a human, and a score the human didn't give is never invented. The
orchestrator needs a full suspicion list, so `reasoning()` fills the gaps with a placeholder
and names the rated seats in `thoughts`; `HumanSink` then drops the placeholders before the
event is logged. A reply that rates nobody is logged with `suspicion: null`.
"""

import asyncio
import json
import threading
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from pydantic import ValidationError

from societysim.agents.base import Agent, AgentFactory, Turn
from societysim.agents.prompts import RESPONSE_MODELS, system_prompt
from societysim.agents.schemas import ABSTAIN, StatementAction, TargetResponse
from societysim.engine.state import GameState
from societysim.logging.events import Event, EventSink
from societysim.models.client import CompletionResult, Usage
from societysim.roles import Role

MODEL = "human"  # model name of the human seat in the log
THOUGHTS = "human player"  # the human seat's logged private_reasoning
# The three answers a human can give per player, as P(wolf). Shared with the web panel.
LEVELS = {"suspicious": 0.8, "unsure": 0.5, "trust": 0.2}
_RATED = f"{THOUGHTS}; rated:"  # thoughts marker, read by HumanSink and never logged
_PLACEHOLDER = 0.5  # stands in for an unrated player until HumanSink drops it
# Lines of the model prompt that only explain the JSON reply format.
_FORMAT_LINES = ('"', '{"', '- "', "Reply with the JSON", "Reply again with a corrected JSON")
_LABELS = {"statement": "speak", "lone_wolf": "kill", "seer": "check", "doctor": "protect"}


class Retry(ValueError):
    """Bad input; the message is shown and the question asked again."""


def plain(text: str) -> str:
    """A model prompt or observation without the lines about the JSON reply format."""
    return "\n".join(x for x in text.splitlines() if not x.lstrip().startswith(_FORMAT_LINES))


def reasoning(rated: Mapping[str, float], others: Sequence[str]) -> dict[str, Any]:
    """private_reasoning for a human reply: the orchestrator's full suspicion list, with a
    placeholder for every player the human didn't rate. HumanSink keeps only the rated ones."""
    return {
        "thoughts": f"{_RATED} {' '.join(s for s in others if s in rated) or '-'}",
        "suspicion": [{"player": s, "p": rated.get(s, _PLACEHOLDER)} for s in others],
    }


class HumanSink:
    """Sink wrapper: the human seat's events keep only the suspicion the human gave (null if
    none) and say THOUGHTS as private_reasoning. Only the sinks see the cleaned event; the
    game's in-memory `events` keep the placeholders, so read suspicion from the log."""

    def __init__(self, sink: EventSink, seat: str):
        self.sink, self.seat = sink, seat

    async def write(self, event: Event) -> None:
        thoughts = event.private_reasoning or ""
        if event.actor == self.seat and thoughts.startswith(_RATED):
            rated = set(thoughts[len(_RATED) :].split())
            kept = [s for s in event.suspicion or [] if s.player in rated]
            update = {"private_reasoning": THOUGHTS, "suspicion": kept or None}
            event = event.model_copy(update=update)
        await self.sink.write(event)

    def __getattr__(self, name: str) -> Any:  # start_game, end_game, close
        return getattr(self.sink, name)


class HumanAgent:
    def __init__(
        self,
        state: GameState,
        seat: str,
        read: Callable[[str], str] | None = None,  # default: input
        write: Callable[[str], Any] | None = None,  # default: print
    ):
        cfg, role = state.config, state.roles[seat]
        system = system_prompt(seat, state.names[seat], role, cfg.rules, cfg.roles)
        # The rules and the role; "How to reply" is about JSON.
        self.system = system.split("\nHow to reply:")[0].rstrip()
        self.names = dict(state.names)
        self.seat = seat
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
            self.write(f"{self.system}\n\nAnswer the questions below; choices are numbered.\n")
            self.system = ""  # shown once, like a cached system prompt
        label = _LABELS.get(turn.action, turn.action.replace("_", " "))
        self.write(f"\n=== your turn: {label} ===\n{plain(turn.prompt)}\n")
        action = self._ask(lambda: self._action(turn))
        rated = self._ask(lambda: self._suspicion(turn.others))
        reply = dict(private_reasoning=reasoning(rated, turn.others), action=action)
        parsed = RESPONSE_MODELS.get(turn.action, TargetResponse).model_validate(reply)
        raw = json.dumps(dict(action=action, suspicion=rated))  # as given, without placeholders
        return CompletionResult(parsed=parsed, raw=raw, usage=Usage(model=MODEL))

    def _ask[T](self, question: Callable[[], T]) -> T:
        while True:
            try:
                return question()
            except (Retry, ValidationError) as e:
                self.write(f"  invalid: {e}")

    def _label(self, option: str) -> str:
        if option == ABSTAIN:
            return "abstain (vote for nobody)"
        return f"{self.names[option]} ({option})" if option in self.names else option

    def _menu(self, options: Sequence[str]) -> None:
        self.write("\n".join(f"  {i}. {self._label(o)}" for i, o in enumerate(options, 1)))

    def _pick(self, question: str, options: list[str]) -> str:
        self._menu(options)
        return choose(self.read(f"{question} [1-{len(options)}]: "), options)

    def _action(self, turn: Turn) -> dict:
        match turn.action:
            case "statement":
                return self._statement(turn)
            case "vote":
                return dict(vote=self._pick("Your vote", turn.targets))
            case "wolf_chat":
                message = _text(self.read("Message to the other wolves: "))
                return dict(message=message, proposed_target=self._pick("Kill", turn.targets))
            case _:  # seer, doctor, lone_wolf
                return dict(target=self._pick("Who", turn.targets))

    def _statement(self, turn: Turn) -> dict:
        text = _text(self.read("What do you say? "))
        self._menu(turn.targets)
        line = self.read("Accuse anyone? Numbers separated by spaces, blank for none: ")
        accuse = list(dict.fromkeys(choose(a, turn.targets) for a in line.split()))
        roles = [r.value for r in Role]
        self._menu(roles)
        answer = self.read(f"Claim a role? [1-{len(roles)}], blank for none: ").strip()
        claim = choose(answer, roles) if answer else None
        report = None
        if claim == Role.SEER:
            seats = [s for s in self.names if s != self.seat]
            self._menu(seats)
            who = self.read(f"Report a check? Who [1-{len(seats)}], blank for none: ").strip()
            if who:
                target = choose(who, seats)
                answer = self.read("Result: 1. wolf  2. not a wolf [1-2]: ")
                result = choose(answer, ["wolf", "not_wolf"])
                night = self.read("Which night was the check? Night number: ").strip()
                if not night.isdigit():
                    raise Retry("type the night as a number, like 1")
                report = dict(target=target, result=result, night=int(night))
        action = dict(text=text, accuse=accuse, claim_role=claim, seer_report=report)
        StatementAction.model_validate(action)
        return action

    def _suspicion(self, others: Sequence[str]) -> dict[str, float]:
        """The players the human rated, as P(wolf); unrated players are left out."""
        answer = self.read("Rate how suspicious the others are? y/N, blank to skip: ")
        if answer.strip().lower() not in ("y", "yes"):
            return {}
        levels, rated = list(LEVELS), {}
        menu = "  ".join(f"{i}. {lv}" for i, lv in enumerate(levels, 1))
        for s in others:
            answer = self.read(f"{self._label(s)}: {menu}, blank to skip: ").strip()
            if answer:
                rated[s] = LEVELS[choose(answer, levels)]
        return rated


def choose(answer: str, options: Sequence[str]) -> str:
    """A numbered choice: the option's number (1-based) or the option itself."""
    answer = answer.strip()
    if answer.isdigit() and 1 <= int(answer) <= len(options):
        return options[int(answer) - 1]
    if answer in options:
        return answer
    raise Retry(f"type a number from 1 to {len(options)}")


def _text(text: str) -> str:
    if not (text := text.strip()):
        raise Retry("say something")
    return text


def with_human(make_agent: AgentFactory, seat: str) -> AgentFactory:
    """Factory that seats a HumanAgent at `seat` and `make_agent`'s agents everywhere else."""

    def make(state: GameState, s: str) -> Agent:
        return HumanAgent(state, s) if s == seat else make_agent(state, s)

    return make
