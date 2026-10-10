import pytest
from fakes import FakeClient

from societysim.agents.schemas import TargetResponse, VoteResponse
from societysim.config import ModelSpec
from societysim.models.cache import CachedClient, CachedUsage, CacheMiss, cache_key

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
    assert isinstance(r.usage, CachedUsage) and r.usage.cached
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


async def test_failed_parse_round_trips(tmp_path):
    bad = FakeClient(script=["not json"])
    a = await CachedClient(bad, tmp_path, "record").complete(SPEC, MSGS, VoteResponse)
    b = await CachedClient(FakeClient(), tmp_path, "replay").complete(SPEC, MSGS, VoteResponse)
    assert a.parsed is None and b.parsed is None
    assert (a.raw, a.error) == (b.raw, b.error)


def test_key_depends_on_model_messages_schema_only():
    k = cache_key(SPEC, MSGS, VoteResponse)
    assert k == cache_key(SPEC.model_copy(update={"color": "#fff"}), MSGS, VoteResponse)
    assert k != cache_key(SPEC.model_copy(update={"name": "n"}), MSGS, VoteResponse)
    assert k != cache_key(SPEC, [{"role": "user", "content": "x"}], VoteResponse)
    assert k != cache_key(SPEC, MSGS, TargetResponse)
