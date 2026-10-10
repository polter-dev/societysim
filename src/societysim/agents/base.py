"""The agent interface the orchestrator drives.

An agent answers one `Turn` with a `CompletionResult`. It does not validate, retry or fall
back; the orchestrator does all of that. LLMAgent (llm_agent.py) and RandomAgent
(scripted.py) implement it.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from societysim.agents.prompts import Action
from societysim.engine.state import GameState
from societysim.models.client import CompletionResult


@dataclass(frozen=True)
class Turn:
    action: Action
    prompt: str  # phase_prompt(...) output; on the retry, with the error appended
    others: list[str]  # other living seats: the suspicion list must cover exactly these
    targets: list[str]  # legal choices in seat order; votes may end with "abstain"


class Agent(Protocol):
    async def act(self, turn: Turn) -> CompletionResult:
        """Reply to one turn. Must not raise for a bad reply; set `error` instead."""
        ...


# Called once per seat after roles are dealt, so an agent can know its seat and role.
type AgentFactory = Callable[[GameState, str], Agent]
