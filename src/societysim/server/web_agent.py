"""WebHumanAgent: a visitor plays one seat over the Play API.

`act(turn)` publishes the turn as `pending` and waits up to `timeout` seconds for the
visitor's move, which the server validates and hands over with `submit()`. When the time
runs out it returns an error reply, so the orchestrator makes its usual invalid-output
fallback; the orchestrator's retry of that turn is answered at once with the same error
instead of a second wait. `max_timeouts` timeouts in a row raise Forfeit, which ends the
game. The visitor's move goes through the orchestrator's validator like any other reply.
"""

import asyncio
import time
from dataclasses import dataclass

from pydantic import BaseModel

from societysim.agents.base import Turn
from societysim.agents.human import MODEL
from societysim.models.client import CompletionResult, Usage

TIMED_OUT = "turn timed out"


class NotYourTurn(Exception):
    """A move arrived while the seat has no pending turn."""


class Forfeit(Exception):
    """The visitor let `max_timeouts` turns in a row time out."""


@dataclass(frozen=True)
class Pending:
    turn: Turn
    deadline: float  # unix seconds


def _timed_out() -> CompletionResult:
    return CompletionResult(parsed=None, raw="", error=TIMED_OUT, usage=Usage(model=MODEL))


class WebHumanAgent:
    def __init__(self, timeout: float = 90, max_timeouts: int = 3):
        self.timeout, self.max_timeouts = timeout, max_timeouts
        self.pending: Pending | None = None
        self.timeouts = 0  # in a row
        self._replies: asyncio.Queue[BaseModel] = asyncio.Queue()
        self._retry_after_timeout = False

    async def act(self, turn: Turn) -> CompletionResult:
        if self._retry_after_timeout:  # the orchestrator always retries a failed turn once
            self._retry_after_timeout = False
            return _timed_out()
        while not self._replies.empty():  # a move that raced the last timeout is stale
            self._replies.get_nowait()
        self.pending = Pending(turn, time.time() + self.timeout)
        try:
            parsed = await asyncio.wait_for(self._replies.get(), self.timeout)
        except TimeoutError:
            if not self._replies.empty():  # a move was accepted (200) as the clock ran out
                parsed = self._replies.get_nowait()
                self.timeouts = 0
                return CompletionResult(
                    parsed=parsed, raw=parsed.model_dump_json(), usage=Usage(model=MODEL)
                )
            self.timeouts += 1
            if self.timeouts >= self.max_timeouts:
                raise Forfeit(f"{self.timeouts} turns in a row timed out") from None
            self._retry_after_timeout = True
            return _timed_out()
        finally:
            self.pending = None
        self.timeouts = 0
        return CompletionResult(
            parsed=parsed, raw=parsed.model_dump_json(), usage=Usage(model=MODEL)
        )

    def submit(self, reply: BaseModel) -> None:
        """Hand over the visitor's validated reply to the pending turn."""
        if self.pending is None:
            raise NotYourTurn
        self.pending = None  # a second move for the same turn gets NotYourTurn
        self._replies.put_nowait(reply)
