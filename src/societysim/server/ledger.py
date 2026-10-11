"""Visitor spend and games started per client, per UTC day, persisted in Neon.

The running totals live in memory, so checks are synchronous; they are loaded from Neon at
startup (a restart keeps today's spend and each client's game count) and every change is
written as a row: a `visitor_spend` row per booked cost, a `visitor_starts` row per client
(an HMAC of the IP keyed with PLAY_IP_SALT, never the IP). With `persistent=True` the ledger
is not `ready` until that load succeeds, and a ledger that is not ready reports nothing left:
no Neon, no visitor games (fail closed). A failed write makes it not ready too: the row is
kept, and a retry with backoff writes it and reloads before games are admitted again (#382).

Each model call reserves its worst-case cost before it starts (`try_reserve`, refused when
it doesn't fit) and settles to the real cost when it returns; a call that never returns
(cancelled at game end) keeps its reservation. Amounts are integer nano-dollars, so as long
as a call costs at most its reservation, spend never passes the budget, not even by rounding.
"""

import asyncio
import contextlib
import hashlib
import hmac
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

log = logging.getLogger(__name__)

MAX_GAMES_SHOWN = 1000  # remaining_today() when a game is estimated to cost nothing
MIN_SALT = 16  # PLAY_IP_SALT characters; `openssl rand -hex 32` gives 64
RETRY_MIN_S, RETRY_MAX_S = 1.0, 60.0  # backoff between attempts to write what failed
_TODAY = "select coalesce(sum(cost_usd), 0) from visitor_spend where day = $1"
# A write that timed out may have landed anyway. (game_id, created_at) is unique per booking,
# so writing the row again never counts it twice.
_INSERT = (
    "insert into visitor_spend (day, game_id, cost_usd, created_at) "
    "select $1::date, $2::text, $3::double precision, $4::timestamptz "
    "where not exists "
    "(select 1 from visitor_spend where game_id = $2::text and created_at = $4::timestamptz)"
)
_STARTS = "select client_hash, games from visitor_starts where day = $1"
_PRUNE_STARTS = "delete from visitor_starts where day < $1"
# The row holds the absolute count, so a retried write is harmless too.
_UPSERT_START = (
    "insert into visitor_starts (day, client_hash, games) values ($1, $2, $3) "
    "on conflict (day, client_hash) do update "
    "set games = greatest(visitor_starts.games, excluded.games)"
)


def _today() -> date:
    return datetime.now(UTC).date()


def nanos(usd: float) -> int:
    return max(0, round(usd * 1e9))


