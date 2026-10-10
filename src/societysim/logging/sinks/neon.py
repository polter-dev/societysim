"""Optional sink: stream a game into Neon Postgres, and backfill finished batches.

JSONL is the source of truth; Neon is a mirror for the viewer. Per the EventSink contract
(T1.3) nothing here may break a game: every sink method swallows its errors, and the
pending-event buffer is bounded (oldest dropped first).
"""

import json
import logging
import os
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from societysim.logging.events import Event

log = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).resolve().parents[4] / "neon" / "schema.sql"

_INSERT_GAME = """
insert into games (game_id, batch_id, status, models, started_at)
values ($1, $2, 'running', $3::jsonb, $4)
on conflict (game_id) do update set
    batch_id = excluded.batch_id, status = 'running', winner = null, ended_at = null,
    models = excluded.models, started_at = excluded.started_at
"""
_INSERT_EVENT = """
insert into events (game_id, seq, day, phase, type, actor, schema_version, event)
values ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
on conflict (game_id, seq) do nothing
"""
_INSERT_LABEL = """
insert into labels (game_id, idx, label_type, actor, source_seq, source, label)
values ($1, $2, $3, $4, $5, $6, $7::jsonb)
on conflict (game_id, idx) do update set
    label_type = excluded.label_type, actor = excluded.actor,
    source_seq = excluded.source_seq, source = excluded.source, label = excluded.label
"""
_END_GAME = "update games set status = $2, winner = $3, ended_at = now() where game_id = $1"

Connect = Callable[[], Awaitable[Any]]


def event_row(event: dict[str, Any]) -> tuple:
    """Row for the events table from an event dict (the full event goes in as jsonb)."""
    return (
        event["game_id"],
        event["seq"],
        event["day"],
        event["phase"],
        event["type"],
        event.get("actor"),
        event["schema_version"],
        json.dumps(event),
    )


def label_row(idx: int, label: dict[str, Any]) -> tuple:
    return (
        label["game_id"],
        idx,
        label["label"],
        label["actor"],
        label["source_seq"],
        label["source"],
        json.dumps(label),
    )


async def connect_neon(dsn: str | None = None) -> Any:
    import asyncpg

    dsn = dsn or os.environ.get("NEON_DATABASE_URL")
    if not dsn:
        raise RuntimeError("NEON_DATABASE_URL is not set")
    # Neon's pooled endpoint is pgbouncer in transaction mode: no prepared-statement cache.
    return await asyncpg.connect(dsn, statement_cache_size=0, timeout=10)


async def apply_schema(conn: Any) -> None:
    await conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))


class NeonSink:
    """EventSink writing to Neon. One instance per game; `batch_id` comes from the caller.

    Events are buffered and inserted `batch_size` at a time (and on end_game / close).
    """

    def __init__(
        self,
        batch_id: str,
        *,
        connect: Connect | None = None,
        batch_size: int = 25,
        max_pending: int = 2000,
    ):
        self.batch_id = batch_id
        self._connect = connect or connect_neon
        self._batch_size = batch_size
        self._max_pending = max_pending
        self._conn: Any = None
        self._pending: list[tuple] = []
        self._game_id: str | None = None
        self._ended = False

    async def _conn_ready(self) -> Any:
        if self._conn is None:
            self._conn = await self._connect()
        return self._conn

    async def start_game(self, run_meta: dict[str, Any]) -> None:
        try:
            conn = await self._conn_ready()
            self._game_id = run_meta["game_id"]
            self._ended = False
            started = run_meta.get("started_at")
            if isinstance(started, str):
                started = datetime.fromisoformat(started)
            await conn.execute(
                _INSERT_GAME,
                self._game_id,
                self.batch_id,
                json.dumps(run_meta.get("models")),
                started,
            )
        except Exception:
            log.exception("NeonSink.start_game failed")

    async def write(self, event: Event) -> None:
        try:
            self._pending.append(event_row(event.model_dump(mode="json")))
            if len(self._pending) > self._max_pending:
                del self._pending[: len(self._pending) - self._max_pending]
                log.warning("NeonSink buffer full, dropped oldest events")
            if len(self._pending) >= self._batch_size:
                await self._flush()
        except Exception:
            log.exception("NeonSink.write failed")

    async def _flush(self) -> None:
        if not self._pending:
            return
        rows = list(self._pending)
        try:
            conn = await self._conn_ready()
            await conn.executemany(_INSERT_EVENT, rows)
            del self._pending[: len(rows)]  # keep anything queued while we awaited
        except Exception:
            log.exception("NeonSink flush failed, will retry with the next batch")

    async def _finish(self, status: str, winner: str | None) -> None:
        try:
            await self._flush()
            if self._game_id is None or self._ended:
                return
            conn = await self._conn_ready()
            await conn.execute(_END_GAME, self._game_id, status, winner)
            self._ended = True
        except Exception:
            log.exception("NeonSink could not set game status")

    async def end_game(self, game_id: str, winner: str | None) -> None:
        await self._finish("finished", winner)

    async def close(self) -> None:
        await self._finish("failed", None)  # no-op when end_game already ran
        if self._conn is not None:
            try:
                await self._conn.close()
            except Exception:
                log.exception("NeonSink close failed")
            self._conn = None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


async def sync_batch(batch_dir: Path, conn: Any, chunk: int = 500) -> dict[str, int]:
    """Backfill every game in batch_dir (and its labels file) into Neon. Safe to re-run.

    Returns counts of games, events and labels read from the JSONL files.
    """
    totals = {"games": 0, "events": 0, "labels": 0}
    for path in sorted(batch_dir.glob("*.jsonl")):
        if path.name.endswith(".labels.jsonl"):
            continue
        events = _read_jsonl(path)
        if not events:
            continue
        gid = events[0]["game_id"]
        start_meta = events[0]["meta"] if events[0]["type"] == "game_start" else {}
        models = [s["model"] for s in (start_meta.get("seats") or {}).values()]
        end = next((e for e in reversed(events) if e["type"] == "game_end"), None)
        started = start_meta.get("ts")
        await conn.execute(
            _INSERT_GAME,
            gid,
            batch_dir.name,
            json.dumps(models),
            datetime.fromisoformat(started) if started else None,
        )
        rows = [event_row(e) for e in events]
        for i in range(0, len(rows), chunk):
            await conn.executemany(_INSERT_EVENT, rows[i : i + chunk])
        winner = end["meta"].get("winner") if end else None
        await conn.execute(_END_GAME, gid, "finished" if end else "failed", winner)
        labels_path = batch_dir / f"{path.stem}.labels.jsonl"
        labels = _read_jsonl(labels_path) if labels_path.is_file() else []
        if labels:
            await conn.executemany(_INSERT_LABEL, [label_row(i, lb) for i, lb in enumerate(labels)])
        totals["games"] += 1
        totals["events"] += len(events)
        totals["labels"] += len(labels)
    return totals
