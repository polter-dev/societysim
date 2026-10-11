"""Optional sink: stream a game into Neon Postgres, and backfill finished batches.

JSONL is the source of truth; Neon is a mirror for the viewer. Per the EventSink contract
(T1.3) nothing here may break a game: every sink method swallows its errors, every call has a
timeout, and the pending-event buffer is bounded (oldest dropped first). A connection that dies
mid-game is dropped and replaced, with a bounded backoff while Neon stays down (#417).
"""

import asyncio
import contextlib
import json
import logging
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from societysim.logging.events import Event, EventType, Visibility
from societysim.logging.textio import read_text_lenient

log = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).resolve().parents[4] / "neon" / "schema.sql"

_INSERT_GAME = """
insert into games (game_id, batch_id, status, models, started_at, agents)
values ($1, $2, 'running', $3::jsonb, $4, $5)
on conflict (game_id) do update set
    batch_id = excluded.batch_id, status = 'running', winner = null, ended_at = null,
    models = excluded.models, started_at = excluded.started_at, agents = excluded.agents
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
_CLEAR_EVENTS = "delete from events where game_id = $1"
_CLEAR_LABELS = "delete from labels where game_id = $1"
_END_GAME = "update games set status = $2, winner = $3, ended_at = now() where game_id = $1"
_SET_SEATS = "update games set models = $2::jsonb where game_id = $1"

Connect = Callable[[], Awaitable[Any]]

# Seconds between reconnects after a failure: the first retry is immediate, then the wait
# starts at the first value and doubles up to the second.
RECONNECT_BACKOFF = (0.5, 30.0)


class _BackingOff(ConnectionError):
    """Neon is down and the sink is waiting before it tries to reconnect."""


def _connection_lost(conn: Any, exc: BaseException) -> bool:
    """True if `exc` leaves the connection unusable, so it must be replaced (#417): a network
    or timeout error, asyncpg's "connection is closed" and its connection errors."""
    import asyncpg

    lost = (OSError, TimeoutError, asyncpg.InterfaceError, asyncpg.PostgresConnectionError)
    if isinstance(exc, lost):
        return True
    try:
        return bool(conn.is_closed())
    except Exception:
        return False


# Per-call metadata a live (public-only) feed leaves out (#387): wolves, the seer and the doctor
# get longer prompts, so token counts, cost and timings would give their roles away. Same list as
# the events_public view in neon/schema.sql.
LIVE_HIDDEN_META = ("usage", "ts")


def live_redact(event: Event) -> Event:
    """A public event as anyone may see it while the game runs: no private reasoning, no
    suspicion, no LIVE_HIDDEN_META."""
    meta = event.meta.model_copy(update=dict.fromkeys(LIVE_HIDDEN_META))
    return event.model_copy(update={"private_reasoning": None, "suspicion": None, "meta": meta})


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


def public_seats(start_meta: dict[str, Any]) -> list[dict[str, str]]:
    """games.models from game_start's seats: [{"model", "name"}] in seat order. Never the role:
    anyone can read games, and a running game's game_start event is hidden from them (#400)."""
    seats = start_meta.get("seats") or {}
    return [
        {"model": seats[s].get("model", ""), "name": seats[s].get("name", "")}
        for s in sorted(seats, key=lambda s: int(s[1:]))
    ]


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
    return await asyncpg.connect(dsn, statement_cache_size=0, timeout=10, command_timeout=10)


async def apply_schema(conn: Any) -> None:
    await conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))


MIGRATIONS_DIR = SCHEMA_PATH.parent / "migrations"


def list_migrations() -> list[tuple[str, str]]:
    """(file name, sql) of neon/migrations/*.sql in order. Read-only."""
    return [(p.name, p.read_text(encoding="utf-8")) for p in sorted(MIGRATIONS_DIR.glob("*.sql"))]


