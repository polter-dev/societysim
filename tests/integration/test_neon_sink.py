"""NeonSink and sync-neon. Fake-connection tests always run; the real-database test needs
NEON_DATABASE_URL and skips cleanly without it."""

import asyncio
import json
import os
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
import pytest
from typer.testing import CliRunner

from societysim.batch.runner import run_batch
from societysim.cli import app
from societysim.config import BatchConfig, ModelSpec
from societysim.logging.events import Event, EventLogger
from societysim.logging.sinks import neon
from societysim.logging.sinks.neon import (
    NeonSink,
    apply_schema,
    connect_neon,
    delete_games,
    sync_batch,
    sync_game,
)

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

    def transaction(self):
        return _Tx(self)

    def _snapshot(self):
        return len(self.executed), len(self.many)

    def _restore(self, snap):
        del self.executed[snap[0] :], self.many[snap[1] :]

    async def close(self):
        self.closed = True


class _Tx:
    """Rolls the fake back to where it was if the block raises."""

    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        self.snap = self.conn._snapshot()

    async def __aexit__(self, exc_type, *_):
        if exc_type is not None:
            self.conn._restore(self.snap)
        return False


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
    ends = [a for sql, a in conn.executed if sql.startswith("update games set status")]
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
    assert [a for sql, a in conn.executed if sql.startswith("update games set status")] == [
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
    seats = events[0]["meta"]["seats"]
    assert json.loads(game[2]) == [
        {"model": seats[f"p{i}"]["model"], "name": seats[f"p{i}"]["name"]} for i in range(7)
    ]
    end = next(a for sql, a in conn.executed if sql.startswith("update games set status"))
    assert end == ("fixture-0001", "finished", "wolves")


@pytest.mark.parametrize("public_only", [False, True])
async def test_game_start_puts_seat_names_on_the_games_row_without_roles(public_only):
    """A running game's game_start is hidden from the public (it holds every role), so the
    live viewer reads names from games.models (#400)."""
    conn = FakeConn()

    async def connect():
        return conn

    sink = NeonSink("b1", connect=connect, public_only=public_only)
    logger = EventLogger("g1", [sink])
    await logger.start_game({}, 1, ["m0", "m1"])
    seats = {
        "p1": {"name": "Bea", "model": "m1", "color": "#000", "role": "wolf"},
        "p0": {"name": "Ash", "model": "m0", "color": "#111", "role": "seer"},
    }
    logger.log(
        t=0, day=0, phase="setup", type="game_start", visibility="narrator", meta={"seats": seats}
    )
    await logger.close()
    sets = [a for sql, a in conn.executed if sql.startswith("update games set models")]
    assert sets == [
        ("g1", json.dumps([{"model": "m0", "name": "Ash"}, {"model": "m1", "name": "Bea"}]))
    ]
    assert "wolf" not in sets[0][1] and "seer" not in sets[0][1]
    written = [json.loads(r[7])["type"] for _, rows in conn.many for r in rows]
    assert written == ([] if public_only else ["game_start"])  # the event itself stays hidden


async def test_sync_batch_marks_unfinished_game_failed(tmp_path):
    batch = _batch(tmp_path)
    path = batch / "fixture-0001.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()[:-1]  # drop game_end
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    conn = FakeConn()
    await sync_batch(batch, conn)
    end = next(a for sql, a in conn.executed if sql.startswith("update games set status"))
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
    ends = [a for sql, a in conn.executed if sql.startswith("update games set status")]
    assert sorted(a[0] for a in ends) == sorted(res["finished"])
    assert all(a[1] == "finished" for a in ends)


async def test_batch_writes_agent_type(tmp_path):
    conn = FakeConn()

    async def connect():
        return conn

    cfg = BatchConfig.model_validate(
        {"batch_id": "nb", "n_games": 1, "models": ["a"], "budget_usd": 1, "game": {}}
    )
    specs = {"a": ModelSpec(name="a", litellm_model="openai/a")}
    await run_batch(cfg, specs, None, runs_dir=tmp_path, neon_connect=connect)
    games = [a for sql, a in conn.executed if "insert into games" in sql]
    assert [g[4] for g in games] == ["scripted"]


async def test_sink_writes_agents_and_null_when_unknown():
    for agents in ("llm", None):
        conn = FakeConn()

        async def connect(conn=conn):
            return conn

        sink = NeonSink("b1", connect=connect, agents=agents)
        await sink.start_game({"game_id": "g1", "models": []})
        game = next(a for sql, a in conn.executed if "insert into games" in sql)
        assert game[4] == agents


async def test_sync_batch_reads_agents_from_run_meta(tmp_path):
    batch = _batch(tmp_path)
    meta = batch / "fixture-0001.run_meta.json"

    async def agents_written() -> str | None:
        conn = FakeConn()
        await sync_batch(batch, conn)
        return next(a for sql, a in conn.executed if "insert into games" in sql)[4]

    assert await agents_written() is None  # older run, no run_meta
    meta.write_text('{"agents": "scripted"}', encoding="utf-8")
    assert await agents_written() == "scripted"
    meta.write_text("{bad", encoding="utf-8")
    assert await agents_written() is None


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
    monkeypatch.setattr("societysim.cli.secrets.token_hex", lambda n: "deadbeefdeadbeef")
    monkeypatch.setattr("societysim.cli.load_dotenv", lambda: None)
    conn = FakeConn()

    async def connect():
        return conn

    monkeypatch.setattr("societysim.logging.sinks.neon.connect_neon", connect)
    res = CliRunner().invoke(app, ["play", "--config", str(CONFIG), "--seed", "0", "--neon"])
    assert res.exit_code == 0, res.output
    assert "create table if not exists games" in conn.executed[0][0]  # schema first
    log = _events(tmp_path / "runs/selfplay_smoke/selfplay_smoke-deadbeefdeadbeef.jsonl")
    assert _event_rows(conn) == {"selfplay_smoke-deadbeefdeadbeef": [e["seq"] for e in log]}
    end = next(a for sql, a in conn.executed if sql.startswith("update games set status"))
    assert end == ("selfplay_smoke-deadbeefdeadbeef", "finished", log[-1]["meta"]["winner"])
    assert next(a for sql, a in conn.executed if "insert into games" in sql)[4] == "scripted"


def test_play_neon_with_human_seat_streams_unrated_suspicion_as_null(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("societysim.cli.secrets.token_hex", lambda n: "deadbeefdeadbeef")
    monkeypatch.setattr("societysim.cli.load_dotenv", lambda: None)
    conn = FakeConn()

    async def connect():
        return conn

    def answer(question):  # scripted stdin: choice 1, nothing optional
        if "blank" in question:
            return ""
        return "1" if "[1-" in question else "Hello."

    monkeypatch.setattr("societysim.logging.sinks.neon.connect_neon", connect)
    monkeypatch.setattr("builtins.input", answer)
    args = ["play", "--config", str(CONFIG), "--seed", "0", "--neon", "--human-seat", "p3"]
    res = CliRunner().invoke(app, args)
    assert res.exit_code == 0, res.output
    log = _events(tmp_path / "runs/selfplay_smoke/selfplay_smoke-deadbeefdeadbeef.jsonl")
    assert _event_rows(conn) == {"selfplay_smoke-deadbeefdeadbeef": [e["seq"] for e in log]}
    rows = [json.loads(r[7]) for _, batch in conn.many for r in batch]
    human = [e["seq"] for e in rows if e.get("actor") == "p3" and e["meta"].get("usage")]
    assert human  # the human skipped suspicion: Neon gets null, as in the log
    assert all(r["suspicion"] is None for r in rows if r["seq"] in human)
    assert all(e.get("suspicion") is None for e in log if e["seq"] in human)
    assert rows[0]["meta"]["seats"]["p3"]["model"] == "human"


def test_play_without_neon_url_still_plays(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("societysim.cli.secrets.token_hex", lambda n: "deadbeefdeadbeef")
    monkeypatch.setattr("societysim.cli.load_dotenv", lambda: None)
    monkeypatch.delenv("NEON_DATABASE_URL", raising=False)
    res = CliRunner().invoke(app, ["play", "--config", str(CONFIG), "--seed", "0", "--neon"])
    assert res.exit_code == 0, res.output
    assert "warning: Neon unavailable (RuntimeError)" in res.output
    assert (tmp_path / "runs/selfplay_smoke/selfplay_smoke-deadbeefdeadbeef.jsonl").is_file()


@pytest.mark.live
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


def test_schema_has_views_and_grants():
    sql = (Path(__file__).resolve().parents[2] / "neon/schema.sql").read_text(encoding="utf-8")
    for v in ("leaderboard", "leaderboard_by_batch", "lies_by_model"):
        assert f"create or replace view {v} as" in sql
    assert "alter table games add column if not exists agents text" in sql
    for v in ("leaderboard", "leaderboard_by_batch", "lies_by_model"):
        body = sql.split(f"view {v} as")[1].split(";")[0]
        assert "agents is distinct from 'scripted'" in body, v
    assert sql.index("add column if not exists agents") < sql.index("create or replace view")
    assert "grant select on leaderboard_by_batch, lies_by_model to anonymous" in sql
    assert "grant insert" not in sql and "grant all" not in sql


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("NEON_DATABASE_URL"), reason="NEON_DATABASE_URL not set")
def test_real_neon_views(tmp_path):
    ids = [f"it-{uuid.uuid4().hex[:8]}" for _ in range(2)]
    batches = ["itb-" + uuid.uuid4().hex[:6] for _ in range(2)]
    dirs = []
    for i, (gid, bid) in enumerate(zip(ids, batches, strict=True)):
        root = tmp_path / f"t{i}"
        root.mkdir()
        d = _batch(root, gid)
        # p6 becomes a human seat; p5 (liar in the fixture labels) keeps its model
        ep = d / f"{gid}.jsonl"
        evs = _events(ep)
        evs[0]["meta"]["seats"]["p6"]["model"] = "human"
        ep.write_text("\n".join(json.dumps(e) for e in evs) + "\n", encoding="utf-8")
        dirs.append(d.rename(root / bid))  # the directory name is the batch_id
    sc_root = tmp_path / "sc"
    sc_root.mkdir()
    sc_id = ids[0] + "-sc"
    sd = _batch(sc_root, sc_id).rename(sc_root / ("itb-sc-" + uuid.uuid4().hex[:6]))
    (sd / f"{sc_id}.run_meta.json").write_text('{"agents": "scripted"}', encoding="utf-8")
    liar = _events(dirs[0] / f"{ids[0]}.jsonl")[0]["meta"]["seats"]["p5"]["model"]

    async def run():
        conn = await connect_neon()
        try:
            await apply_schema(conn)
            for d in [*dirs, sd]:
                await sync_batch(d, conn)
            sc_rows = await conn.fetch(
                "select 1 from leaderboard_by_batch where batch_id = $1", sd.name
            )
            sc_lies = await conn.fetch("select 1 from lies_by_model where batch_id = $1", sd.name)
            sc_games = await conn.fetchval(
                "select count(*) from games where batch_id = $1", sd.name
            )
            board = await conn.fetch("select model from leaderboard")
            by_batch = await conn.fetch(
                "select distinct batch_id from leaderboard_by_batch where batch_id = any($1)",
                batches,
            )
            human = await conn.fetch(
                "select 1 from leaderboard_by_batch where batch_id = any($1) and model = 'human'",
                batches,
            )
            lies = await conn.fetch(
                "select model, lies from lies_by_model"
                " where batch_id = $1 and label_type = 'false_role_claim' and source = 'self_tag'",
                batches[0],
            )
            return board, by_batch, human, lies, sc_rows, sc_lies, sc_games
        finally:
            await conn.execute("delete from games where game_id = any($1)", [*ids, sc_id])
            await conn.close()

    board, by_batch, human, lies, sc_rows, sc_lies, sc_games = asyncio.run(run())
    assert not sc_rows and not sc_lies and sc_games == 1  # replayable, off the leaderboards
    assert "human" not in {r["model"] for r in board}
    assert sorted(r["batch_id"] for r in by_batch) == sorted(batches)
    assert not human
    assert [(r["model"], r["lies"]) for r in lies] == [(liar, 1)]


def test_abandoned_human_game_ends_failed_in_neon(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("societysim.cli.secrets.token_hex", lambda n: "deadbeefdeadbeef")
    monkeypatch.setattr("societysim.cli.load_dotenv", lambda: None)
    conn = FakeConn()

    async def connect():
        return conn

    monkeypatch.setattr("societysim.logging.sinks.neon.connect_neon", connect)
    args = ["play", "--config", str(CONFIG), "--seed", "0", "--neon", "--human-seat", "p0"]
    res = CliRunner().invoke(app, args, input="")  # stdin EOF: the player walks away
    assert res.exit_code == 130, res.output
    ends = [a for sql, a in conn.executed if sql.startswith("update games set status")]
    assert ends == [("selfplay_smoke-deadbeefdeadbeef", "failed", None)]


class MemConn(FakeConn):
    """Tiny in-memory Neon: games, events (game_id, seq) and labels (game_id, idx)."""

    def __init__(self):
        super().__init__()
        self.games: dict[str, str] = {}
        self.status: dict[str, str] = {}
        self.events: dict[tuple, tuple] = {}
        self.labels: dict[tuple, tuple] = {}

    def _snapshot(self):
        return {k: dict(getattr(self, k)) for k in ("games", "status", "events", "labels")}

    def _restore(self, snap):
        for k, v in snap.items():
            setattr(self, k, v)

    def _put(self, table, key, row, replace):
        if replace or key not in table:
            table[key] = row

    async def execute(self, sql, *args):
        if "insert into games" in sql:
            self.games[args[0]] = args[1]
            self.status[args[0]] = "running"
        elif sql.startswith("update games set status"):
            self.status[args[0]] = args[1]
        elif sql.startswith("delete from events"):
            self.events = {k: v for k, v in self.events.items() if k[0] != args[0]}
        elif sql.startswith("delete from labels"):
            self.labels = {k: v for k, v in self.labels.items() if k[0] != args[0]}

    async def executemany(self, sql, rows):
        for r in rows:
            if "into events" in sql:
                self._put(self.events, (r[0], r[1]), r, False)  # on conflict do nothing
            else:
                self._put(self.labels, (r[0], r[1]), r, True)

    async def fetchval(self, sql, ids):
        table = self.events if "from events" in sql else self.labels
        return sum(1 for k in table if k[0] in ids)

    async def fetch(self, sql, ids):
        gone = [g for g in self.games if g in ids]
        for g in gone:
            del self.games[g]
        self.events = {k: v for k, v in self.events.items() if k[0] not in gone}
        self.labels = {k: v for k, v in self.labels.items() if k[0] not in gone}
        return [{"game_id": g} for g in gone]


async def test_restarted_game_keeps_only_the_second_run():
    conn = MemConn()

    async def connect():
        return conn

    for n in (7, 3):  # the second run is shorter, like an interrupted-then-replayed game
        await _play(NeonSink("b1", connect=connect, batch_size=2), n=n)
    assert sorted(k[1] for k in conn.events) == list(range(3))


async def test_resync_replaces_events_and_drops_extra_labels(tmp_path):
    batch = _batch(tmp_path)
    conn = MemConn()
    await sync_batch(batch, conn)
    n_events, n_labels = len(conn.events), len(conn.labels)
    assert n_labels > 1
    await sync_batch(batch, conn)
    assert (len(conn.events), len(conn.labels)) == (n_events, n_labels)
    lp = batch / "fixture-0001.labels.jsonl"
    lines = lp.read_text(encoding="utf-8").splitlines()
    lp.write_text(lines[0] + "\n", encoding="utf-8")
    ep = batch / "fixture-0001.jsonl"
    await sync_batch(batch, conn)
    assert len(conn.labels) == 1
    # events rewritten from the file: drop the tail and the stale rows go too
    ep.write_text("\n".join(ep.read_text(encoding="utf-8").splitlines()[:5]) + "\n")
    await sync_batch(batch, conn)
    assert sorted(k[1] for k in conn.events) == list(range(5))


def _games_insert(conn: FakeConn) -> tuple[str, tuple]:
    return next((s, a) for s, a in conn.executed if "insert into games" in s)


def _drop_game_start_ts(batch: Path) -> None:
    ep = batch / "fixture-0001.jsonl"
    lines = ep.read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    first["meta"].pop("ts")  # the engine never writes it
    lines[0] = json.dumps(first)
    ep.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def test_sync_game_takes_started_at_from_run_meta(tmp_path):
    batch = _batch(tmp_path)
    _drop_game_start_ts(batch)
    (batch / "fixture-0001.run_meta.json").write_text(
        '{"started_at": "2026-10-10T09:30:00+00:00"}', encoding="utf-8"
    )
    conn = FakeConn()
    await sync_batch(batch, conn)
    assert _games_insert(conn)[1][3] == datetime(2026, 10, 10, 9, 30, tzinfo=UTC)


async def test_sync_game_falls_back_to_game_start_ts(tmp_path):
    conn = FakeConn()
    await sync_batch(_batch(tmp_path), conn)  # fixture has meta.ts and no run_meta
    assert _games_insert(conn)[1][3] == datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


async def test_resync_without_a_start_time_keeps_the_stored_one(tmp_path):
    batch = _batch(tmp_path)
    _drop_game_start_ts(batch)
    conn = FakeConn()
    await sync_batch(batch, conn)
    sql, args = _games_insert(conn)
    assert args[3] is None  # nothing to write, so the upsert must not null the column
    assert "started_at = coalesce(excluded.started_at, games.started_at)" in sql


async def test_start_game_still_resets_started_at():
    conn = FakeConn()

    async def connect():
        return conn

    await NeonSink("b1", connect=connect).start_game({"game_id": "g1", "models": []})
    assert "started_at = excluded.started_at" in _games_insert(conn)[0]


async def test_delete_games_reports_unknown_ids(tmp_path):
    batch = _batch(tmp_path)
    conn = MemConn()
    await sync_batch(batch, conn)
    n_events, n_labels = len(conn.events), len(conn.labels)
    res = await delete_games(conn, ["fixture-0001", "nope"])
    assert res == {
        "deleted": ["fixture-0001"],
        "missing": ["nope"],
        "events": n_events,
        "labels": n_labels,
    }
    assert not conn.games and not conn.events and not conn.labels


def test_sync_neon_delete_cli(monkeypatch):
    monkeypatch.setattr("societysim.cli.load_dotenv", lambda: None)
    monkeypatch.setenv("NEON_DATABASE_URL", "postgres://fake")
    conn = MemConn()
    conn.games["a"] = "b"
    conn.events[("a", 0)] = ("a", 0)

    async def connect():
        return conn

    monkeypatch.setattr("societysim.logging.sinks.neon.connect_neon", connect)
    res = CliRunner().invoke(app, ["sync-neon", "--delete", "a", "--delete", "zzz"])
    assert res.exit_code == 0, res.output
    assert "deleted 1 games, 1 events, 0 labels" in res.output
    assert "not found: zzz" in res.output
    assert not conn.games and conn.closed


@pytest.mark.parametrize("url", [None, "postgres://x"])
def test_sync_neon_cli_no_args_says_what_to_pass(monkeypatch, url):
    monkeypatch.delenv("NEON_DATABASE_URL", raising=False)
    if url:
        monkeypatch.setenv("NEON_DATABASE_URL", url)
    monkeypatch.setattr("societysim.cli.load_dotenv", lambda: None)
    res = CliRunner().invoke(app, ["sync-neon"])
    assert res.exit_code == 1
    assert res.output.strip() == "give a batch dir to sync, or --delete GAME_ID, or --prune"


def test_sync_neon_cli_not_a_directory(tmp_path, monkeypatch):
    monkeypatch.delenv("NEON_DATABASE_URL", raising=False)
    monkeypatch.setattr("societysim.cli.load_dotenv", lambda: None)
    res = CliRunner().invoke(app, ["sync-neon", str(tmp_path / "nope")])
    assert res.exit_code == 1
    assert f"not a directory: {tmp_path / 'nope'}" in res.output


# --- #417: reconnect after the connection dies ------------------------------------------------


class ClosableConn(FakeConn):
    """Fails like asyncpg once `dead` is set: every call raises `error`, is_closed() is True."""

    def __init__(self, error=None):
        super().__init__()
        self.dead = False
        self.error = error or asyncpg.InterfaceError(
            "cannot perform operation: connection is closed"
        )
        self.terminated = False

    def _check(self):
        if self.dead:
            raise self.error

    async def execute(self, sql, *args):
        self._check()
        await super().execute(sql, *args)

    async def executemany(self, sql, rows):
        self._check()
        await super().executemany(sql, rows)

    def is_closed(self):
        return self.dead

    def terminate(self):
        self.terminated = True


def _ends(conn: FakeConn) -> list[tuple]:
    return [a for sql, a in conn.executed if sql.startswith("update games set status")]


def _seqs(conn: FakeConn) -> list[int]:
    return [r[1] for _, rows in conn.many for r in rows]


@pytest.mark.parametrize(
    "error",
    [
        asyncpg.InterfaceError("cannot perform operation: connection is closed"),
        asyncpg.ConnectionDoesNotExistError("connection was closed in the middle of operation"),
        ConnectionResetError("reset by peer"),
    ],
)
async def test_sink_reconnects_after_the_connection_dies(error):
    conns = [ClosableConn(error), ClosableConn()]
    calls = []

    async def connect():
        calls.append(1)
        return conns[len(calls) - 1]

    sink = NeonSink("b1", connect=connect, batch_size=1, flush_after=60)
    await sink.start_game({"game_id": "g1", "models": []})
    await sink.write(_event(0))
    conns[0].dead = True
    for i in range(1, 6):
        await sink.write(_event(i))
    await sink.end_game("g1", "wolves")
    await sink.close()
    assert len(calls) == 2
    assert conns[0].terminated
    assert _seqs(conns[0]) == [0]
    assert _seqs(conns[1]) == [1, 2, 3, 4, 5]
    assert _ends(conns[1]) == [("g1", "finished", "wolves")]


async def test_reconnect_backs_off_while_neon_is_down(monkeypatch):
    monkeypatch.setattr(neon, "RECONNECT_BACKOFF", (0.2, 0.4))
    conn = FakeConn()
    calls = []
    down = True

    async def connect():
        calls.append(1)
        if down:
            raise OSError("no route")
        return conn

    sink = NeonSink("b1", connect=connect, batch_size=1, flush_after=60)
    await sink.start_game({"game_id": "g1", "models": []})  # fails: retry right away
    for i in range(20):
        await sink.write(_event(i))  # the first fails too; then the sink waits 0.2 s
    assert len(calls) == 2
    down = False
    await asyncio.sleep(0.25)
    await sink.write(_event(20))  # backoff over: reconnects and sends everything kept
    assert len(calls) == 3
    assert _seqs(conn) == list(range(21))
    # start_game never reached Neon, so its games row goes in before the events
    assert "insert into games" in conn.executed[0][0]
    await sink.close()


async def test_backoff_doubles_up_to_the_cap(monkeypatch):
    monkeypatch.setattr(neon, "RECONNECT_BACKOFF", (0.5, 30.0))

    async def connect():
        raise OSError("no route")

    sink = NeonSink("b1", connect=connect)
    waits = []
    for _ in range(10):
        now = asyncio.get_running_loop().time()
        with pytest.raises(OSError):
            await sink._conn_ready(force=True)
        waits.append(round(sink._retry_at - now, 1))
    assert waits == [0.0, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0, 30.0]


async def test_end_game_still_wins_over_close_after_a_failed_status_write():
    """end_game can't reach Neon; close() later can. The game is finished, not failed."""
    conns = [ClosableConn(), ClosableConn()]
    calls = []
    down = False

    async def connect():
        if down:
            raise OSError("no route")
        calls.append(1)
        return conns[len(calls) - 1]

    sink = NeonSink("b1", connect=connect, batch_size=100, flush_after=60)
    await sink.start_game({"game_id": "g1", "models": []})
    for i in range(3):
        await sink.write(_event(i))
    conns[0].dead, down = True, True
    await sink.end_game("g1", "wolves")
    down = False
    await sink.close()
    assert _seqs(conns[1]) == [0, 1, 2]
    assert _ends(conns[0]) + _ends(conns[1]) == [("g1", "finished", "wolves")]


async def test_a_query_error_keeps_the_connection():
    """Only connection and timeout errors replace the connection."""

    class BadRows(FakeConn):
        async def executemany(self, sql, rows):
            raise ValueError("invalid input syntax for type json")

    calls = []

    async def connect():
        calls.append(1)
        return BadRows()

    sink = NeonSink("b1", connect=connect, batch_size=1, flush_after=60)
    await sink.start_game({"game_id": "g1", "models": []})
    for i in range(3):
        await sink.write(_event(i))
    await sink.close()
    assert len(calls) == 1


# --- #420: a full buffer never loses an event queued during a flush -----------------------------


async def test_overflow_during_a_flush_keeps_the_unsent_event():
    class GatedConn(FakeConn):
        def __init__(self):
            super().__init__()
            self.gate = asyncio.Event()
            self.in_flight = asyncio.Event()

        async def executemany(self, sql, rows):
            self.in_flight.set()
            await self.gate.wait()
            await super().executemany(sql, rows)

    conn = GatedConn()

    async def connect():
        return conn

    sink = NeonSink("b1", connect=connect, batch_size=10**6, max_pending=3, flush_after=60)
    await sink.start_game({"game_id": "g1", "models": []})
    for i in range(3):
        await sink.write(_event(i))
    flush = asyncio.create_task(sink._flush())
    await conn.in_flight.wait()  # rows 0-2 are being sent
    await sink.write(_event(3))  # buffer full: seq 0 (already in flight) is trimmed
    assert sink.dropped == 1
    conn.gate.set()
    await flush
    assert [r[1] for r in sink._pending] == [3]
    await sink.close()
    assert _seqs(conn) == [0, 1, 2, 3]


# --- #418: sync_game is all or nothing ----------------------------------------------------------


async def test_failed_resync_leaves_the_finished_game_alone(tmp_path):
    batch = _batch(tmp_path)
    conn = MemConn()
    await sync_batch(batch, conn)
    assert conn.status == {"fixture-0001": "finished"}
    before = conn._snapshot()

    async def boom(sql, rows):
        raise TimeoutError("command_timeout on a 500-row chunk")

    conn.executemany = boom
    with pytest.raises(TimeoutError):
        await sync_batch(batch, conn)
    assert conn._snapshot() == before  # still finished, with its events and labels


# --- #419: a torn last line never aborts the batch ---------------------------------------------


def _write_game(d: Path, gid: str, keep: int | None = None, tail: str = "") -> None:
    lines = (FIXTURES / "sample_game.jsonl").read_text(encoding="utf-8").splitlines()[:keep]
    text = "\n".join(lines).replace("fixture-0001", gid) + "\n" + tail
    (d / f"{gid}.jsonl").write_text(text, encoding="utf-8")


async def test_sync_batch_skips_a_torn_last_line(tmp_path, caplog):
    d = tmp_path / "b"
    d.mkdir()
    _write_game(d, "g1", keep=-1, tail='{"schema_version":2,"game_id":"g1","seq":9')
    _write_game(d, "g2")
    labels = (FIXTURES / "sample_game.labels.jsonl").read_text(encoding="utf-8")
    (d / "g2.labels.jsonl").write_text(labels.replace("fixture-0001", "g2") + "[1,", "utf-8")
    conn = MemConn()
    totals = await sync_batch(d, conn)
    n = len(_events(FIXTURES / "sample_game.jsonl"))
    assert totals == {"games": 2, "events": 2 * n - 1, "labels": 8}
    assert conn.status == {"g1": "failed", "g2": "finished"}
    assert "g1.jsonl ends with a torn line, ignored" in caplog.text
    assert "g2.labels.jsonl ends with a torn line, ignored" in caplog.text


async def test_sync_game_still_refuses_a_bad_line_before_the_end(tmp_path):
    d = tmp_path / "b"
    d.mkdir()
    _write_game(d, "g1")
    lines = (d / "g1.jsonl").read_text(encoding="utf-8").splitlines()
    lines[3] = lines[3][:20]
    (d / "g1.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        await sync_game(d / "g1.jsonl", MemConn())
