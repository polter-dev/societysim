"""The model client contract. Every agent call goes through `ModelClient.complete`.

The LiteLLM implementation is T2.1; tests use `tests/fakes.py::FakeClient`.
"""

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict

from societysim.config import ModelSpec


class Usage(BaseModel):
    """Logged as `meta.usage` on every LLM-backed event. Covers all attempts of one call."""

    model_config = ConfigDict(extra="forbid")

    model: str  # ModelSpec.name
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0  # estimate; does not assume prompt caching
    latency_ms: int = 0
    attempts: int = 1


class CompletionResult(BaseModel):
    parsed: BaseModel | None  # instance of response_model, or None if parsing failed
    raw: str  # raw model text, kept for invalid_output events
    error: str | None = None  # parse or provider error; set whenever parsed is None
    usage: Usage


class ModelClient(Protocol):
    async def complete(
        self,
        model: ModelSpec,
        messages: list[dict[str, Any]],  # OpenAI-style [{"role": ..., "content": ...}]
        response_model: type[BaseModel],
    ) -> CompletionResult:
        """Return a result; never raise for a bad reply (set `error` instead)."""
        ...
