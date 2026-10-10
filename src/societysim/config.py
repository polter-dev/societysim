"""Config models, loaded from YAML. Every game rule is a flag on RuleFlags (see docs/RULES.md)."""

import os
import re
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Config(BaseModel):
    model_config = ConfigDict(extra="forbid")  # typos in YAML fail loudly


class ModelSpec(_Config):
    """One playable model, as listed in configs/models.yaml."""

    name: str  # short id used in logs and seats, e.g. "qwen3-235b"
    litellm_model: str  # "huggingface/<provider>/<org>/<model>", "gemini/...", "openai/..."
    structured: Literal["json_schema", "json_object", "none"] = "json_schema"
    temperature: float = 0.7
    max_tokens: int = 1024
    reasoning_effort: str | None = None  # e.g. "minimal"; None leaves the provider default
    hf_provider: str | None = None  # pin an HF Inference Provider
    api_base: str | None = Field(None, exclude_if=lambda value: value is None)
    api_key_env: str | None = Field(None, exclude_if=lambda value: value is None)
    input_cost_per_mtok: float | None = None  # USD per 1M tokens; overrides LiteLLM pricing
    output_cost_per_mtok: float | None = None
    color: str | None = None  # viewer color, e.g. "#4f8ef7"

    @property
    def provider(self) -> str:
        """LiteLLM provider prefix: "huggingface", "gemini", "openai", ..."""
        return self.litellm_model.split("/", 1)[0]

    def resolved_api_base(self) -> str | None:
        if self.api_base is None:
            return None
        match = re.fullmatch(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", self.api_base)
        if match:
            var = match.group(1)
            if not os.environ.get(var):
                raise ValueError(f"missing API base for {self.name}; set {var} in .env")
            return os.environ[var]
        return self.api_base


class RuleFlags(_Config):
    doctor_self_protect: bool = True
    doctor_no_repeat: bool = True  # not the same player two nights running
    max_days: int = Field(6, ge=1)  # no winner after this day's vote -> draw
    reveal_role_on_death: bool = True
    last_words: bool = False
    wolf_chat_rounds: int = Field(2, ge=1)
    discussion_rounds: int = Field(2, ge=1)
    allow_abstain: bool = True
    max_statement_words: int = Field(80, ge=1)  # longer text is truncated, not rejected


class RoleCounts(_Config):
    """Leave `villagers` out to fill the remaining seats, e.g. `roles: {wolves: 3}`."""

    wolves: int = Field(2, ge=1)
    seers: int = Field(1, ge=0, le=1)
    doctors: int = Field(1, ge=0, le=1)
    villagers: int | None = Field(None, ge=0)

    def resolved(self, n_players: int) -> "RoleCounts":
        villagers = n_players - self.wolves - self.seers - self.doctors
        if self.villagers is not None and self.villagers != villagers:
            raise ValueError(
                f"role counts add up to {n_players - villagers + self.villagers}, "
                f"expected {n_players}"
            )
        if villagers < 0:
            raise ValueError(f"too many special roles for {n_players} players")
        return self.model_copy(update={"villagers": villagers})


class GameConfig(_Config):
    n_players: int = Field(7, ge=7, le=9)
    seed: int = 0
    roles: RoleCounts = RoleCounts()
    rules: RuleFlags = RuleFlags()
    # Model names from models.yaml: one per seat, or a single name for self-play.
    # Empty in a batch template; the batch runner fills it per game.
    models: list[str] = []

    @model_validator(mode="after")
    def _check(self) -> Self:
        self.roles = self.roles.resolved(self.n_players)
        if len(self.models) not in (0, 1, self.n_players):
            n = len(self.models)
            raise ValueError(f"models must list 1 or {self.n_players} names, got {n}")
        return self

    def seat_models(self) -> list[str]:
        """Model name for each seat p0..p{n-1}."""
        return self.models * self.n_players if len(self.models) == 1 else list(self.models)


class BatchConfig(_Config):
    batch_id: str
    seed: int = 0
    n_games: int = Field(ge=1)
    models: list[str] = Field(min_length=1)
    mode: Literal["selfplay", "mixed"] = "mixed"
    budget_usd: float = Field(gt=0)  # hard stop for the whole batch
    max_concurrency: int = Field(4, ge=1)  # games in flight
    model_game_limits: dict[str, int] = {}  # e.g. {"gpt-5-mini": 10}: cap games per model
    # Stop starting games once a model's invalid_output rate (over finished games) passes this,
    # given at least min_calls_for_invalid_check calls from it. Like budget, not in the fingerprint.
    max_invalid_rate: float = Field(0.25, ge=0, le=1)
    min_calls_for_invalid_check: int = Field(40, ge=1)
    game: GameConfig = GameConfig()  # shared settings; models and seed are set per game


def load_yaml[T: BaseModel](path: str | Path, model: type[T]) -> T:
    """Load a YAML file into any config model, e.g. load_yaml("configs/x.yaml", BatchConfig)."""
    return model.model_validate(yaml.safe_load(Path(path).read_text()) or {})