async def apply_migrations(conn: Any) -> None:
    """Run every migration, all in one transaction. Only `sync-neon --migrate --apply` calls this:
    schema.sql, NeonSink and batch start never alter an existing table."""
    async with conn.transaction():
        for _, sql in list_migrations():
            await conn.execute(sql)


class NeonSink:
    """EventSink writing to Neon. One instance per game; `batch_id` comes from the caller.

    Events are buffered and inserted `batch_size` at a time, by a background timer every
    `flush_after` seconds (so a slow LLM game shows up live), and on end_game / close.

    A dead connection is dropped and the call retried once on a new one. If connecting keeps
    failing, further tries wait per RECONNECT_BACKOFF; end_game and close always make one more
    try, so the final status lands as soon as Neon is back. When the buffer is full the oldest
    events are dropped (`dropped` counts them); what a flush sent is removed by identity, so
    events queued during the flush are never lost with it (#420).

    `public_only` (visitor games, #281): the live feed gets only public events, without
    private reasoning, suspicion or per-call metadata (`live_redact`), since the Data API is
    readable by anyone, the visitor included. The full log goes in afterwards with `sync_game`.
    """

    def __init__(
        self,
        batch_id: str,
        *,
        connect: Connect | None = None,
        agents: str | None = None,
        public_only: bool = False,
        batch_size: int = 25,
        max_pending: int = 2000,
        timeout: float = 10,
        flush_after: float = 1.0,
    ):
        self._flush_after = flush_after
        self._timer: asyncio.Task[None] | None = None
        self._closed = False
        self._lock = asyncio.Lock()  # one statement at a time on the connection
        self._timeout = timeout
        self.batch_id = batch_id
        self.agents = agents  # 'scripted' | 'llm'; None when unknown
        self.public_only = public_only
        self._connect = connect or connect_neon
        self._batch_size = batch_size
        self._max_pending = max_pending
        self._conn: Any = None
        self._pending: list[tuple] = []
        self._game_id: str | None = None
        self._ended = False
        self._outcome: tuple[str, str | None] | None = None  # first (status, winner) asked for
        self._start_op: Callable[[Any], Awaitable[None]] | None = None  # until it lands
        self._failures = 0  # consecutive connection failures
        self._retry_at = 0.0  # loop time before which no reconnect is tried
        self.dropped = 0  # events dropped from the full buffer

    async def _t(self, fn: Any, *args: Any) -> Any:
        return await asyncio.wait_for(fn(*args), self._timeout)

    async def _conn_ready(self, *, force: bool = False) -> Any:
        if self._conn is None:
            if not force and asyncio.get_running_loop().time() < self._retry_at:
                raise _BackingOff
            try:
                self._conn = await asyncio.wait_for(self._connect(), self._timeout)
            except Exception:
                self._lost()
                raise
        return self._conn

    def _lost(self) -> None:
        """Drop the connection (if any) and schedule the next connect."""
        conn, self._conn = self._conn, None
        if conn is not None:
            with contextlib.suppress(Exception):
                conn.terminate()  # asyncpg: close the socket without waiting on it
        self._failures += 1
        first, cap = RECONNECT_BACKOFF
        wait = 0.0 if self._failures == 1 else min(cap, first * 2 ** (self._failures - 2))
        self._retry_at = asyncio.get_running_loop().time() + wait

    async def _with_conn(
        self, what: str, op: Callable[[Any], Awaitable[None]], *, force: bool = False
    ) -> bool:
        """Run `op(conn)`, swallowing errors. If the connection turns out dead it is dropped
        and `op` retried once on a new one. `force` ignores the backoff. True on success.

        If start_game hasn't reached Neon yet, its statements run first, so a game that
        started while Neon was down still gets its games row."""
        for _ in range(2):
            try:
                conn = await self._conn_ready(force=force)
                if self._start_op is not None:
                    await self._start_op(conn)
                    self._start_op = None
                await op(conn)
            except _BackingOff:
                return False
            except Exception as exc:
                log.exception(what)
                if self._conn is None or not _connection_lost(self._conn, exc):
                    return False  # a failed connect is already counted; other errors stay
                self._lost()
            else:
                self._failures = 0
                return True
        return False

    async def start_game(self, run_meta: dict[str, Any]) -> None:
        try:
            self._game_id = run_meta["game_id"]
            self._ended = False
            self._outcome = None
            started = run_meta.get("started_at")
            if isinstance(started, str):
                started = datetime.fromisoformat(started)
            models = json.dumps(run_meta.get("models"))
        except Exception:
            log.exception("NeonSink.start_game failed")
            return

        async def op(conn: Any) -> None:
            gid = self._game_id
            await self._t(
                conn.execute, _INSERT_GAME, gid, self.batch_id, models, started, self.agents
            )
            # A replayed game must not keep the old run's rows (JSONL was overwritten).
            await self._t(conn.execute, _CLEAR_EVENTS, gid)
            await self._t(conn.execute, _CLEAR_LABELS, gid)

        async def nothing_else(conn: Any) -> None:
            pass

        self._start_op = op
        await self._with_conn("NeonSink.start_game failed", nothing_else)

    async def write(self, event: Event) -> None:
        if event.type == EventType.GAME_START:
            await self._set_seats(event)
        try:
            if self.public_only:
                if event.visibility != Visibility.PUBLIC:
                    return
                event = live_redact(event)
            self._pending.append(event_row(event.model_dump(mode="json")))
            over = len(self._pending) - self._max_pending
            if over > 0:
                del self._pending[:over]
                self.dropped += over
                log.warning("NeonSink buffer full, dropped %d oldest events so far", self.dropped)
            if self._timer is None and not self._closed:
                self._timer = asyncio.create_task(self._tick())
            if len(self._pending) >= self._batch_size:
                await self._flush()
        except Exception:
            log.exception("NeonSink.write failed")

    async def _set_seats(self, event: Event) -> None:
        """Seat names and models go public on the games row as soon as the game starts."""
        try:
            seats = public_seats(event.model_dump(mode="json")["meta"])
            if not seats or self._game_id is None:
                return
            gid, models = self._game_id, json.dumps(seats)

            async def op(conn: Any) -> None:
                await self._t(conn.execute, _SET_SEATS, gid, models)

            async with self._lock:
                await self._with_conn("NeonSink could not set seat names", op)
        except Exception:
            log.exception("NeonSink could not set seat names")

    async def _tick(self) -> None:
        while True:
            await asyncio.sleep(self._flush_after)
            await self._flush()  # never raises; close() cancels this task

    async def _flush(self) -> None:
        async with self._lock:
            await self._flush_now()

    async def _flush_now(self, *, force: bool = False) -> None:
        if not self._pending:
            return
        rows = list(self._pending)

        async def op(conn: Any) -> None:
            await self._t(conn.executemany, _INSERT_EVENT, rows)

        if await self._with_conn("NeonSink flush failed, will retry", op, force=force):
            # By identity, not count: write() may have trimmed the front while we awaited, and
            # anything queued meanwhile must stay (#420).
            sent = {id(r) for r in rows}
            self._pending[:] = [r for r in self._pending if id(r) not in sent]

    async def _finish(self, status: str, winner: str | None) -> None:
        try:
            if self._outcome is None:
                self._outcome = (status, winner)  # close() after a failed end_game keeps it
            async with self._lock:
                await self._flush_now(force=True)
                if self._game_id is None or self._ended:
                    return
                gid, (status, winner) = self._game_id, self._outcome

                async def op(conn: Any) -> None:
                    await self._t(conn.execute, _END_GAME, gid, status, winner)

                what = "NeonSink could not set game status"
                self._ended = await self._with_conn(what, op, force=True)
        except Exception:
            log.exception("NeonSink could not set game status")

    async def end_game(self, game_id: str, winner: str | None) -> None:
        await self._finish("finished", winner)

    async def close(self) -> None:
        self._closed = True
        if self._timer is not None:
            self._timer.cancel()
            await asyncio.gather(self._timer, return_exceptions=True)
            self._timer = None
        await self._finish("failed", None)  # no-op when end_game already ran
        if self._conn is not None:
            try:
                await asyncio.wait_for(self._conn.close(), self._timeout)
            except Exception:
                log.exception("NeonSink close failed")
            self._conn = None


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Every JSON object line in the file. A bad last line (a hard kill mid-write) is skipped
    with a warning, as `read_log` does; a bad line anywhere else raises (#419)."""
    lines = [line for line in read_text_lenient(path).splitlines() if line.strip()]
    rows = []
    for i, line in enumerate(lines):
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"line {i + 1} is not a JSON object")
        except ValueError:
            if i < len(lines) - 1:
                raise
            log.warning("%s ends with a torn line, ignored", path.name)
            continue
        rows.append(row)
    return rows


