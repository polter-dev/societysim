"""#323: the Data API's anonymous role can't read hidden information of a running game.

Runs neon/schema.sql on a throwaway local Postgres (initdb + pg_ctl, trust auth on 127.0.0.1)
with Neon's roles: a login writer that owns the tables, as NeonSink does, and a nologin
`anonymous` that PostgREST switches to. Skips when no Postgres binaries are installed.
"""

import asyncio
import json
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

from societysim.logging.sinks.neon import apply_schema, event_row

asyncpg = pytest.importorskip("asyncpg")

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def _bindir() -> Path | None:
    if found := shutil.which("initdb"):
        return Path(found).parent
    if pg_config := shutil.which("pg_config"):
        out = subprocess.run([pg_config, "--bindir"], capture_output=True, text=True)
        d = Path(out.stdout.strip())
        if (d / "initdb").is_file():
            return d
    return None


BINDIR = _bindir()
pytestmark = pytest.mark.skipif(BINDIR is None, reason="no local Postgres (initdb) installed")


@pytest.fixture(scope="module")
def pg_port(tmp_path_factory):
    data = tmp_path_factory.mktemp("pg") / "data"
    subprocess.run(
        [BINDIR / "initdb", "-D", data, "-U", "postgres", "--auth=trust"],
        check=True,
        capture_output=True,
    )
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    opts = f"-p {port} -c listen_addresses=127.0.0.1 -c unix_socket_directories=''"
    subprocess.run(
        [BINDIR / "pg_ctl", "-D", data, "-o", opts, "-l", data / "log", "-w", "start"],
        check=True,
        capture_output=True,
    )
    try:
        yield port
    finally:
        subprocess.run(
            [BINDIR / "pg_ctl", "-D", data, "-m", "immediate", "stop"], capture_output=True
        )


def _connect(port: int, user: str, database: str = "sim"):
    return asyncpg.connect(host="127.0.0.1", port=port, user=user, database=database)


def _fixture_events(game_id: str) -> list[dict]:
    text = (FIXTURES / "sample_game.jsonl").read_text(encoding="utf-8")
    return [json.loads(x.replace("fixture-0001", game_id)) for x in text.splitlines() if x.strip()]


GAMES = {"g-run": "running", "g-done": "finished", "g-fail": "failed"}


@pytest.fixture(scope="module")
def db(pg_port):
    asyncio.run(_setup(pg_port))
    return pg_port


async def _setup(pg_port: int) -> None:
    """Neon-like roles, the schema applied twice by the writer, and three games."""
    su = await _connect(pg_port, "postgres", "postgres")
    try:
        await su.execute("create role anonymous nologin")
        await su.execute("create role writer login")
        await su.execute("create database sim owner writer")
    finally:
        await su.close()
    w = await _connect(pg_port, "writer")
    try:
        await apply_schema(w)
        # The live database before #323: anonymous could select events directly.
        await w.execute("grant select on events to anonymous")
        await apply_schema(w)  # idempotent, and it takes that grant away
        for gid, status in GAMES.items():
            await w.execute(
                "insert into games (game_id, batch_id, status, models) values ($1, 'b', $2, '[]')",
                gid,
                status,
            )
            rows = [event_row(e) for e in _fixture_events(gid)]
            await w.executemany(
                "insert into events (game_id, seq, day, phase, type, actor, schema_version, event)"
                " values ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)",
                rows,
            )
    finally:
        await w.close()


async def _as_anon(port: int, sql: str, *args):
    conn = await _connect(port, "postgres")
    try:
        async with conn.transaction():
            await conn.execute("set local role anonymous")
            return await conn.fetch(sql, *args)
    finally:
        await conn.close()


def _event(row) -> dict:
    return json.loads(row["event"])


async def test_fixture_has_hidden_information():
    events = _fixture_events("g")
    assert any(e["visibility"] != "public" for e in events)
    assert any(e["visibility"] == "public" and e.get("private_reasoning") for e in events)
    assert any(e["visibility"] == "public" and e.get("suspicion") for e in events)


async def test_anonymous_cannot_select_events(db):
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await _as_anon(db, "select event from events limit 1")


# A failed game counts as not over: batch resume reruns the same game_id and seed (same roles).
@pytest.mark.parametrize("gid", ["g-run", "g-fail"])
async def test_unfinished_game_reads_public_events_without_private_fields(db, gid):
    rows = await _as_anon(db, "select * from events_public where game_id = $1 order by seq", gid)
    full = _fixture_events(gid)
    public = [e for e in full if e["visibility"] == "public"]
    assert [r["seq"] for r in rows] == [e["seq"] for e in public]
    for row, original in zip(rows, public, strict=True):
        ev = _event(row)
        assert ev["private_reasoning"] is None and ev["suspicion"] is None
        assert ev == {**original, "private_reasoning": None, "suspicion": None}
    # Filters on the hidden rows' columns find nothing.
    hidden = await _as_anon(
        db,
        "select seq from events_public where game_id = $1"
        " and (event ->> 'visibility' <> 'public' or event ->> 'private_reasoning' is not null"
        " or type = 'game_start')",
        gid,
    )
    assert hidden == []


async def test_finished_game_reads_in_full(db):
    rows = await _as_anon(
        db, "select event from events_public where game_id = 'g-done' order by seq"
    )
    assert [_event(r) for r in rows] == _fixture_events("g-done")


async def test_status_change_reveals_the_rest(db):
    w = await _connect(db, "writer")
    try:
        await w.execute("insert into games (game_id, status) values ('g-flip', 'running')")
        rows = [event_row(e) for e in _fixture_events("g-flip")]
        await w.executemany(
            "insert into events (game_id, seq, day, phase, type, actor, schema_version, event)"
            " values ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)",
            rows,
        )
        q = "select count(*) as n from events_public where game_id = 'g-flip'"
        before = (await _as_anon(db, q))[0]["n"]
        await w.execute("update games set status = 'finished' where game_id = 'g-flip'")
        after = (await _as_anon(db, q))[0]["n"]
    finally:
        await w.close()
    assert before < after == len(rows)


async def test_anonymous_still_reads_games_labels_and_views(db):
    for name in ("games", "labels", "leaderboard", "leaderboard_by_batch", "lies_by_model"):
        await _as_anon(db, f"select * from {name}")
    board = await _as_anon(db, "select model from leaderboard")
    assert board, "leaderboard reads events with the owner's rights"
    for table in ("events", "games", "labels"):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await _as_anon(db, f"delete from {table}")
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await _as_anon(db, "select * from visitor_spend")


async def test_writer_keeps_full_access(db):
    w = await _connect(db, "writer")
    try:
        rows = await w.fetch("select event from events where game_id = 'g-run' order by seq")
    finally:
        await w.close()
    assert [_event(r) for r in rows] == _fixture_events("g-run")
