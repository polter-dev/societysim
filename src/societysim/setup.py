"""Game settings the UI renders and submits: Basic (fixed defaults) or Advanced (every knob).

A GameSetup resolves to the GameConfig that `play` runs. The UI contract is
schema/game_setup.schema.json (generated from GameSetup) plus models_catalog.json
(`societysim catalog`); see docs/DATA_CONTRACT.md, "Game setup".
"""

import random
from functools import cache
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, ValidationInfo, model_validator

from societysim.config import GameConfig, ModelSpec, RoleCounts
from societysim.roles import SeatId

HUMAN = "human"  # agents.human.MODEL: the seat a person plays
REGISTRY = Path(__file__).resolve().parents[2] / "configs/models.yaml"
# Basic mode's models: the cheap hosted ones. OpenAI runs are selective and the AMD pod is often
# down, so neither is in the default.
DEFAULT_LINEUP = [
    "qwen3-235b",
    "llama4-maverick",
    "gemma4-26b",
    "gpt-oss-120b",
    "deepseek-v3.1",
    "gemini-flash-lite",
    "gemini-flash",
]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SpecialRoles(_Strict):
    seer: bool = True
    doctor: bool = True


class SeatModel(_Strict):
    seat: SeatId
    model: str = Field(description=f"A model name from models_catalog.json, or {HUMAN!r}.")


BASIC_FIXED = ("n_players", "n_wolves", "special_roles", "seats", "model_pool", "rotation")


@cache
def _registry_models() -> frozenset[str]:
    from societysim.models.registry import load_registry

    return frozenset(m.name for m in load_registry(REGISTRY).models)


class GameSetup(_Strict):
    """One game's settings. Every default is the Basic setup.

    Validate with `GameSetup.model_validate(data)`; model names are checked against
    configs/models.yaml unless `context={"models": {...}}` gives another set.
    """

    mode: Literal["basic", "advanced"] = Field(
        "basic", description="basic allows only seed and human_seat to change."
    )
    n_players: int = Field(10, ge=7, le=10)
    n_wolves: int = Field(2, ge=1, description="Must be fewer than the villager side.")
    special_roles: SpecialRoles = SpecialRoles()
    seats: list[SeatModel] | None = Field(
        None, description="One entry per seat p0..p{n_players-1}. Excludes model_pool."
    )
    model_pool: list[str] | None = Field(
        None,
        min_length=1,
        description="Models to fill the seats from; null means the default lineup.",
    )
    rotation: Literal["in_order", "shuffle"] = Field(
        "shuffle",
        description="How model_pool fills the model seats: in_order repeats the pool from p0; "
        "shuffle first shuffles the pool with the seed.",
    )
    human_seat: SeatId | None = Field(None, description="The seat a person plays, or null.")
    seed: int = 0

    @classmethod
    def basic(cls, human: bool = False, *, seed: int = 0) -> Self:
        """The default game: 10 seats, 2 wolves, seer and doctor; the human (if any) at p0."""
        return cls(human_seat="p0" if human else None, seed=seed)

    @model_validator(mode="after")
    def _check(self, info: ValidationInfo) -> Self:
        n, seats = self.n_players, [f"p{i}" for i in range(self.n_players)]
        if self.mode == "basic":
            for name in BASIC_FIXED:
                if getattr(self, name) != GameSetup.model_fields[name].default:
                    raise ValueError(f"{name} is fixed in basic mode; use mode 'advanced'")
        if self.n_wolves >= n - self.n_wolves:
            raise ValueError(
                f"{self.n_wolves} wolves need more than {n - self.n_wolves} villagers; "
                f"use at most {(n - 1) // 2} wolves for {n} players"
            )
        if self.human_seat is not None and self.human_seat not in seats:
            raise ValueError(f"human_seat {self.human_seat} is not one of p0..p{n - 1}")
        known = (info.context or {}).get("models") or _registry_models()

        if self.seats is not None:
            if self.model_pool is not None:
                raise ValueError("give seats or model_pool, not both")
            given = [s.seat for s in self.seats]
            if sorted(given) != sorted(seats):
                raise ValueError(f"seats must list p0..p{n - 1} once each, got {', '.join(given)}")
            humans = [s.seat for s in self.seats if s.model == HUMAN]
            if len(humans) > 1:
                raise ValueError(f"at most one human seat, got {', '.join(humans)}")
            if humans and self.human_seat not in (None, humans[0]):
                raise ValueError(
                    f"human_seat is {self.human_seat} but seats puts the human at {humans[0]}"
                )
            if self.human_seat is not None and not humans:
                raise ValueError(f"human_seat {self.human_seat}: set that seat's model to 'human'")
            self.human_seat = humans[0] if humans else None
            models = [(s.seat, s.model) for s in self.seats if s.model != HUMAN]
        else:
            if HUMAN in (self.model_pool or []):
                raise ValueError("model_pool can't hold 'human'; use human_seat")
            models = [("model_pool", m) for m in self.model_pool or DEFAULT_LINEUP]
        for where, m in models:
            if m not in known:
                raise ValueError(
                    f"unknown model {m!r} at {where}; known: {', '.join(sorted(known))}"
                )
        return self

    def seat_models(self) -> list[str]:
        """Model name per seat p0..p{n-1}, 'human' at the human seat. Deterministic in the seed."""
        if self.seats is not None:
            return [s.model for s in sorted(self.seats, key=lambda s: int(s.seat[1:]))]
        pool = list(self.model_pool or DEFAULT_LINEUP)
        if self.rotation == "shuffle":
            random.Random(self.seed).shuffle(pool)
        human = int(self.human_seat[1:]) if self.human_seat else None
        out, k = [], 0
        for i in range(self.n_players):
            if i == human:
                out.append(HUMAN)
            else:
                out.append(pool[k % len(pool)])
                k += 1
        return out

    def game_config(self) -> GameConfig:
        """The config `play` runs. Rule flags stay at their defaults."""
        roles = RoleCounts(
            wolves=self.n_wolves,
            seers=int(self.special_roles.seer),
            doctors=int(self.special_roles.doctor),
        )
        return GameConfig(
            n_players=self.n_players, seed=self.seed, roles=roles, models=self.seat_models()
        )


