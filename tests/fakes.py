"""FakeClient: a ModelClient for tests. No network.

Scripted: pass `script`, a list consumed one item per call. An item is a response model
instance, a dict (validated against the requested response_model), or a raw string
(parsed as JSON; a bad string gives parsed=None and an error, like a real bad reply).
Random: once the script is empty (or with no script), it returns a seeded random reply
that targets and rates only `players`. Pass the legal players for the call under test.
"""

import json
import random
from collections.abc import Iterable, Sequence
from typing import Any

from pydantic import BaseModel, ValidationError

from societysim.agents.schemas import (
    ABSTAIN,
    StatementResponse,
    TargetResponse,
    VoteResponse,
    WolfChatResponse,
)
from societysim.config import ModelSpec
from societysim.models.client import CompletionResult, Usage


class FakeClient:
    def __init__(
        self,
        script: Iterable[BaseModel | dict | str] = (),
        players: Sequence[str] = ("p1", "p2", "p3"),
        seed: int = 0,
    ) -> None:
        self.script = list(script)
        self.players = list(players)
        self.rng = random.Random(seed)
        self.calls: list[tuple[ModelSpec, list[dict[str, Any]], type[BaseModel]]] = []

    async def complete(
        self,
        model: ModelSpec,
        messages: list[dict[str, Any]],
        response_model: type[BaseModel],
    ) -> CompletionResult:
        self.calls.append((model, messages, response_model))
        item = self.script.pop(0) if self.script else self._random(response_model)
        if isinstance(item, BaseModel):
            item = item.model_dump(mode="json")
        raw = item if isinstance(item, str) else json.dumps(item)
        usage = Usage(model=model.name, input_tokens=100, output_tokens=20)
        try:
            return CompletionResult(
                parsed=response_model.model_validate_json(raw), raw=raw, usage=usage
            )
        except ValidationError as e:
            return CompletionResult(parsed=None, raw=raw, error=str(e), usage=usage)

    def _random(self, response_model: type[BaseModel]) -> dict[str, Any]:
        pick = self.rng.choice
        reasoning = {
            "thoughts": "fake",
            "suspicion": [{"player": p, "p": round(self.rng.random(), 2)} for p in self.players],
        }
        if response_model is StatementResponse:
            action = {
                "text": "I have nothing to add.",
                "accuse": [pick(self.players)],
                "claim_role": None,
                "seer_report": None,
            }
        elif response_model is VoteResponse:
            action = {"vote": pick([*self.players, ABSTAIN])}
        elif response_model is WolfChatResponse:
            action = {"message": "Let's go.", "proposed_target": pick(self.players)}
        elif response_model is TargetResponse:
            action = {"target": pick(self.players)}
        else:
            raise TypeError(f"FakeClient has no random reply for {response_model.__name__}")
        return {"private_reasoning": reasoning, "action": action}
