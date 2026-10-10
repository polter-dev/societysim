"""The model client contract. Every agent call goes through `ModelClient.complete`.

`LiteLLMClient` below is the real one; tests use `tests/fakes.py::FakeClient`.
"""

import asyncio
import os
import random
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

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
    # Served from the cassette cache (free). Added in schema_version 2, so optional in the
    # JSON schema: v1 lines have no such key.
    cached: bool = Field(False, json_schema_extra={"x-optional": True})


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


# --- LiteLLM implementation (T2.1) -------------------------------------------------------

RETRY_STATUS = {408, 429, 500, 502, 503, 504, 529}

Sleep = Callable[[float], Awaitable[Any]]
Clock = Callable[[], float]


@dataclass(frozen=True)
class ProviderLimit:
    """Per-provider throttle: at most `concurrency` calls in flight and `rpm` per minute."""

    concurrency: int
    rpm: int


DEFAULT_LIMITS: dict[str, ProviderLimit] = {
    "gemini": ProviderLimit(concurrency=4, rpm=60),
    "huggingface": ProviderLimit(concurrency=8, rpm=300),
    "openai": ProviderLimit(concurrency=8, rpm=500),
}
FALLBACK_LIMIT = ProviderLimit(concurrency=8, rpm=300)


class _Limiter:
    """A semaphore plus a token bucket. The bucket holds at most `concurrency` tokens, so
    no 60 s window sees more than rpm + concurrency calls."""

    def __init__(self, limit: ProviderLimit, clock: Clock, sleep: Sleep) -> None:
        self.sem = asyncio.Semaphore(limit.concurrency)
        self.rate = limit.rpm / 60.0  # tokens per second
        self.capacity = float(max(1, min(limit.rpm, limit.concurrency)))
        self.tokens = self.capacity
        self.clock, self.sleep = clock, sleep
        self.updated = clock()
        self.lock = asyncio.Lock()

    async def take_token(self) -> None:
        async with self.lock:
            while True:
                now = self.clock()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                await self.sleep((1 - self.tokens) / self.rate)


def litellm_model_name(spec: ModelSpec) -> str:
    """The model string sent to LiteLLM, with `hf_provider` pinned into HF model strings
    (`huggingface/<provider>/<org>/<model>`)."""
    if spec.provider != "huggingface" or not spec.hf_provider:
        return spec.litellm_model
    rest = spec.litellm_model.split("/")[1:]
    if len(rest) == 3:  # already has a provider segment: replace it
        rest = rest[1:]
    return "/".join(["huggingface", spec.hf_provider, *rest])


def response_format(spec: ModelSpec, response_model: type[BaseModel]) -> dict[str, Any] | None:
    if spec.structured == "json_schema":
        return {
            "type": "json_schema",
            "json_schema": {
                "name": response_model.__name__,
                "schema": response_model.model_json_schema(),
                "strict": True,
            },
        }
    if spec.structured == "json_object":
        return {"type": "json_object"}
    return None  # "none": the schema is in the prompt (prompts.build_messages)


_GEMINI_VERSION = re.compile(r"^gemini/gemini-(\d+)")


def sends_temperature(spec: ModelSpec) -> bool:
    """False for Gemini 3+: Google is removing temperature (and top_p/top_k) and wants
    sampling steered through the instructions. Those models keep their default temperature.
    We never send top_p or top_k."""
    m = _GEMINI_VERSION.match(spec.litellm_model)
    return not (m and int(m.group(1)) >= 3)


def request_kwargs(spec: ModelSpec, response_model: type[BaseModel]) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "model": litellm_model_name(spec),
        "max_tokens": spec.max_tokens,
        "drop_params": True,  # providers that lack a param (e.g. reasoning_effort) skip it
    }
    if sends_temperature(spec):
        kwargs["temperature"] = spec.temperature
    if spec.reasoning_effort is not None:
        # LiteLLM maps this per provider: OpenAI reasoning_effort, Gemini thinking budget
        # ("disable" gives Gemini a thinking budget of 0).
        kwargs["reasoning_effort"] = spec.reasoning_effort
    fmt = response_format(spec, response_model)
    if fmt is not None:
        kwargs["response_format"] = fmt
    api_base = spec.resolved_api_base()
    if api_base is not None:
        kwargs["api_base"] = api_base
    if spec.api_key_env is not None:
        api_key = os.environ.get(spec.api_key_env)
        if not api_key:
            raise ValueError(f"missing API key for {spec.name}; set {spec.api_key_env} in .env")
        kwargs["api_key"] = api_key
    return kwargs


_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def extract_json(text: str) -> str:
    """Strip a ```json fence and any prose around the outermost JSON object."""
    m = _FENCE.match(text)
    if m:
        text = m.group(1)
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if 0 <= start < end else text


def estimate_cost(spec: ModelSpec, input_tokens: int, output_tokens: int) -> float:
    """USD estimate at full input price (no prompt-caching discount). `*_cost_per_mtok` in
    the spec override LiteLLM's price table; models it doesn't know cost 0."""
    import litellm

    try:
        in_cost, out_cost = litellm.cost_per_token(
            model=litellm_model_name(spec),
            prompt_tokens=input_tokens,
            completion_tokens=output_tokens,
        )
    except Exception:
        in_cost = out_cost = 0.0
    if spec.input_cost_per_mtok is not None:
        in_cost = input_tokens * spec.input_cost_per_mtok / 1e6
    if spec.output_cost_per_mtok is not None:
        out_cost = output_tokens * spec.output_cost_per_mtok / 1e6
    return in_cost + out_cost


