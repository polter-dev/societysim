import json
import os
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from societysim.analysis import jev
from societysim.cli import app
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


async def test_retries_timeout_with_explicit_timeout(tmp_path, monkeypatch):
    async def no_sleep(_):
        pass

    monkeypatch.setattr(jev.asyncio, "sleep", no_sleep)
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, json=fake_answers(json.loads(request.content)))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await jev.classify_game(FIXTURE, cache_dir=tmp_path, client=client, concurrency=1)
    assert len(results) == N_STATEMENTS
    assert len(requests) == N_STATEMENTS + 1
    assert requests[0].extensions["timeout"]["read"] == jev.TIMEOUT


async def test_timeout_gives_up_after_last_try(monkeypatch):
    async def no_sleep(_):
        pass

    monkeypatch.setattr(jev.asyncio, "sleep", no_sleep)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.ReadTimeout):
            await jev._post(client, {}, tries=2)


async def test_auth_error_raises_without_retry_or_cache(tmp_path):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(401)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.HTTPStatusError) as exc:
            await jev.classify_game(FIXTURE, cache_dir=tmp_path, client=client, concurrency=1)
    assert exc.value.response.status_code == 401
    assert len(requests) == N_STATEMENTS  # each statement tried once: 401 is not retried
    assert not any(tmp_path.rglob("*.json"))


async def test_connection_error_raises(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(httpx.ConnectError):
            await jev.classify_game(FIXTURE, cache_dir=tmp_path, client=client)


def test_cli_cache_is_under_batch_dir_whatever_the_cwd(tmp_path, monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "fake")
    monkeypatch.setattr("societysim.cli.load_dotenv", lambda: None)
    calls = []

    async def fake_post(client, body):
        calls.append(body)
        return fake_answers(body)

    monkeypatch.setattr(jev, "_post", fake_post)
    batch = tmp_path / "runs" / "b1"
    batch.mkdir(parents=True)
    (batch / "fixture-0001.jsonl").write_bytes(FIXTURE.read_bytes())
    (tmp_path / "elsewhere").mkdir()

    monkeypatch.chdir(tmp_path)
    assert CliRunner().invoke(app, ["label", "runs/b1"]).exit_code == 0
    assert len(calls) == N_STATEMENTS
    assert len(list((batch / ".cache" / "jev" / "fixture-0001").glob("*.json"))) == N_STATEMENTS

    monkeypatch.chdir(tmp_path / "elsewhere")
    result = CliRunner().invoke(app, ["label", "../runs/b1"])
    assert result.exit_code == 0, result.output
    assert len(calls) == N_STATEMENTS  # second run, other cwd: all cache hits
    assert not (tmp_path / "elsewhere" / ".cache").exists()


def test_claim_role_none_parses_as_no_claim():
    events = [jev.Event.model_validate_json(line) for line in FIXTURE.read_text().splitlines()]
    stmt = next(e for e in events if e.seq == 17)
    names = {seat: s.name for seat, s in events[0].meta.seats.items()}
    data = fake_answers(jev.build_request(stmt, names))
    data["answers"]["claim_role"].update(choice="none", confidence=0.7)
    result = jev.parse_response(stmt, data)
    assert result.claim_role is None
    assert result.claim_role_confidence == 0.7


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("TYPESAFE_API_KEY"), reason="TYPESAFE_API_KEY unset")
async def test_real_jev_on_fixture(tmp_path):
    results = {r.seq: r for r in await jev.classify_game(FIXTURE, cache_dir=tmp_path)}
    assert len(results) == N_STATEMENTS
    finn = results[17]  # "I'm the Seer. I checked Ava last night and she is a wolf."
    assert finn.claim_role is Role.SEER
    assert finn.seer_report > 0.5
    assert (finn.seer_target, finn.seer_result) == ("p0", "wolf")
    assert finn.accuse["p0"] > 0.5


def _stored(tmp_path, seq: int = 17) -> dict:
    return json.loads((tmp_path / "fixture-0001" / f"{seq}.json").read_text())


async def test_new_cache_entries_carry_a_fingerprint(tmp_path):
    async with mock_client([]) as client:
        await jev.classify_game(FIXTURE, cache_dir=tmp_path, client=client)
    assert _stored(tmp_path)["fingerprint"]


async def test_changed_statement_is_a_cache_miss_and_is_re_asked(tmp_path):
    requests: list[httpx.Request] = []
    async with mock_client(requests) as client:
        await jev.classify_game(FIXTURE, cache_dir=tmp_path, client=client)
        old = _stored(tmp_path)["fingerprint"]
        # a resumed game replays seq 17 with different text under the same game_id
        lines = FIXTURE.read_text(encoding="utf-8").splitlines()
        events = [json.loads(x) for x in lines if x]
        for e in events:
            if e["seq"] == 17:
                e["public_text"] = "A completely different thing, said after the resume."
        resumed = [jev.Event.model_validate(e) for e in events]
        requests.clear()
        results = await jev.classify_events(resumed, cache_dir=tmp_path, client=client)

    assert len(results) == N_STATEMENTS
    assert len(requests) == 1
    assert json.loads(requests[0].content)["state"]["statement"].startswith("A completely")
    assert _stored(tmp_path)["fingerprint"] != old  # entry now describes the new statement


async def test_changed_statement_without_a_key_is_skipped_with_a_warning(
    tmp_path, monkeypatch, capsys
):
    async with mock_client([]) as client:
        await jev.classify_game(FIXTURE, cache_dir=tmp_path, client=client)
    stale = tmp_path / "fixture-0001" / "17.json"
    stale.write_text(json.dumps({**_stored(tmp_path), "fingerprint": "someone-elses"}))

    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    results = await jev.classify_game(FIXTURE, cache_dir=tmp_path)

    assert len(results) == N_STATEMENTS - 1
    assert 17 not in {r.seq for r in results}
    assert "skipped" in capsys.readouterr().err


async def test_legacy_cache_entry_without_fingerprint_is_a_hit(tmp_path, monkeypatch, capsys):
    requests: list[httpx.Request] = []
    async with mock_client(requests) as client:
        first = await jev.classify_game(FIXTURE, cache_dir=tmp_path, client=client)
    for f in (tmp_path / "fixture-0001").glob("*.json"):
        data = json.loads(f.read_text())
        del data["fingerprint"]
        f.write_text(json.dumps(data))
    requests.clear()

    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    second = await jev.classify_game(FIXTURE, cache_dir=tmp_path)  # a miss would raise

    assert second == first and requests == []
    assert "no fingerprint" in capsys.readouterr().err
