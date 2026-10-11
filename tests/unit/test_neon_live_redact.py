"""#387: a visitor game's live Neon feed carries no per-call metadata (token counts, cost,
latency, timestamps), since longer role prompts would give wolves, seer and doctor away."""

import contextlib
import json
from pathlib import Path

from societysim.logging.events import Event
from societysim.logging.sinks.neon import LIVE_HIDDEN_META, NeonSink, sync_game

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "sample_game.jsonl"


class FakeConn:
    def __init__(self):
        self.rows: list[dict] = []

    async def execute(self, sql, *args):
        pass

    async def executemany(self, sql, rows):
        if "insert into events" in sql:
            self.rows += [json.loads(r[-1]) for r in rows]

    def transaction(self):  # sync_game runs in one (#418)
        return contextlib.nullcontext()

    async def close(self):
        pass


def _fixture() -> list[Event]:
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()
    return [Event.model_validate_json(x) for x in lines if x.strip()]


async def _stream(public_only: bool) -> list[dict]:
    conn = FakeConn()

    async def connect():
        return conn

    sink = NeonSink("b", connect=connect, public_only=public_only, flush_after=60)
    await sink.start_game({"game_id": "fixture-0001", "models": []})
    for e in _fixture():
        await sink.write(e)
    await sink.end_game("fixture-0001", "wolves")
    await sink.close()
    return conn.rows


async def test_fixture_public_events_carry_usage_and_ts():
    public = [e for e in _fixture() if e.visibility == "public"]
    assert any(e.meta.usage for e in public) and all(e.meta.ts for e in public)


async def test_public_only_feed_strips_per_call_meta():
    rows = await _stream(public_only=True)
    originals = {e.seq: e for e in _fixture()}
    assert rows and all(r["visibility"] == "public" for r in rows)
    for r in rows:
        assert not set(LIVE_HIDDEN_META) & set(r["meta"]), r
        assert r["private_reasoning"] is None and r["suspicion"] is None
        # Everything else in meta (rounds, claims, tallies) still goes out.
        kept = originals[r["seq"]].model_dump(mode="json")["meta"]
        assert r["meta"] == {k: v for k, v in kept.items() if k not in LIVE_HIDDEN_META}


async def test_full_feed_and_backfill_keep_usage(tmp_path):
    full = [e.model_dump(mode="json") for e in _fixture()]
    assert await _stream(public_only=False) == full
    log = tmp_path / "b" / "fixture-0001.jsonl"
    log.parent.mkdir()
    log.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    conn = FakeConn()
    await sync_game(log, conn)
    assert conn.rows == [json.loads(x) for x in log.read_text().splitlines() if x.strip()]
    assert any("usage" in r["meta"] for r in conn.rows)
