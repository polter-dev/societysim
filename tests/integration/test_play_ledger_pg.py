"""#382: the visitor ledger against a throwaway local Postgres with neon/schema.sql, like #323's
tests. A write that fails stops games until a retry writes it; a restart within the UTC day
keeps today's spend and each client's game count; a retried row is never counted twice.
Skips when no Postgres binaries are installed."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from test_neon_anon_privacy import BINDIR, _connect, pg_port  # noqa: F401  (fixture)

from societysim.logging.sinks.neon import apply_schema
from societysim.server import ledger as ledger_module
from societysim.server.ledger import Ledger, nanos

asyncpg = pytest.importorskip("asyncpg")
pytestmark = pytest.mark.skipif(BINDIR is None, reason="no local Postgres (initdb) installed")

SALT = "pg-test-salt-0123456789"
IP = "203.0.113.9"


@pytest.fixture(scope="module")
def pg(pg_port):  # noqa: F811
    async def setup():
        su = await _connect(pg_port, "postgres", "postgres")
        try:
            await su.execute("create role ledger_writer login")
            await su.execute("create database ledger owner ledger_writer")
        finally:
            await su.close()
        w = await _connect(pg_port, "ledger_writer", "ledger")
        try:
            await apply_schema(w)
        finally:
            await w.close()

    asyncio.run(setup())
    return lambda: _connect(pg_port, "ledger_writer", "ledger")


async def _rows(connect, sql: str, *args):
    conn = await connect()
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


async def test_fail_closed_recover_and_restart(pg, monkeypatch):
    monkeypatch.setattr(ledger_module, "RETRY_MIN_S", 0.01)
    old = datetime.now(UTC).date() - timedelta(days=1)
    stale = "insert into visitor_starts (day, client_hash, games) values ($1, 'old', 3)"
    conn = await pg()
    try:
        await conn.execute(stale, old)
    finally:
        await conn.close()

    first = Ledger(5.0, persistent=True, ip_salt=SALT)
    assert await first.load(pg)
    held = first.try_reserve(0.50)
    first.count_start(IP)
    first.count_start(IP)
    await asyncio.gather(*first._tasks)

    await first._conn.close()  # the connection dies under the ledger: the next write fails
    await first.settle("g1", held, 0.25)
    assert not first.ready and first.try_reserve(0.01) is None and first.remaining_usd() == 0
    async with asyncio.timeout(10):  # the retry reconnects and writes the row
        while not first.ready:
            await asyncio.sleep(0.01)
    assert not first.unsaved

    # A row that landed although its write raised (a lost reply) is written again: a no-op.
    at = datetime.now(UTC) + timedelta(seconds=1)
    args = (first._day, "g2", 0.5, at)
    conn = await pg()
    try:
        await conn.execute(ledger_module._INSERT, *args)
    finally:
        await conn.close()
    first.spent_nanos += nanos(0.5)
    first.unsaved.append((ledger_module._INSERT, args))
    first.ready = False
    assert await first.load(pg)
    assert len(await _rows(pg, "select 1 from visitor_spend where game_id = 'g2'")) == 1
    await first.close()

    again = Ledger(5.0, persistent=True, ip_salt=SALT)  # a restart, same UTC day
    assert await again.load(pg)
    assert again.spent_usd() == pytest.approx(0.75)
    assert again.started_today(IP) == 2 and again.started_today("198.51.100.1") == 0
    rows = await _rows(pg, "select day, client_hash, games from visitor_starts")
    assert [(r["client_hash"], r["games"]) for r in rows] == [(again.client_key(IP), 2)]
    assert IP not in rows[0]["client_hash"]  # earlier days' rows are gone too
    await again.close()