def _retryable(e: Exception) -> bool:
    import litellm

    if isinstance(e, litellm.exceptions.APIConnectionError | litellm.exceptions.Timeout):
        return True
    return getattr(e, "status_code", None) in RETRY_STATUS


API_KEY_ENV = {
    "gemini": "GEMINI_API_KEY",
    "huggingface": "HF_TOKEN",
    "hosted_vllm": "HF_TOKEN",  # DeepInfra; Registry.apply_env sets HOSTED_VLLM_API_KEY from it
    "openai": "OPENAI_API_KEY",
}
_MISSING_CREDS = re.compile(r"api[_ ]key|credentials|auth(?:entication|orization)? token", re.I)


def _is_auth_error(e: Exception) -> bool:
    import litellm

    if isinstance(e, litellm.exceptions.AuthenticationError):
        return True
    return getattr(e, "status_code", None) is None and bool(_MISSING_CREDS.search(str(e)))


def _auth_message(spec: ModelSpec, e: Exception) -> str:
    """Names the env var to set. Never includes the provider's text, which may echo a key."""
    var = spec.api_key_env or API_KEY_ENV.get(spec.provider, f"{spec.provider.upper()}_API_KEY")
    return f"{type(e).__name__}: missing or invalid API key for {spec.name}; set {var} in .env"


class LiteLLMClient:
    """`ModelClient` over `litellm.acompletion`. Retries 429/5xx with exponential backoff
    (separate from the orchestrator's game-level retry) and never raises for a bad reply."""

    def __init__(
        self,
        limits: dict[str, ProviderLimit] | None = None,
        max_retries: int = 4,
        backoff_s: float = 1.0,
        max_backoff_s: float = 30.0,
        sleep: Sleep = asyncio.sleep,
        clock: Clock = time.monotonic,
    ) -> None:
        self.limits = {**DEFAULT_LIMITS, **(limits or {})}
        self.max_retries = max_retries
        self.backoff_s, self.max_backoff_s = backoff_s, max_backoff_s
        self.sleep, self.clock = sleep, clock
        self._limiters: dict[str, _Limiter] = {}
        self._jitter = random.Random()  # client-side only; the engine never sees this

    def _limiter(self, provider: str) -> _Limiter:
        if provider not in self._limiters:
            limit = self.limits.get(provider, FALLBACK_LIMIT)
            self._limiters[provider] = _Limiter(limit, self.clock, self.sleep)
        return self._limiters[provider]

    async def complete(
        self,
        model: ModelSpec,
        messages: list[dict[str, Any]],
        response_model: type[BaseModel],
    ) -> CompletionResult:
        import litellm  # imported here: engine modules import Usage and must not load it

        try:
            kwargs = request_kwargs(model, response_model)
        except ValueError as e:
            return CompletionResult(
                parsed=None, raw="", error=str(e), usage=Usage(model=model.name, attempts=0)
            )
        limiter = self._limiter(model.provider)
        usage = Usage(model=model.name, attempts=0)
        latency = 0.0
        for attempt in range(self.max_retries + 1):
            await limiter.take_token()
            async with limiter.sem:
                usage.attempts += 1
                start = self.clock()
                try:
                    resp = await litellm.acompletion(messages=messages, **kwargs)
                    error = None
                except Exception as e:
                    resp, error = None, e
                latency += self.clock() - start
            if error is None:
                break
            if _is_auth_error(error):  # fail fast: retrying a bad key never helps
                usage.latency_ms = round(latency * 1000)
                return CompletionResult(
                    parsed=None, raw="", error=_auth_message(model, error), usage=usage
                )
            if not _retryable(error) or attempt == self.max_retries:
                usage.latency_ms = round(latency * 1000)
                return CompletionResult(
                    parsed=None, raw="", error=f"{type(error).__name__}: {error}", usage=usage
                )
            delay = min(self.max_backoff_s, self.backoff_s * 2**attempt)
            await self.sleep(delay * (0.5 + self._jitter.random() / 2))

        tokens = getattr(resp, "usage", None)
        usage.input_tokens = getattr(tokens, "prompt_tokens", 0) or 0
        usage.output_tokens = getattr(tokens, "completion_tokens", 0) or 0
        usage.cost_usd = estimate_cost(model, usage.input_tokens, usage.output_tokens)
        usage.latency_ms = round(latency * 1000)
        if not getattr(resp, "choices", None):
            return CompletionResult(
                parsed=None, raw="", error="provider returned no choices", usage=usage
            )
        raw = resp.choices[0].message.content or ""
        if not raw.strip():
            return CompletionResult(parsed=None, raw=raw, error="empty reply", usage=usage)
        try:
            parsed = response_model.model_validate_json(extract_json(raw))
        except ValidationError as e:
            return CompletionResult(parsed=None, raw=raw, error=str(e), usage=usage)
        return CompletionResult(parsed=parsed, raw=raw, usage=usage)