async def sync_batch(batch_dir: Path, conn: Any, chunk: int = 500) -> dict[str, int]:
    """Backfill every game in batch_dir (and its labels file) into Neon. Safe to re-run.

    Returns counts of games, events and labels read from the JSONL files.
    """
    totals = {"games": 0, "events": 0, "labels": 0}
    for path in sorted(batch_dir.glob("*.jsonl")):
        if path.name.endswith(".labels.jsonl"):
            continue
        counts = await sync_game(path, conn, chunk)
        if counts:
            totals["games"] += 1
            totals["events"] += counts["events"]
            totals["labels"] += counts["labels"]
    return totals


async def sync_game(path: Path, conn: Any, chunk: int = 500) -> dict[str, int] | None:
    """Backfill one game's log (and its labels file) into Neon; batch_id is the dir name.
    Returns its event and label counts, or None for an empty log."""
    batch_dir = path.parent
    events = _read_jsonl(path)
    if not events:
        return None
    gid = events[0]["game_id"]
    start_meta = events[0]["meta"] if events[0]["type"] == "game_start" else {}
    models = public_seats(start_meta)
    end = next((e for e in reversed(events) if e["type"] == "game_end"), None)
    started = start_meta.get("ts")
    agents = None
    meta_path = batch_dir / f"{path.stem}.run_meta.json"
    if meta_path.is_file():
        with contextlib.suppress(OSError, ValueError, AttributeError):
            agents = json.loads(read_text_lenient(meta_path)).get("agents")
    rows = [event_row(e) for e in events]
    winner = end["meta"].get("winner") if end else None
    labels_path = batch_dir / f"{path.stem}.labels.jsonl"
    labels = _read_jsonl(labels_path) if labels_path.is_file() else []
    # All or nothing (#418): a failed re-sync must not leave the game 'running' with no events,
    # and readers never see it half-written.
    async with conn.transaction():
        await conn.execute(
            _INSERT_GAME,
            gid,
            batch_dir.name,
            json.dumps(models),
            datetime.fromisoformat(started) if started else None,
            agents,
        )
        await conn.execute(_CLEAR_EVENTS, gid)
        await conn.execute(_CLEAR_LABELS, gid)
        for i in range(0, len(rows), chunk):
            await conn.executemany(_INSERT_EVENT, rows[i : i + chunk])
        await conn.execute(_END_GAME, gid, "finished" if end else "failed", winner)
        if labels:
            await conn.executemany(_INSERT_LABEL, [label_row(i, lb) for i, lb in enumerate(labels)])
    return {"events": len(events), "labels": len(labels)}


