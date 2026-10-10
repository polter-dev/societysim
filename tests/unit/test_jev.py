import json
import os
from pathlib import Path

import httpx
import pytest

from societysim.analysis import jev
from societysim.roles import Role

FIXTURE = Path(__file__).parents[2] / "fixtures" / "sample_game.jsonl"
N_STATEMENTS = 26


def fake_answers(body: dict) -> dict:
    """Answer like Jev would: accuse p2 only, claim seer, report p0 as a wolf."""
    answers = {}
    for key, q in body["questions"].items():
        if q["type"] == "noul":
            answers[key] = {
                "type": "noul",
                "noul": 0.9 if key in ("accuse_p2", "seer_report") else 0.1,
            }
        else:
            opts = list(q["criteria"])
            pick = {"claim_role": "seer", "seer_target": "p0", "seer_result": "wolf"}[key]
            pick = pick if pick in opts else opts[0]
            probs = {o: (1.0 if o == pick else 0.0) for o in opts}
            answers[key] = {
                "type": "choice",
                "choice": pick,
                "probabilities": probs,
                "confidence": 0.8,
            }
    return {
        "model": "jev-test",
        "answers": answers,
        "usage": {"input_tokens": 1, "output_tokens": 0},
    }


def mock_client(requests: list, fail_first: int = 0) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) <= fail_first:
            return httpx.Response(429)
        return httpx.Response(200, json=fake_answers(json.loads(request.content)))

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_classifies_fixture_end_to_end(tmp_path):
    requests: list[httpx.Request] = []
    async with mock_client(requests) as client:
        results = await jev.classify_game(FIXTURE, cache_dir=tmp_path, client=client)

    assert len(results) == len(requests) == N_STATEMENTS
    assert all(str(r.url) == jev.API_URL for r in requests)

    by_seq = {r.seq: r for r in results}
    finn = by_seq[17]  # Finn (p5) fake-claims Seer
    assert finn.actor == "p5"
    assert set(finn.accuse) == {"p0", "p1", "p2", "p3", "p4", "p6"}  # everyone but the speaker
    assert finn.accuse["p2"] == 0.9 and finn.accuse["p0"] == 0.1
    assert finn.claim_role is Role.SEER
    assert (finn.seer_report, finn.seer_target, finn.seer_result) == (0.9, "p0", "wolf")

    bodies = [json.loads(r.content) for r in requests]
    body = next(b for b in bodies if b["state"]["statement"].startswith("I'll be direct"))
    assert body["model"] == "jev-latest"
    assert "accuse_p5" not in body["questions"]
    assert body["state"]["speaker"] == "Finn (p5)"
    assert body["questions"]["claim_role"]["type"] == "choice"
    assert set(body["questions"]["claim_role"]["criteria"]) == {"none", *Role}
    assert set(body["questions"]["seer_result"]["criteria"]) == {"wolf", "not_wolf"}


async def test_cached_rerun_makes_no_calls(tmp_path, monkeypatch):
    requests: list[httpx.Request] = []
    async with mock_client(requests) as client:
        first = await jev.classify_game(FIXTURE, cache_dir=tmp_path, client=client)
    assert (tmp_path / "fixture-0001" / "17.json").exists()

    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    second = await jev.classify_game(FIXTURE, cache_dir=tmp_path)  # would raise on any miss
    assert second == first
    assert len(requests) == N_STATEMENTS


async def test_missing_key_and_cache_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="TYPESAFE_API_KEY"):
        await jev.classify_game(FIXTURE, cache_dir=tmp_path)


async def test_retries_rate_limit(tmp_path, monkeypatch):
    async def no_sleep(_):
        pass

    monkeypatch.setattr(jev.asyncio, "sleep", no_sleep)
    requests: list[httpx.Request] = []
    async with mock_client(requests, fail_first=2) as client:
        results = await jev.classify_game(FIXTURE, cache_dir=tmp_path, client=client, concurrency=1)
    assert len(results) == N_STATEMENTS
    assert len(requests) == N_STATEMENTS + 2


@pytest.mark.skipif(not os.environ.get("TYPESAFE_API_KEY"), reason="TYPESAFE_API_KEY unset")
async def test_real_jev_on_fixture(tmp_path):
    results = {r.seq: r for r in await jev.classify_game(FIXTURE, cache_dir=tmp_path)}
    assert len(results) == N_STATEMENTS
    finn = results[17]  # "I'm the Seer. I checked Ava last night and she is a wolf."
    assert finn.claim_role is Role.SEER
    assert finn.seer_report > 0.5
    assert (finn.seer_target, finn.seer_result) == ("p0", "wolf")
    assert finn.accuse["p0"] > 0.5