def setup_errors(exc: ValidationError) -> list[str]:
    """One readable line per validation error, e.g. 'n_players: Input should be ...'."""
    out = []
    for e in exc.errors():
        msg = str(e["ctx"]["error"]) if e["type"] == "value_error" else e["msg"]
        loc = ".".join(str(p) for p in e["loc"])
        out.append(f"{loc}: {msg}" if loc else msg)
    return out


def setup_json_schema() -> dict:
    """schema/game_setup.schema.json (written by scripts/gen-schema.sh)."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "game_setup.schema.json",
        **GameSetup.model_json_schema(),
    }


# --- models_catalog.json -------------------------------------------------------------------


class CatalogModel(_Strict):
    name: str  # what seats and model_pool use
    display_name: str
    provider: Literal["huggingface", "google", "openai", "amd", "other"]
    sponsor: str | None  # sponsor track, e.g. "huggingface" or "amd"; null for none
    price_tier: Literal["free", "low", "medium", "high", "unknown"]
    structured: Literal["json_schema", "json_object", "none"]
    structured_ok: bool
    color: str | None
    available: bool
    unavailable_reason: str | None


class ModelsCatalog(_Strict):
    human: str = HUMAN
    default_lineup: list[str]
    models: list[CatalogModel]


SPONSORS = {"huggingface", "amd"}


def _provider(spec: ModelSpec) -> str:
    if spec.api_key_env == "AMD_API_KEY":
        return "amd"
    # hosted_vllm without its own api_base goes through the HF router (models.yaml `env`).
    return {
        "huggingface": "huggingface",
        "hosted_vllm": "huggingface",
        "gemini": "google",
        "openai": "openai",
    }.get(spec.provider, "other")


def _price_tier(spec: ModelSpec) -> str:
    """By USD for 1M input + 1M output tokens."""
    from societysim.models.client import estimate_cost

    usd = estimate_cost(spec, 1_000_000, 1_000_000)
    if usd == 0:
        priced = spec.input_cost_per_mtok is not None and spec.output_cost_per_mtok is not None
        return "free" if priced else "unknown"
    return "low" if usd < 1.5 else "medium" if usd < 3 else "high"


def build_catalog(specs: list[ModelSpec], *, check: bool = False) -> ModelsCatalog:
    """Self-hosted models are unavailable when their base URL env var is unset; with `check`,
    also when GET {api_base}/models doesn't list them (a network call per server)."""
    from societysim.models.registry import check_served_model

    models = []
    for spec in specs:
        try:
            spec.resolved_api_base()
            # The check's own message names the server URL; keep that out of a published file.
            down = check and spec.api_base and check_served_model(spec)
            reason = f"{spec.name}: its server is down or not serving it" if down else None
        except ValueError as exc:
            reason = str(exc)
        provider = _provider(spec)
        models.append(
            CatalogModel(
                name=spec.name,
                display_name=spec.litellm_model.rsplit("/", 1)[-1].split(":")[0],
                provider=provider,
                sponsor=provider if provider in SPONSORS else None,
                price_tier=_price_tier(spec),
                structured=spec.structured,
                structured_ok=spec.structured != "none",
                color=spec.color,
                available=reason is None,
                unavailable_reason=reason,
            )
        )
    return ModelsCatalog(default_lineup=DEFAULT_LINEUP, models=models)