async def delete_games(conn: Any, game_ids: list[str]) -> dict[str, Any]:
    """Delete games (events and labels go with them via on delete cascade).

    Returns the deleted ids, the unknown ids, and how many events and labels went.
    """
    ids = list(dict.fromkeys(game_ids))
    n_events = await conn.fetchval("select count(*) from events where game_id = any($1)", ids)
    n_labels = await conn.fetchval("select count(*) from labels where game_id = any($1)", ids)
    rows = await conn.fetch("delete from games where game_id = any($1) returning game_id", ids)
    deleted = sorted(r["game_id"] for r in rows)
    return {
        "deleted": deleted,
        "missing": [i for i in ids if i not in deleted],
        "events": n_events if deleted else 0,
        "labels": n_labels if deleted else 0,
    }


# Game ids in fixtures/sample_game.jsonl. Exact ids only: a real batch may start with "fixture".
FIXTURE_GAME_IDS = frozenset({"fixture-0001"})

_HAS_INSERTED_AT = """
select exists (
    select 1 from information_schema.columns
    where table_schema = current_schema() and table_name = 'events'
      and column_name = 'inserted_at'
)
"""


class PruneBlocked(RuntimeError):
    """--prune can't run safely; the message says what to do."""


_PRUNE_CANDIDATES = """
select g.game_id, g.batch_id, g.status,
       now() as db_now,
       (select max(e.inserted_at) from events e where e.game_id = g.game_id) as last_activity,
       exists (
           select 1 from events e where e.game_id = g.game_id and e.type = 'game_end'
       ) as has_end,
       (select count(*) from events e where e.game_id = g.game_id) as events,
       (select count(*) from labels l where l.game_id = g.game_id) as labels
from games g
order by g.batch_id, g.game_id
"""


