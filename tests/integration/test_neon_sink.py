"""NeonSink and sync-neon. Fake-connection tests always run; the real-database test needs
NEON_DATABASE_URL and skips cleanly without it."""

import asyncio
import json
import os
import shutil
import uuid
from pathlib import Path

import pytest
from typer.testing import CliRunner

from societysim.batch.runner import run_batch
from societysim.cli import app
from societysim.config import BatchConfig, ModelSpec
from societysim.logging.events import Event, EventLogger
from societysim.logging.sinks.neon import NeonSink, apply_schema, connect_neon, sync_batch

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


class FakeConn:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.executed: list[tuple] = []
        self.many: list[tuple[str, list]] = []
        self.closed = False

    async def execute(self, sql, *args):
        if self.fail:
            raise ConnectionError("down")
        self.executed.append((sql, args))

    async def executemany(self, sql, rows):
        if self.fail:
            raise ConnectionError("down")
        self.many.append((sql, list(rows)))

    async def close(self):
        self.closed = True


def _events(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _batch(tmp_path: Path, game_id: str = "fixture-0001") -> Path:
    d = tmp_path / "batch1"
    d.mkdir()
    for src, dst in (
        ("sample_game.jsonl", f"{game_id}.jsonl"),
        ("sample_game.labels.jsonl", f"{game_id}.labels.jsonl"),
    ):
        text = (FIXTURES / src).read_text(encoding="utf-8").replace("fixture-0001", game_id)
        (d / dst).write_text(text, encoding="utf-8")
    return d


async def _play(sink: NeonSink, n: int = 7, winner="wolves"):
    logger = EventLogger("g1", [sink])
    await logger.start_game({}, 1, ["m1", "m2"])
    for i in range(n):
        logger.log(t=i, day=1, phase="night", type="phase_start", visibility="public")
    await logger.end_game(winner)
    await logger.close()


async def test_batches_inserts_and_sets_status():
    conn = FakeConn()

    async def connect():
        return conn

    await _play(NeonSink("b1", connect=connect, batch_size=3))
    assert [len(rows) for _, rows in conn.many] == [3, 3, 1]
    seqs = [r[1] for _, rows in conn.many for r in rows]
    assert seqs == list(range(7))
    assert json.loads(conn.many[0][1][0][7])["type"] == "phase_start"
    games = [a for sql, a in conn.executed if "insert into games" in sql]
    assert games[0][:2] == ("g1", "b1")
    assert json.loads(games[0][2]) == ["m1", "m2"]
    ends = [a for sql, a in conn.executed if sql.startswith("update games")]
    assert ends == [("g1", "finished", "wolves")]
    assert conn.closed


async def test_close_without_end_game_marks_failed():
    conn = FakeConn()

    async def connect():
        return conn

    sink = NeonSink("b1", connect=connect)
    logger = EventLogger("g1", [sink])
    await logger.start_game({}, 1, [])
    logger.log(t=0, day=0, phase="setup", type="game_start", visibility="narrator")
    await logger.close()
    assert [a for sql, a in conn.executed if sql.startswith("update games")] == [
        ("g1", "failed", None)
    ]
    assert sum(len(r) for _, r in conn.many) == 1


def _event(seq: int) -> Event:
    return Event(
        game_id="g1", seq=seq, t=seq, day=1, phase="night", type="phase_start", visibility="public"
    )


async def _drive(sink: NeonSink) -> None:
    """Call every sink method directly, so only the sink's own guards can save us."""
    await sink.start_game({"game_id": "g1", "models": ["m"], "started_at": "2026-10-10T12:00:00"})
    for i in range(4):
        await sink.write(_event(i))
    await sink.end_game("g1", "wolves")
    await sink.close()


async def test_sink_errors_are_swallowed():
    conn = FakeConn(fail=True)

    async def connect():
        return conn

    await _drive(NeonSink("b1", connect=connect, batch_size=2))  # must not raise


async def test_connect_failure_is_swallowed():
    async def connect():
        raise OSError("no route")

    await _drive(NeonSink("b1", connect=connect, batch_size=1))


class HangConn(FakeConn):
    def __init__(self):
        super().__init__()
        self.never = asyncio.Event()

    async def execute(self, sql, *args):
        await self.never.wait()

    async def executemany(self, sql, rows):
        await self.never.wait()

    async def close(self):
        await self.never.wait()


async def test_hung_connection_cannot_block_the_game():
    conn = HangConn()

    async def connect():
        return conn

    sink = NeonSink("b1", connect=connect, batch_size=2, timeout=0.05)
    await asyncio.wait_for(_drive(sink), 3)


async def test_hung_connect_cannot_block_the_game():
    async def connect():
        await asyncio.Event().wait()

    sink = NeonSink("b1", connect=connect, batch_size=1, timeout=0.05)
    await asyncio.wait_for(_drive(sink), 3)


async def test_pending_buffer_is_bounded():
    async def connect():
        raise OSError("no route")

    sink = NeonSink("b1", connect=connect, batch_size=10**9, max_pending=5)
    logger = EventLogger("g1", [sink])
    for i in range(20):
        logger.log(t=i, day=1, phase="night", type="phase_start", visibility="public")
    await logger.close()
    assert len(sink._pending) <= 5
    assert [r[1] for r in sink._pending] == list(range(15, 20))  # newest kept


async def test_sync_batch_reads_every_line(tmp_path):
    batch = _batch(tmp_path)
    conn = FakeConn()
    totals = await sync_batch(batch, conn, chunk=40)
    events = _events(batch / "fixture-0001.jsonl")
    labels = _events(batch / "fixture-0001.labels.jsonl")
    assert totals == {"games": 1, "events": len(events), "labels": len(labels)}
    assert sum(len(r) for sql, r in conn.many if "into events" in sql) == len(events)
    assert max(len(r) for _, r in conn.many) <= 40
    game = next(a for sql, a in conn.executed if "insert into games" in sql)
    assert game[1] == "batch1"
    assert len(json.loads(game[2])) == 7
    end = next(a for sql, a in conn.executed if sql.startswith("update games"))
    assert end == ("fixture-0001", "finished", "wolves")


async def test_sync_batch_marks_unfinished_game_failed(tmp_path):
    batch = _batch(tmp_path)
    path = batch / "fixture-0001.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()[:-1]  # drop game_end
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    conn = FakeConn()
    await sync_batch(batch, conn)
    end = next(a for sql, a in conn.executed if sql.startswith("update games"))
    assert end == ("fixture-0001", "failed", None)


def test_sync_neon_cli_needs_url(tmp_path, monkeypatch):
    monkeypatch.delenv("NEON_DATABASE_URL", raising=False)
    monkeypatch.setattr("societysim.cli.load_dotenv", lambda: None)
    res = CliRunner().invoke(app, ["sync-neon", str(tmp_path)])
    assert res.exit_code == 1
    assert "NEON_DATABASE_URL" in res.output


async def test_lone_event_lands_while_the_game_is_idle():
    conn = FakeConn()

    async def connect():
        return conn

    sink = NeonSink("b1", connect=connect, batch_size=25, flush_after=0.05)
    await sink.start_game({"game_id": "g1", "models": []})
    await sink.write(_event(0))
    assert conn.many == []  # below batch_size, nothing sent yet
    await asyncio.sleep(0.3)  # no further events: only the timer can send it
    assert [r[1] for _, rows in conn.many for r in rows] == [0]
    await sink.write(_event(1))
    await sink.close()  # flushes the rest and stops the timer
    assert [r[1] for _, rows in conn.many for r in rows] == [0, 1]
    n_calls = len(conn.many) + len(conn.executed)
    await asyncio.sleep(0.2)
    assert len(conn.many) + len(conn.executed) == n_calls  # nothing runs after close
    assert sink._timer is None
    await sink.write(_event(2))  # a late write must not restart the timer
    assert sink._timer is None


async def test_timer_and_writes_never_overlap_on_the_connection():
    class OneAtATime(FakeConn):
        busy = False
        overlapped = False  # an assert here would be swallowed by the sink

        async def executemany(self, sql, rows):
            self.overlapped |= self.busy
            self.busy = True
            await asyncio.sleep(0.02)
            self.busy = False
            await super().executemany(sql, rows)

    conn = OneAtATime()

    async def connect():
        return conn

    sink = NeonSink("b1", connect=connect, batch_size=2, flush_after=0.01)
    await sink.start_game({"game_id": "g1", "models": []})
    for i in range(20):
        await sink.write(_event(i))
        await asyncio.sleep(0.005)
    await sink.close()
    assert sorted(r[1] for _, rows in conn.many for r in rows) == list(range(20))
    assert not conn.overlapped


def _event_rows(conn: FakeConn) -> dict[str, list[int]]:
    seqs: dict[str, list[int]] = {}
    for sql, rows in conn.many:
        if "into events" in sql:
            for r in rows:
                seqs.setdefault(r[0], []).append(r[1])
    return seqs


async def test_batch_streams_every_game_in_order(tmp_path):
    conn = FakeConn()

    async def connect():
        return conn

    cfg = BatchConfig.model_validate(
        {"batch_id": "nb", "n_games": 2, "models": ["a"], "budget_usd": 1, "game": {}}
    )
    specs = {"a": ModelSpec(name="a", litellm_model="openai/a")}
    res = await run_batch(cfg, specs, None, runs_dir=tmp_path, neon_connect=connect)
    assert res["status"] == "completed"
    seqs = _event_rows(conn)
    assert set(seqs) == set(res["finished"])
    for gid, got in seqs.items():
        assert got == list(range(len(_events(tmp_path / "nb" / f"{gid}.jsonl"))))
    ends = [a for sql, a in conn.executed if sql.startswith("update games")]
    assert sorted(a[0] for a in ends) == sorted(res["finished"])
    assert all(a[1] == "finished" for a in ends)


async def test_batch_survives_a_dead_neon(tmp_path):
    async def connect():
        raise OSError("no route")

    cfg = BatchConfig.model_validate(
        {"batch_id": "nb", "n_games": 2, "models": ["a"], "budget_usd": 1, "game": {}}
    )
    specs = {"a": ModelSpec(name="a", litellm_model="openai/a")}
    res = await run_batch(cfg, specs, None, runs_dir=tmp_path, neon_connect=connect)
    assert res["status"] == "completed"
    assert len(res["finished"]) == 2


CONFIG = Path(__file__).resolve().parents[2] / "configs/selfplay_smoke.yaml"


def test_play_neon_streams_live(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("societysim.cli.load_dotenv", lambda: None)
    conn = FakeConn()

    async def connect():
        return conn

    monkeypatch.setattr("societysim.logging.sinks.neon.connect_neon", connect)
    res = CliRunner().invoke(app, ["play", "--config", str(CONFIG), "--neon"])
    assert res.exit_code == 0, res.output
    assert "create table if not exists games" in conn.executed[0][0]  # schema first
    log = _events(tmp_path / "runs/selfplay_smoke/selfplay_smoke-s0.jsonl")
    assert _event_rows(conn) == {"selfplay_smoke-s0": [e["seq"] for e in log]}
    end = next(a for sql, a in conn.executed if sql.startswith("update games"))
    assert end == ("selfplay_smoke-s0", "finished", log[-1]["meta"]["winner"])


def test_play_neon_with_human_seat_streams_marked_events(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("societysim.cli.load_dotenv", lambda: None)
    conn = FakeConn()

    async def connect():
        return conn

    def answer(question):  # scripted stdin: first option, nothing optional
        if question.startswith(("statement", "message")):
            return "Hello."
        if question.startswith(("vote", "target", "kill")):
            return question.rsplit("(", 1)[1].split()[0].rstrip("):")
        return ""

    monkeypatch.setattr("societysim.logging.sinks.neon.connect_neon", connect)
    monkeypatch.setattr("builtins.input", answer)
    args = ["play", "--config", str(CONFIG), "--neon", "--human-seat", "p3"]
    res = CliRunner().invoke(app, args)
    assert res.exit_code == 0, res.output
    log = _events(tmp_path / "runs/selfplay_smoke/selfplay_smoke-s0.jsonl")
    assert _event_rows(conn) == {"selfplay_smoke-s0": [e["seq"] for e in log]}
    rows = [json.loads(r[7]) for _, batch in conn.many for r in batch]
    marked = [e["seq"] for e in rows if e["meta"].get("suspicion_default")]
    assert marked
    assert marked == [e["seq"] for e in log if e["meta"].get("suspicion_default")]
    assert rows[0]["meta"]["seats"]["p3"]["model"] == "human"


def test_play_without_neon_url_still_plays(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("societysim.cli.load_dotenv", lambda: None)
    monkeypatch.delenv("NEON_DATABASE_URL", raising=False)
    res = CliRunner().invoke(app, ["play", "--config", str(CONFIG), "--neon"])
    assert res.exit_code == 0, res.output
    assert "warning: Neon unavailable (RuntimeError)" in res.output
    assert (tmp_path / "runs/selfplay_smoke/selfplay_smoke-s0.jsonl").is_file()


@pytest.mark.skipif(not os.environ.get("NEON_DATABASE_URL"), reason="NEON_DATABASE_URL not set")
def test_real_neon_roundtrip(tmp_path):
    gid = f"it-{uuid.uuid4().hex[:8]}"
    batch = _batch(tmp_path, gid)
    n_events = len(_events(batch / f"{gid}.jsonl"))

    async def run():
        conn = await connect_neon()
        try:
            await apply_schema(conn)
            await sync_batch(batch, conn)
            await sync_batch(batch, conn)  # idempotent
            count = await conn.fetchval("select count(*) from events where game_id = $1", gid)
            status = await conn.fetchval("select status from games where game_id = $1", gid)
            await conn.fetch("select * from leaderboard")
            return count, status
        finally:
            await conn.execute("delete from games where game_id = $1", gid)
            await conn.close()

    count, status = asyncio.run(run())
    assert count == n_events
    assert status == "finished"
    shutil.rmtree(batch)
