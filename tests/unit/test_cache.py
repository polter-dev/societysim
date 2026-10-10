import json

import pytest
from fakes import FakeClient

from societysim.agents.llm_agent import llm_agents
from societysim.agents.schemas import TargetResponse, VoteResponse
from societysim.config import GameConfig, ModelSpec
from societysim.logging.sinks.jsonl import JsonlSink
from societysim.models.cache import CachedClient, CacheMiss, cache_key
from societysim.models.client import CompletionResult, Usage
from societysim.orchestrator import run_game

SPEC = ModelSpec(name="m", litellm_model="openai/x")
PLAYERS = ["p1", "p2", "p3"]
MSGS = [{"role": "user", "content": "vote"}]


async def _game(client, n=6):
    out = []
    for i in range(n):
        msgs = [{"role": "user", "content": f"turn {i}"}]
        r = await client.complete(SPEC, msgs, VoteResponse)
        out.append((r.raw, r.error, r.usage.model_dump_json(exclude={"cached"})))
    return out


async def test_record_then_replay_is_identical(tmp_path):
    rec = CachedClient(FakeClient(players=PLAYERS, seed=3), tmp_path, "record")
    first = await _game(rec)
    inner = FakeClient(players=PLAYERS, seed=99)
    second = await _game(CachedClient(inner, tmp_path, "replay"))
    assert first == second
    assert inner.calls == []


async def test_replay_marks_usage_cached_and_keeps_numbers(tmp_path):
    await CachedClient(FakeClient(players=PLAYERS), tmp_path, "record").complete(
        SPEC, MSGS, VoteResponse
    )
    r = await CachedClient(FakeClient(), tmp_path, "replay").complete(SPEC, MSGS, VoteResponse)
    assert r.usage.cached is True
    assert (r.usage.input_tokens, r.usage.output_tokens) == (100, 20)
    assert isinstance(r.parsed, VoteResponse)


async def test_replay_miss_is_error(tmp_path):
    with pytest.raises(CacheMiss):
        await CachedClient(FakeClient(), tmp_path, "replay").complete(SPEC, MSGS, VoteResponse)


async def test_record_missing_only_calls_inner_on_miss(tmp_path):
    inner = FakeClient(players=PLAYERS)
    c = CachedClient(inner, tmp_path, "record_missing")
    a = await c.complete(SPEC, MSGS, VoteResponse)
    b = await c.complete(SPEC, MSGS, VoteResponse)
    assert len(inner.calls) == 1 and a.raw == b.raw


async def test_record_always_calls_inner_and_off_writes_nothing(tmp_path):
    inner = FakeClient(players=PLAYERS)
    await CachedClient(inner, tmp_path, "record").complete(SPEC, MSGS, VoteResponse)
    await CachedClient(inner, tmp_path, "record").complete(SPEC, MSGS, VoteResponse)
    assert len(inner.calls) == 2
    off = tmp_path / "off"
    await CachedClient(inner, off, "off").complete(SPEC, MSGS, VoteResponse)
    assert not off.exists()