async def find_prunable(
    conn: Any,
    *,
    older_than_hours: float = 6,
    batch_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Read-only. Games that should not reach the live leaderboard, each with the reason why.

    Reasons: `test batch <id>` (exact batch id given by the caller), `fixture` (a game id in
    FIXTURE_GAME_IDS, exact match), and `partial`: status running or failed, no game_end event,
    and no new event for more than `older_than_hours`. Activity is max(events.inserted_at)
    and the age is measured against the database's now(); client clocks and the event
    payload's own timestamps are never used. A finished game is never partial, even if its
    game_end row was lost. A game with no events is left alone.

    Raises PruneBlocked if events.inserted_at does not exist yet (migration not applied).
    """
    if not await conn.fetchval(_HAS_INSERTED_AT):
        raise PruneBlocked(
            "events.inserted_at is missing, so stalled games can't be told from live ones. "
            "Apply neon/migrations/001_events_inserted_at.sql first "
            "(`societysim sync-neon --migrate` shows it; `--migrate --apply` runs it)."
        )
    test_batches = set(batch_ids or [])
    found = []
    for r in await conn.fetch(_PRUNE_CANDIDATES):
        last = r["last_activity"]
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=UTC)
        if r["batch_id"] in test_batches:
            reason = f"test batch {r['batch_id']}"
        elif r["game_id"] in FIXTURE_GAME_IDS:
            reason = "fixture"
        elif (
            r["status"] in ("running", "failed")
            and not r["has_end"]
            and last is not None
            and last < r["db_now"] - timedelta(hours=older_than_hours)
        ):
            reason = f"partial: no game_end, last activity {last:%Y-%m-%d %H:%M} UTC"
        else:
            continue
        found.append(
            {
                "game_id": r["game_id"],
                "batch_id": r["batch_id"],
                "status": r["status"],
                "events": r["events"],
                "labels": r["labels"],
                "reason": reason,
            }
        )
    return found


async def prune_games(conn: Any, game_ids: list[str]) -> None:
    """Delete exactly these games, with their events and labels, in one transaction."""
    ids = list(dict.fromkeys(game_ids))
    if not ids:
        return
    async with conn.transaction():
        await conn.execute("delete from events where game_id = any($1)", ids)
        await conn.execute("delete from labels where game_id = any($1)", ids)
        await conn.execute("delete from games where game_id = any($1)", ids)
