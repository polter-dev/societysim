import asyncio
import json
import logging
from pathlib import Path

from jsonschema import Draft202012Validator

from societysim.logging.events import EventLogger
from societysim.logging.sinks.jsonl import JsonlSink

ROOT = Path(__file__).parents[2]
SCHEMA = json.loads((ROOT / "schema/events.schema.json").read_text())
EV = dict(t=0, day=0, phase="setup", type="game_start", visibility="narrator")


class Recorder:
    def __init__(self):
        self.events, self.calls = [], []

    async def start_game(self, run_meta):
        self.calls.append("start")

    async def write(self, event):
        self.events.append(event)

    async def end_game(self, game_id, winner):
        self.calls.append("end")

    async def close(self):
        self.calls.append("close")


class Raiser(Recorder):
    async def write(self, event):
        raise RuntimeError("boom")


class Slow(Recorder):
    async def write(self, event):
        await asyncio.sleep(3600)


async def test_jsonl_seq_schema_and_meta(tmp_path):
    logger = EventLogger("g1", [JsonlSink("b1", tmp_path)])
    await logger.start_game({"k": 1}, seed=7, models=["m"])
    for _ in range(3):
        logger.log(**EV)
    await logger.end_game("wolves")
    await logger.close()
    lines = (tmp_path / "b1/g1.jsonl").read_text().splitlines()
    validator = Draft202012Validator(SCHEMA)
    assert [json.loads(x)["seq"] for x in lines] == [0, 1, 2]
    for x in lines:
        validator.validate(json.loads(x))
    meta = json.loads((tmp_path / "b1/run_meta.json").read_text())
    assert meta["seed"] == 7 and meta["models"] == ["m"] and meta["config"] == {"k": 1}
    assert {"git_sha", "started_at"} <= meta.keys()


async def test_raising_sink_does_not_break_others(caplog):
    good = Recorder()
    logger = EventLogger("g", [Raiser(), good])
    with caplog.at_level(logging.ERROR):
        for _ in range(2):
            logger.log(**EV)
        await logger.close()
    assert [e.seq for e in good.events] == [0, 1]
    assert "boom" in caplog.text
    assert good.calls == ["close"]


async def test_slow_sink_does_not_block_and_drops(caplog):
    logger = EventLogger("g", [Slow()], queue_size=2)
    with caplog.at_level(logging.WARNING):
        for _ in range(10):
            logger.log(**EV)  # sync; would hang if it waited on the sink
    assert "dropping" in caplog.text
    await logger.close(timeout=0.1)