class _Seq:
    """Returns the given results in order and counts calls."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = 0

    async def complete(self, model, messages, response_model):
        self.calls += 1
        return self.results.pop(0)


def _timeout():
    return CompletionResult(parsed=None, raw="", error="Timeout: 60s", usage=Usage(model="m"))


@pytest.mark.parametrize("mode", ["record", "record_missing"])
async def test_error_results_are_not_cached(tmp_path, mode):
    good = await FakeClient(players=PLAYERS).complete(SPEC, MSGS, VoteResponse)
    inner = _Seq(_timeout(), good)
    c = CachedClient(inner, tmp_path, mode)
    assert (await c.complete(SPEC, MSGS, VoteResponse)).error == "Timeout: 60s"
    assert not list(tmp_path.glob("*.json"))
    ok = await c.complete(SPEC, MSGS, VoteResponse)
    assert inner.calls == 2 and ok.parsed is not None
    assert len(list(tmp_path.glob("*.json"))) == 1
    if mode == "record_missing":
        again = await c.complete(SPEC, MSGS, VoteResponse)
        assert inner.calls == 2 and again.usage.cached


async def test_bad_parse_is_not_cached(tmp_path):
    bad = FakeClient(script=["not json"])
    await CachedClient(bad, tmp_path, "record").complete(SPEC, MSGS, VoteResponse)
    assert not list(tmp_path.glob("*.json"))


async def test_existing_error_entry_is_a_miss(tmp_path):
    path = tmp_path / f"{cache_key(SPEC, MSGS, VoteResponse)}.json"
    entry = {"raw": "", "error": "Timeout: 60s", "parsed": False, "usage": {"model": "m"}}
    path.write_text(json.dumps(entry))
    with pytest.raises(CacheMiss):
        await CachedClient(FakeClient(), tmp_path, "replay").complete(SPEC, MSGS, VoteResponse)
    inner = FakeClient(players=PLAYERS)
    r = await CachedClient(inner, tmp_path, "record_missing").complete(SPEC, MSGS, VoteResponse)
    assert len(inner.calls) == 1 and r.parsed is not None
    assert json.loads(path.read_text())["error"] is None


def test_key_depends_on_model_messages_schema_only():
    k = cache_key(SPEC, MSGS, VoteResponse)
    assert k == cache_key(SPEC.model_copy(update={"color": "#fff"}), MSGS, VoteResponse)
    assert k != cache_key(SPEC.model_copy(update={"name": "n"}), MSGS, VoteResponse)
    assert k != cache_key(SPEC, [{"role": "user", "content": "x"}], VoteResponse)
    assert k != cache_key(SPEC, MSGS, TargetResponse)


def test_optional_endpoint_fields_preserve_existing_cache_key():
    import hashlib
    import json

    old_payload = {
        "model": SPEC.model_dump(
            mode="json",
            exclude={
                "color",
                "input_cost_per_mtok",
                "output_cost_per_mtok",
                "api_base",
                "api_key_env",
            },
        ),
        "messages": MSGS,
        "schema": VoteResponse.model_json_schema(),
    }
    old_blob = json.dumps(old_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert cache_key(SPEC, MSGS, VoteResponse) == hashlib.sha256(old_blob.encode()).hexdigest()


async def test_full_game_replays_byte_identical(tmp_path):
    config = GameConfig(seed=4, models=["m"])
    seats = [f"p{i}" for i in range(config.n_players)]

    async def play(client):
        game = await run_game(config, llm_agents(client, {"m": SPEC}), [], "g")
        # replay differs from the recording only in usage.cached, which is the point of it
        skip = {"meta": {"ts": True, "usage": {"cached"}}}
        return [e.model_dump_json(exclude=skip) for e in game.events]

    recorded = await play(CachedClient(FakeClient(players=seats, seed=1), tmp_path, "record"))
    inner = FakeClient(players=seats, seed=2)
    replayed = await play(CachedClient(inner, tmp_path, "replay"))
    assert len(recorded) > 50
    assert replayed == recorded
    assert inner.calls == []


async def test_replayed_game_logs_cached_usage_in_jsonl(tmp_path):
    config = GameConfig(seed=4, models=["m"])
    seats = [f"p{i}" for i in range(config.n_players)]
    cache = tmp_path / "cache"

    async def play(client, batch):
        sink = JsonlSink(batch, tmp_path / "runs")
        await run_game(config, llm_agents(client, {"m": SPEC}), [sink], "g")
        lines = (tmp_path / "runs" / batch / "g.jsonl").read_text().splitlines()
        events = [json.loads(x) for x in lines]
        return [e["meta"]["usage"]["cached"] for e in events if "usage" in (e["meta"] or {})]

    recorded = await play(CachedClient(FakeClient(players=seats, seed=1), cache, "record"), "rec")
    replayed = await play(CachedClient(FakeClient(players=seats), cache, "replay"), "rep")
    assert len(recorded) > 20
    assert len(replayed) == len(recorded)
    assert not any(recorded)
    assert all(replayed)
