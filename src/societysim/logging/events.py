"""Event and Label models: the log contract shared by engine, runner and viewer.

Documented in docs/LOG_SCHEMA.md. `scripts/gen-schema.sh` turns these models into
schema/events.schema.json and viewer/src/types/events.ts. Any change here needs its own
ticket, a SCHEMA_VERSION bump and regenerated schema files in the same PR.
"""

import asyncio
import logging
import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_serializer

from societysim.models.client import Usage
from societysim.roles import Phase, Role, SeatId, SeerResult, Winner

# 2: meta.usage.cached. 3: seat ids p0..p9 (10-player games). Older logs still load.
SCHEMA_VERSION = 3


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

    schema_version: Literal[1, 2, 3] = SCHEMA_VERSION
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

    schema_version: Literal[1, 2, 3] = SCHEMA_VERSION
    game_id: str
    label: LabelType
    actor: SeatId
    source_seq: int = Field(ge=0)  # seq of the event the label is about
    source: Literal["self_tag", "jev", "engine"]
    confidence: float = Field(ge=0, le=1)
    detail: str


# --- EventLogger -------------------------------------------------------------------------

log = logging.getLogger(__name__)


class EventSink(Protocol):
    async def start_game(self, run_meta: dict[str, Any]) -> None: ...
    async def write(self, event: Event) -> None: ...
    async def end_game(self, game_id: str, winner: Winner | None) -> None: ...
    async def close(self) -> None: ...


def _git_sha() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5, check=True
        )
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


class EventLogger:
    """Assigns seq and fans each event out to the sinks as it happens.

    `log()` is sync and never blocks: every sink has its own bounded queue and worker task.
    A full queue drops the item with a warning; a sink exception is logged and swallowed.
    start_game, end_game and close travel through the same queues, so sinks see calls in order.
    Must be created inside a running event loop.
    """

    def __init__(self, game_id: str, sinks: Sequence[EventSink], queue_size: int = 1000):
        self.game_id = game_id
        self._seq = 0
        self._sinks = list(sinks)
        self._queues = [asyncio.Queue[tuple[str, tuple] | None](queue_size) for _ in self._sinks]
        self._workers = [
            asyncio.create_task(self._run(s, q))
            for s, q in zip(self._sinks, self._queues, strict=True)
        ]

    async def _run(self, sink: EventSink, queue: asyncio.Queue) -> None:
        while (item := await queue.get()) is not None:
            method, args = item
            try:
                await getattr(sink, method)(*args)
            except Exception:
                log.exception("sink %s.%s failed", type(sink).__name__, method)

    def _fan_out(self, method: str, *args: Any) -> None:
        for sink, q in zip(self._sinks, self._queues, strict=True):
            try:
                q.put_nowait((method, args))
            except asyncio.QueueFull:
                log.warning("sink %s queue full, dropping %s", type(sink).__name__, method)

    async def start_game(self, config: dict[str, Any], seed: int, models: list[str]) -> None:
        run_meta = {
            "game_id": self.game_id,
            "config": config,
            "seed": seed,
            "models": models,
            "git_sha": _git_sha(),
            "started_at": datetime.now(UTC).isoformat(),
        }
        self._fan_out("start_game", run_meta)

    def log(self, **fields: Any) -> Event:
        """Build an Event (game_id and seq filled in) and queue it for every sink."""
        event = Event(game_id=self.game_id, seq=self._seq, **fields)
        self._seq += 1
        self._fan_out("write", event)
        return event

    async def end_game(self, winner: Winner | None) -> None:
        self._fan_out("end_game", self.game_id, winner)

    async def close(self, timeout: float = 5) -> None:
        """Let the sinks drain (up to `timeout`), stop the workers, then close the sinks."""
        for q in self._queues:
            while True:  # the stop marker must get in even if the queue is full
                try:
                    q.put_nowait(None)
                    break
                except asyncio.QueueFull:
                    q.get_nowait()
        # asyncio.wait rejects an empty set, so skip it when there are no sinks
        pending = (await asyncio.wait(self._workers, timeout=timeout))[1] if self._workers else ()
        for w in pending:  # ponytail: a stuck sink loses its backlog; fine for a log
            w.cancel()
        for sink in self._sinks:
            try:
                await sink.close()
            except Exception:
                log.exception("sink %s.close failed", type(sink).__name__)
