"""#453: the play server's Neon startup. The schema apply is serialized with an advisory lock,
and if Neon (or the ledger load) fails the server keeps retrying in the background instead of
running sink-less until a restart. Fake connections always run; the concurrent-apply test uses a
throwaway local Postgres (initdb + pg_ctl) and skips when none is installed."""

import asyncio
import contextlib
import logging
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="needs the server extra: uv run --extra server pytest")
asyncpg = pytest.importorskip("asyncpg")

from societysim.logging.sinks import neon  # noqa: E402
from societysim.server.ledger import Ledger  # noqa: E402
from societysim.server.manager import NEON_SCHEMA_LOCK, GameManager, Settings  # noqa: E402


class FakeConn:
    def __init__(self, error: Exception | None = None):
        self.error, self.executed = error, []

    @contextlib.asynccontextmanager
    async def transaction(self):
        yield

    async def execute(self, sql, *args):
        self.executed.append((sql, args))
        if self.error and not args:  # the schema itself, after the lock was taken
            raise self.error

    async def close(self):
        pass


def manager(tmp_path) -> GameManager:
    m = GameManager(Settings(runs_dir=tmp_path, neon=True), Ledger(persistent=True))
    m.neon_backoff = (0,)
    return m


def fake_neon(monkeypatch, errors: list[Exception | None]) -> list[FakeConn]:
    """connect_neon hands out a FakeConn per attempt, failing with errors[i] (the last repeats)."""
    conns: list[FakeConn] = []

    async def connect(dsn=None):
        conns.append(FakeConn(errors[min(len(conns), len(errors) - 1)]))
        return conns[-1]

    monkeypatch.setattr(neon, "connect_neon", connect)
    monkeypatch.setattr(neon, "SCHEMA_PATH", Path(__file__))  # any readable text
    return conns


async def test_schema_apply_takes_the_advisory_lock_first(tmp_path, monkeypatch):
    conns = fake_neon(monkeypatch, [None])
    m = manager(tmp_path)
    await m.prepare_neon()
    assert m.neon_connect is neon.connect_neon and m._neon_task is None
    (lock, lock_args), (schema, _) = conns[0].executed
    assert "pg_advisory_xact_lock" in lock and lock_args == (NEON_SCHEMA_LOCK,)
    assert schema == Path(__file__).read_text(encoding="utf-8")


async def test_deadlock_at_startup_is_retried_and_the_sink_comes_up(tmp_path, monkeypatch, caplog):
    deadlock = asyncpg.exceptions.DeadlockDetectedError("deadlock detected")
    conns = fake_neon(monkeypatch, [deadlock, None])
    m = manager(tmp_path)
    loads = []

    async def on_ready():
        loads.append(m.neon_connect)
        return True

    with caplog.at_level(logging.WARNING):
        await m.prepare_neon(on_ready)
        assert m.neon_connect is None  # games started now run without the sink
        await asyncio.wait_for(m._neon_task, 5)
    assert m.neon_connect is neon.connect_neon
    assert len(conns) == 2 and loads == [neon.connect_neon]  # the ledger loads once Neon is up
    messages = [r.getMessage() for r in caplog.records]
    assert messages == [
        "Neon unavailable (DeadlockDetectedError); not streaming live",
        "Neon available again; streaming live",
    ]


async def test_persistent_failure_keeps_retrying_without_crashing(tmp_path, monkeypatch, caplog):
    conns = fake_neon(monkeypatch, [ConnectionRefusedError("down")])
    m = manager(tmp_path)
    with caplog.at_level(logging.WARNING):
        await m.prepare_neon()
        for _ in range(200):
            if len(conns) >= 5:
                break
            await asyncio.sleep(0)
    assert len(conns) >= 5 and not m._neon_task.done()
    assert m.neon_connect is None
    assert len(caplog.records) == 1  # once per state change, not per attempt
    await m.shutdown()  # the retry loop stops with the server
    assert m._neon_task.cancelled()


async def test_ledger_load_failure_is_retried(tmp_path, monkeypatch):
    fake_neon(monkeypatch, [None])
    m = manager(tmp_path)
    results = iter([False, False, True])
    calls = []

    async def on_ready():
        calls.append(1)
        return next(results)

    await m.prepare_neon(on_ready)
    await asyncio.wait_for(m._neon_task, 5)
    assert len(calls) == 3 and m.neon_connect is neon.connect_neon


async def test_app_startup_retries_neon_and_then_loads_the_ledger(tmp_path, monkeypatch):
    """The lifespan wires the retry to the ledger: a visitor game is refused while Neon is down
    (fail closed) and the ledger becomes ready once the background retry succeeds."""
    from societysim.server.app import create_app
    from societysim.server.security import PlayConfig

    fake_neon(monkeypatch, [ConnectionResetError("reset"), None])

    async def load(self, connect):
        self.ready = connect is not None
        return self.ready

    monkeypatch.setattr(Ledger, "load", load)
    app = create_app(Settings(runs_dir=tmp_path, neon=True, play=PlayConfig(turnstile_secret="x")))
    m = app.state.manager
    m.neon_backoff = (0.05,)
    async with app.router.lifespan_context(app):
        assert not m.ledger.ready and m._neon_task is not None
        await asyncio.wait_for(m._neon_task, 5)
        assert m.ledger.ready and m.neon_connect is neon.connect_neon


# --- real Postgres: concurrent startups ---------------------------------------------------


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


@pytest.fixture
def pg_dsn(tmp_path):
    if BINDIR is None:
        pytest.skip("no local Postgres (initdb) installed")
    data = tmp_path / "pg"
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
        yield f"postgresql://postgres@127.0.0.1:{port}/postgres"
    finally:
        subprocess.run(
            [BINDIR / "pg_ctl", "-D", data, "-m", "immediate", "stop"], capture_output=True
        )


async def test_overlapping_instances_apply_the_schema_one_at_a_time(tmp_path, monkeypatch, pg_dsn):
    """Several instances starting at once on an empty database (a deploy overlap): each one
    applies the schema on its first try, with no failure and no background retry."""
    monkeypatch.setenv("NEON_DATABASE_URL", pg_dsn)
    managers = [manager(tmp_path) for _ in range(6)]
    try:
        await asyncio.gather(*(m.prepare_neon() for m in managers))
        assert all(m.neon_connect is neon.connect_neon and not m._neon_task for m in managers)
    finally:
        await asyncio.gather(*(m.shutdown() for m in managers))
    conn = await asyncpg.connect(pg_dsn)
    try:
        assert await conn.fetchval("select count(*) from games") == 0
    finally:
        await conn.close()
