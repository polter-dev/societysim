"""LLMAgent: answers a turn with one ModelClient call."""

from collections.abc import Mapping

from societysim.agents.base import AgentFactory, Turn
from societysim.agents.prompts import RESPONSE_MODELS, build_messages, system_prompt
from societysim.config import ModelSpec
from societysim.engine.state import GameState
from societysim.models.client import CompletionResult, ModelClient


class LLMAgent:
    def __init__(self, client: ModelClient, spec: ModelSpec, system: str):
        self.client = client
        self.spec = spec
        self.system = system  # static per seat, so provider prompt caching applies

    async def act(self, turn: Turn) -> CompletionResult:
        model = RESPONSE_MODELS[turn.action]
        messages = build_messages(self.system, turn.prompt, self.spec, model)
        return await self.client.complete(self.spec, messages, model)


def llm_agents(client: ModelClient, specs: Mapping[str, ModelSpec]) -> AgentFactory:
    """Factory for `run_game`: each seat gets an LLMAgent for its model in `state.models`."""

    def make(state: GameState, seat: str) -> LLMAgent:
        cfg = state.config
        system = system_prompt(seat, state.names[seat], state.roles[seat], cfg.rules, cfg.roles)
        return LLMAgent(client, specs[state.models[seat]], system)

    return make
