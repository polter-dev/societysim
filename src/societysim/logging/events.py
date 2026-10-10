"""Event and Label models: the log contract shared by engine, runner and viewer.

Documented in docs/LOG_SCHEMA.md. `scripts/gen-schema.sh` turns these models into
schema/events.schema.json and viewer/src/types/events.ts. Any change here needs its own
ticket, a SCHEMA_VERSION bump and regenerated schema files in the same PR.
"""

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer

from societysim.models.client import Usage
from societysim.roles import Phase, Role, SeatId, SeerResult, Winner

SCHEMA_VERSION = 1


class EventType(StrEnum):
    # game flow
    GAME_START = "game_start"
    PHASE_START = "phase_start"
    # night
    WOLF_CHAT = "wolf_chat"
    WOLF_PROPOSAL = "wolf_proposal"
    NIGHT_KILL = "night_kill"
    DOCTOR_SAVE = "doctor_save"
    SEER_CHECK = "seer_check"
    DEATH_REVEAL = "death_reveal"
    # day speech, plus the self-tag events derived from a statement
    STATEMENT = "statement"
    ACCUSATION = "accusation"
    ROLE_CLAIM = "role_claim"
    SEER_CLAIM = "seer_claim"
    # votes
    VOTE = "vote"
    ELIMINATION = "elimination"
    NO_ELIMINATION = "no_elimination"
    # failures
    INVALID_OUTPUT = "invalid_output"
    FALLBACK_ACTION = "fallback_action"
    # end
    GAME_END = "game_end"


class Visibility(StrEnum):
    """Which players could see this event during the game."""

    PUBLIC = "public"
    WOLVES = "wolves"
    SEER = "seer"
    DOCTOR = "doctor"
    NARRATOR = "narrator"  # nobody in-game; the viewer's narrator mode only


class Seat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    model: str  # ModelSpec.name
    color: str
    role: Role


class Suspicion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    player: SeatId
    p: float = Field(ge=0, le=1)


class EventMeta(BaseModel):
    """Known meta keys. All optional; which ones appear depends on the event type
    (see docs/LOG_SCHEMA.md). Unknown keys are allowed. None values are not written."""

    model_config = ConfigDict(extra="allow")

    ts: str | None = None  # wall-clock ISO 8601 UTC
    seed: int | None = None  # game_start
    seats: dict[SeatId, Seat] | None = None  # game_start
    usage: Usage | None = None  # every LLM-backed event
    round: int | None = None  # wolf chat or discussion round, 1-based
    statement_seq: int | None = None  # self-tag events -> seq of their statement
    role: Role | None = None  # role_claim (claimed), death_reveal / elimination (revealed)
    result: SeerResult | None = None  # seer_check, seer_claim
    night: int | None = None  # seer_claim: night of the claimed check
    saved: bool | None = None  # doctor_save
    method: Literal["consensus", "plurality", "tiebreak"] | None = None  # night_kill
    tally: dict[SeatId, int] | None = None  # night_kill, elimination, no_elimination
    winner: Winner | None = None  # game_end
    reason: str | None = None  # no_elimination, game_end
    roles: dict[SeatId, Role] | None = None  # game_end
    truncated: bool | None = None  # statement cut to the word cap
    fallback: bool | None = None  # action event produced by a fallback
    action: str | None = None  # invalid_output / fallback_action: statement|vote|wolf_chat|...
    attempt: int | None = None  # invalid_output: 1 or 2
    error: str | None = None  # invalid_output
    raw: str | None = None  # invalid_output: the model's raw reply

    @model_serializer(mode="wrap")
    def _drop_none(self, handler: Any) -> dict[str, Any]:
        return {k: v for k, v in handler(self).items() if v is not None}


class Event(BaseModel):
    """One line of <game_id>.jsonl."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = SCHEMA_VERSION
    game_id: str
    seq: int = Field(ge=0)  # 0-based line index; unique and monotonic
    t: int = Field(ge=0)  # engine step; +1 per phase transition
    day: int = Field(ge=0)  # 0 for game_start; night N and day N share day=N
    phase: Phase
    type: EventType
    visibility: Visibility
    actor: SeatId | None = None
    target: SeatId | None = None
    public_text: str | None = None
    private_reasoning: str | None = None  # the actor's private thoughts
    suspicion: list[Suspicion] | None = None  # the actor's P(wolf) for other living players
    meta: EventMeta = EventMeta()


class LabelType(StrEnum):
    FALSE_ROLE_CLAIM = "false_role_claim"
    FALSE_REPORT = "false_report"
    BUSSING = "bussing"
    HIDDEN_KNOWLEDGE = "hidden_knowledge"
    MIS_ELIMINATION = "mis_elimination"


class Label(BaseModel):
    """One line of <game_id>.labels.jsonl. The raw game log is never edited."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = SCHEMA_VERSION
    game_id: str
    label: LabelType
    actor: SeatId
    source_seq: int = Field(ge=0)  # seq of the event the label is about
    source: Literal["self_tag", "jev", "engine"]
    confidence: float = Field(ge=0, le=1)
    detail: str
