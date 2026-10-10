"""Visitor spend per UTC day against PLAY_DAILY_BUDGET_USD, persisted in Neon (visitor_spend).

The running total lives in memory, so checks are synchronous; it is loaded from Neon at
startup (a restart keeps today's spend) and every booked cost is inserted as a row. With
`persistent=True` the ledger is not `ready` until that load succeeds, and a ledger that is
not ready reports nothing left: no Neon, no visitor games (fail closed).

Each model call reserves its worst-case cost before it starts (`try_reserve`, refused when
it doesn't fit) and settles to the real cost when it returns; a call that never returns
(cancelled at game end) keeps its reservation. Amounts are integer nano-dollars, so as long
as a call costs at most its reservation, spend never passes the budget, not even by rounding.
"""

import asyncio
import contextlib
import logging
from datetime import UTC, date, datetime
from typing import Any

log = logging.getLogger(__name__)

MAX_GAMES_SHOWN = 1000  # remaining_today() when a game is estimated to cost nothing
_TODAY = "select coalesce(sum(cost_usd), 0) from visitor_spend where day = $1"
_INSERT = (
    "insert into visitor_spend (day, game_id, cost_usd, created_at) "
    "values ($1, $2, $3::double precision, now())"
)


def _today() -> date:
    return datetime.now(UTC).date()


def nanos(usd: float) -> int:
    return max(0, round(usd * 1e9))


class Ledger:
    def __init__(self, budget_usd: float = 2.0, *, persistent: bool = False, timeout=10.0):
        self.budget_nanos = nanos(budget_usd)
        self.game_estimate_usd = 0.0  # the default game's prior estimate; set by the app
        self.persistent = persistent
        self.ready = not persistent
        self.timeout = timeout
        self._connect: Any = None
        self._conn: Any = None
        self._lock = asyncio.Lock()
        self._day = _today()
        self.spent_nanos = 0  # booked plus reserved, today
        self.games: dict[str, float] = {}  # game_id -> USD booked today

    def _roll(self) -> None:
        if (today := _today()) != self._day:
            self._day, self.spent_nanos, self.games = today, 0, {}

    async def load(self, connect: Any) -> bool:
        """Read today's spend from Neon. Returns whether the ledger is ready."""
        if not self.persistent:
            return True
        if connect is None:
            return False
        try:
            async with self._lock:
                self._connect = connect
                conn = await self._conn_ready()
                self._roll()
                total = await asyncio.wait_for(conn.fetchval(_TODAY, self._day), self.timeout)
            self.spent_nanos = max(self.spent_nanos, nanos(float(total or 0)))
            self.ready = True
        except Exception as exc:  # the message may hold the DSN: log the type only
            log.warning("spend ledger unavailable (%s); refusing visitor games", type(exc).__name__)
            await self._drop()
        return self.ready

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
        if not self.persistent or usd <= 0:
            return
        try:
            async with self._lock:
                conn = await self._conn_ready()
                await asyncio.wait_for(conn.execute(_INSERT, self._day, game_id, usd), self.timeout)
        except Exception as exc:  # the in-memory total still holds it
            log.warning("could not persist visitor spend (%s)", type(exc).__name__)
            await self._drop()

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
        async with self._lock:
            await self._drop()
