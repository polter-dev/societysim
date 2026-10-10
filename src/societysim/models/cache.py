"""Cassette cache: record model replies to disk and replay them for free re-runs.

`CachedClient` wraps any `ModelClient` without changing its interface. One JSON file per
call, named by a hash of (model spec, messages, response schema).
"""

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from societysim.config import ModelSpec
from societysim.models.client import CompletionResult, ModelClient, Usage, extract_json

CacheMode = Literal["off", "record", "replay", "record_missing"]

# Spec fields that never change what the model is asked, so they stay out of the key.
_KEY_IGNORED = {"color", "input_cost_per_mtok", "output_cost_per_mtok"}


class CacheMiss(Exception):
    """Raised in `replay` mode when a call has no recording."""


class CachedUsage(Usage):
    """The original usage of a replayed call. `cached=True` lets cost reports skip it."""

    cached: bool = True


def cache_key(
    model: ModelSpec, messages: list[dict[str, Any]], response_model: type[BaseModel]
) -> str:
    payload = {
        "model": model.model_dump(mode="json", exclude=_KEY_IGNORED),
        "messages": messages,
        "schema": response_model.model_json_schema(),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode()).hexdigest()


class CachedClient:
    def __init__(
        self, inner: ModelClient, directory: Path | str, mode: CacheMode = "record_missing"
    ) -> None:
        self.inner = inner
        self.dir = Path(directory)
        self.mode = mode

    async def complete(
        self,
        model: ModelSpec,
        messages: list[dict[str, Any]],
        response_model: type[BaseModel],
    ) -> CompletionResult:
        if self.mode == "off":
            return await self.inner.complete(model, messages, response_model)
        key = cache_key(model, messages, response_model)
        path = self.dir / f"{key}.json"
        if self.mode != "record" and path.exists():
            return self._load(path, response_model)
        if self.mode == "replay":
            raise CacheMiss(f"no recording for {model.name} call {key[:12]}")
        result = await self.inner.complete(model, messages, response_model)
        self.dir.mkdir(parents=True, exist_ok=True)
        entry = {
            "raw": result.raw,
            "error": result.error,
            "parsed": result.parsed is not None,
            "usage": result.usage.model_dump(mode="json", exclude={"cached"}),
        }
        path.write_text(json.dumps(entry, sort_keys=True, indent=1) + "\n", encoding="utf-8")
        return result

    @staticmethod
    def _load(path: Path, response_model: type[BaseModel]) -> CompletionResult:
        entry = json.loads(path.read_text(encoding="utf-8"))
        parsed = None
        if entry["parsed"]:
            parsed = response_model.model_validate_json(extract_json(entry["raw"]))
        return CompletionResult(
            parsed=parsed,
            raw=entry["raw"],
            error=entry["error"],
            usage=CachedUsage(**entry["usage"]),
        )
