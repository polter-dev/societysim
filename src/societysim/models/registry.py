"""Model registry: loads configs/models.yaml and resolves model names. Also the doctor probe."""

import fnmatch
import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from societysim.config import ModelSpec
from societysim.models.client import CompletionResult, ModelClient, ProviderLimit

DEFAULT_PATH = Path("configs/models.yaml")
MODES: tuple[Literal["json_schema", "json_object", "none"], ...] = (
    "json_schema",
    "json_object",
    "none",
)


class ProviderLimitConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    concurrency: int = Field(ge=1)
    rpm: int = Field(ge=1)


class Registry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    providers: dict[str, ProviderLimitConfig] = {}
    # Process env LiteLLM reads for providers needing a custom endpoint. `env` sets literals
    # (kept if already set); `env_from` copies another variable, e.g. HF_TOKEN -> an API key.
    env: dict[str, str] = {}
    env_from: dict[str, str] = {}
    models: list[ModelSpec]

    @model_validator(mode="after")
    def _unique(self) -> "Registry":
        names = [m.name for m in self.models]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError(f"duplicate model names: {', '.join(dupes)}")
        return self

    def get(self, name: str) -> ModelSpec:
        for m in self.models:
            if m.name == name:
                return m
        raise KeyError(f"unknown model {name!r}; known: {', '.join(m.name for m in self.models)}")

    def resolve(self, names: list[str]) -> list[ModelSpec]:
        return [self.get(n) for n in names]

    def match(self, pattern: str = "*") -> list[ModelSpec]:
        """Models whose name matches a shell-style glob, in file order."""
        return [m for m in self.models if fnmatch.fnmatchcase(m.name, pattern)]

    def apply_env(self) -> None:
        """Export `env` and `env_from` to os.environ. Call after load_dotenv(), before any call."""
        for k, v in self.env.items():
            os.environ.setdefault(k, v)
        for k, src in self.env_from.items():
            if src in os.environ:
                os.environ.setdefault(k, os.environ[src])

    def limits(self) -> dict[str, ProviderLimit]:
        """For `LiteLLMClient(limits=...)`."""
        return {p: ProviderLimit(c.concurrency, c.rpm) for p, c in self.providers.items()}


def load_registry(path: str | Path = DEFAULT_PATH) -> Registry:
    return Registry.model_validate(yaml.safe_load(Path(path).read_text()) or {})


# --- doctor ---------------------------------------------------------------------------------


class Ping(BaseModel):
    """Tiny structured reply used by doctor."""

    model_config = ConfigDict(extra="forbid")  # strict json_schema needs additionalProperties

    ok: bool
    word: str


class ModeResult(BaseModel):
    mode: str
    ok: bool
    latency_ms: int
    cost_usd: float
    error: str | None = None


DOCTOR_MESSAGES = [
    {"role": "system", "content": "Reply with JSON only."},
    {
        "role": "user",
        "content": 'Return a JSON object {"ok": true, "word": "<any one word>"}. '
        "The fields are ok (boolean) and word (string).",
    },
]


def _result(mode: str, r: CompletionResult) -> ModeResult:
    return ModeResult(
        mode=mode,
        ok=r.parsed is not None,
        latency_ms=r.usage.latency_ms,
        cost_usd=r.usage.cost_usd,
        error=r.error,
    )


async def probe_model(client: ModelClient, spec: ModelSpec) -> list[ModeResult]:
    """Try every structured mode, the configured one first. One result per mode."""
    order = [spec.structured, *(m for m in MODES if m != spec.structured)]
    results = []
    for mode in order:
        probe = spec.model_copy(update={"structured": mode})
        results.append(_result(mode, await client.complete(probe, DOCTOR_MESSAGES, Ping)))
    return results