class Ledger:
    def __init__(
        self,
        budget_usd: float = 2.0,
        *,
        persistent: bool = False,
        timeout=10.0,
        ip_salt: str = "",
    ):
        self.budget_nanos = nanos(budget_usd)
        self.game_estimate_usd = 0.0  # the default game's prior estimate; set by the app
        self.persistent = persistent
        self.ready = not persistent
        self.timeout = timeout
        self._salt = ip_salt.encode()
        self._connect: Any = None
        self._conn: Any = None
        self._lock = asyncio.Lock()
        self._day = _today()
        self.spent_nanos = 0  # booked plus reserved, today
        self.games: dict[str, float] = {}  # game_id -> USD booked, kept across midnight
        self.starts: dict[str, int] = {}  # client_key(ip) -> games started today
        self.unsaved: list[tuple[str, tuple]] = []  # (sql, args) not yet written to Neon
        self._last_at = datetime.min.replace(tzinfo=UTC)
        self._retry: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()
        self.on_spend: Any = None  # async (spent_usd, budget_usd), called after each booking

    def _roll(self) -> None:
        if (today := _today()) != self._day:
            # `games` stays: a game running across midnight must not book its cost again.
            self._day, self.spent_nanos, self.starts = today, 0, {}

    async def load(self, connect: Any) -> bool:
        """Write what earlier writes missed, then read today's spend and game counts from
        Neon. Returns whether the ledger is ready."""
        if not self.persistent:
            return True
        if connect is None:
            return False
        if len(self._salt) < MIN_SALT:
            log.warning("PLAY_IP_SALT unset or under %d characters; refusing visitor games", MIN_SALT)
            return False
        try:
            async with self._lock:
                self._connect = connect
                conn = await self._conn_ready()
                await self._write_unsaved(conn)
                self._roll()
                total = await asyncio.wait_for(conn.fetchval(_TODAY, self._day), self.timeout)
                await asyncio.wait_for(conn.execute(_PRUNE_STARTS, self._day), self.timeout)
                rows = await asyncio.wait_for(conn.fetch(_STARTS, self._day), self.timeout)
            self.spent_nanos = max(self.spent_nanos, nanos(float(total or 0)))
            for row in rows:
                key = row["client_hash"]
                self.starts[key] = max(self.starts.get(key, 0), int(row["games"]))
            self.ready = True
        except Exception as exc:  # the message may hold the DSN: log the type only
            log.warning("visitor ledger unavailable (%s); refusing visitor games", type(exc).__name__)
            await self._drop()
        return self.ready

    def client_key(self, ip: str) -> str:
        """What the per-client count is kept and stored under: never the IP itself."""
        return hmac.new(self._salt, ip.encode(), hashlib.sha256).hexdigest()

    def started_today(self, ip: str) -> int:
        self._roll()
        return self.starts.get(self.client_key(ip), 0)

    def count_start(self, ip: str) -> None:
        """One more game for this client today. Synchronous; the row is written in the
        background, and if that fails no game starts until it is written."""
        self._roll()
        key = self.client_key(ip)
        self.starts[key] = games = self.starts.get(key, 0) + 1
        if self.persistent:
            self.unsaved.append((_UPSERT_START, (self._day, key, games)))
            task = asyncio.ensure_future(self._flush())
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    def spent_usd(self) -> float:
        self._roll()
        return self.spent_nanos / 1e9

    def remaining_usd(self) -> float:
        if not self.ready:
            return 0.0
        self._roll()
        return max(0, self.budget_nanos - self.spent_nanos) / 1e9

    def remaining_today(self) -> int:
        """Games of the default estimate still affordable today (GET /api/limits)."""
        left = self.remaining_usd()
        if left <= 0:
            return 0
        if self.game_estimate_usd <= 0:
            return MAX_GAMES_SHOWN
        return min(MAX_GAMES_SHOWN, int(left // self.game_estimate_usd))

    def try_reserve(self, usd: float) -> int | None:
        """Hold `usd` against the budget if it fits; returns the held nanos, or None.
        Synchronous, so two calls can't both take the last of the budget."""
        if not self.ready or not usd < float("inf"):  # an unpriceable call never fits
            return None
        held = nanos(usd)
        self._roll()
        if self.spent_nanos + held > self.budget_nanos:
            return None
        self.spent_nanos += held
        return held

    async def settle(self, game_id: str, held: int, cost_usd: float | None) -> None:
        """Book a call: its real cost, or (None: it never returned) what was held for it."""
        booked = held if cost_usd is None else nanos(cost_usd)
        self._roll()
        self.spent_nanos = max(0, self.spent_nanos + booked - held)
        await self._book(game_id, booked / 1e9)

    async def record(self, game_id: str, cost_usd: float) -> None:
        """At game end, however it ended: books what the per-call metering missed, if any."""
        missed = nanos(cost_usd - self.games.get(game_id, 0.0))
        if missed > 0:
            self._roll()
            self.spent_nanos += missed
            await self._book(game_id, missed / 1e9)

    async def _book(self, game_id: str, usd: float) -> None:
        self.games[game_id] = self.games.get(game_id, 0.0) + usd
        if self.on_spend is not None:
            with contextlib.suppress(Exception):  # an alert must never break a game
                await self.on_spend(self.spent_nanos / 1e9, self.budget_nanos / 1e9)
        if not self.persistent or usd <= 0:
            return
        at = max(datetime.now(UTC), self._last_at + timedelta(microseconds=1))
        self._last_at = at  # strictly increasing, so each booking's row is distinct
        self.unsaved.append((_INSERT, (self._day, game_id, usd, at)))
        await self._flush()

    async def _flush(self) -> None:
        """Write the unsaved rows. On failure the ledger stops admitting games (fail closed);
        the in-memory totals still hold the rows, and a retry writes them and reloads."""
        if not self.ready:  # a retry writes them once Neon is back
            self._schedule_retry()
            return
        try:
            async with self._lock:
                await self._write_unsaved(await self._conn_ready())
        except Exception as exc:  # the message may hold the DSN: log the type only
            log.warning("could not persist the visitor ledger (%s)", type(exc).__name__)
            self.ready = False
            await self._drop()
            self._schedule_retry()

    async def _write_unsaved(self, conn: Any) -> None:
        while self.unsaved:
            sql, args = self.unsaved[0]
            await asyncio.wait_for(conn.execute(sql, *args), self.timeout)
            self.unsaved.pop(0)

    def _schedule_retry(self) -> None:
        if self._connect is not None and (self._retry is None or self._retry.done()):
            self._retry = asyncio.ensure_future(self._retry_loop())

    async def _retry_loop(self) -> None:
        delay = RETRY_MIN_S
        while not self.ready:
            await asyncio.sleep(delay)
            delay = min(delay * 2, RETRY_MAX_S)
            await self.load(self._connect)

    async def _conn_ready(self) -> Any:
        if self._conn is None:
            if self._connect is None:
                raise RuntimeError("no Neon connection")
            self._conn = await asyncio.wait_for(self._connect(), self.timeout)
        return self._conn

    async def _drop(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(conn.close(), self.timeout)

    async def close(self) -> None:
        for task in (self._retry, *self._tasks):
            if task is not None:
                task.cancel()
        async with self._lock:
            if self.unsaved and self._conn is not None:  # a last try for what failed
                with contextlib.suppress(Exception):
                    await self._write_unsaved(self._conn)
            await self._drop()
