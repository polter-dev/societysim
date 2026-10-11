"""#498: re-running neon/schema.sql takes no strong lock on the tables, so it can't block or
deadlock with a live NeonSink flush.

The static test always runs. The rest use a throwaway local Postgres (initdb + pg_ctl, the same
harness as test_neon_anon_privacy.py) and skip when no Postgres binaries are installed.
"""

import asyncio
import re

import asyncpg
import pytest
from test_neon_anon_privacy import BINDIR, _connect, pg_port  # noqa: F401

from societysim.logging.sinks.neon import NEON_SCHEMA_LOCK, SCHEMA_PATH, apply_schema

needs_pg = pytest.mark.skipif(BINDIR is None, reason="no local Postgres (initdb) installed")

TABLES = ("games", "events", "visitor_spend", "visitor_starts")
STRONG = {
    "ShareLock",
    "ShareRowExclusiveLock",
    "ExclusiveLock",
    "AccessExclusiveLock",
    "ShareUpdateExclusiveLock",
}


def test_alters_and_indexes_only_run_inside_a_catalog_check():
    """No bare ALTER TABLE / CREATE INDEX: both lock the table before checking IF NOT EXISTS."""
    sql = re.sub(r"--[^\n]*", "", SCHEMA_PATH.read_text(encoding="utf-8"))
    outside = re.sub(r"do \$\$.*?end \$\$;", "", sql, flags=re.S | re.I)
    assert not re.search(r"\b(alter table|create (unique )?index)\b", outside, re.I)


@pytest.fixture(scope="module")
def db(pg_port):  # noqa: F811
    asyncio.run(_setup(pg_port))
    return pg_port


async def _setup(port: int) -> None:
    su = await _connect(port, "postgres", "postgres")
    try:
        await su.execute("create role writer login")
        await su.execute("create database sim owner writer")
    finally:
        await su.close()
    w = await _connect(port, "writer")
    try:
        await apply_schema(w)
        await w.execute("insert into games (game_id, status) values ('g1', 'running')")
    finally:
        await w.close()


async def _insert_event(conn, seq: int) -> None:
    await conn.execute(
        "insert into events (game_id, seq, day, phase, type, schema_version, event)"
        " values ('g1', $1, 1, 'day', 'speech', 1, '{}')",
        seq,
    )


@needs_pg
async def test_second_apply_takes_no_strong_lock_on_the_tables(db):
    w = await _connect(db, "writer")
    try:
        async with w.transaction():
            await w.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
            rows = await w.fetch(
                "select c.relname, l.mode from pg_locks l join pg_class c on c.oid = l.relation"
                " where l.pid = pg_backend_pid() and c.relname = any($1)",
                list(TABLES),
            )
        assert {r["mode"] for r in rows if r["mode"] in STRONG} == set(), rows
    finally:
        await w.close()


@needs_pg
async def test_apply_finishes_while_a_flush_is_inserting_events(db):
    """The #498 deadlock: a NeonSink flush holds ROW EXCLUSIVE on events (and ROW SHARE on games
    for the FK check) in an open transaction while a new instance applies the schema."""
    flush, applier = await _connect(db, "writer"), await _connect(db, "writer")
    try:
        tx = flush.transaction()
        await tx.start()
        await _insert_event(flush, 1)
        await asyncio.wait_for(apply_schema(applier, lock_timeout="2s"), 10)
        await _insert_event(flush, 2)  # the flush goes on after the apply
        await tx.commit()
    finally:
        await flush.close()
        await applier.close()


@needs_pg
async def test_apply_waits_for_nobody_else_applying_and_then_fails_fast(db):
    holder, applier = await _connect(db, "writer"), await _connect(db, "writer")
    try:
        async with holder.transaction():
            await holder.execute("select pg_advisory_xact_lock($1)", NEON_SCHEMA_LOCK)
            with pytest.raises(asyncpg.LockNotAvailableError):
                await asyncio.wait_for(apply_schema(applier, lock_timeout="300ms"), 10)
        await apply_schema(applier)  # the failed apply rolled back; the next one goes through
    finally:
        await holder.close()
        await applier.close()


async def test_apply_schema_sets_the_timeout_and_takes_the_lock_before_the_schema():
    """The CLI paths (batch/play --neon, sync-neon) call apply_schema alone, so it must do both."""
    calls = []

    class Conn:
        def transaction(self):
            calls.append("begin")
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            calls.append("commit")

        async def execute(self, sql, *args):
            calls.append((sql.split("(")[0], args))

    await apply_schema(Conn())
    assert calls[:3] == [
        "begin",
        ("select set_config", ("lock_timeout", "5s")[1:]),
        ("select pg_advisory_xact_lock", (NEON_SCHEMA_LOCK,)),
    ]
    assert calls[3][0].startswith("-- Neon Postgres schema") and calls[4:] == ["commit"]
