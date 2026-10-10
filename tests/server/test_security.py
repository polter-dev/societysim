"""Security and abuse limits for visitor play (#281). In-process app, scripted or fake agents,
a fake Neon, a mocked Turnstile. No paid calls; the one real-Neon test needs
NEON_DATABASE_URL and skips without it."""

import asyncio
import json
import logging
import os
import uuid
from decimal import Decimal
from pathlib import Path
from unittest import mock

import pytest

pytest.importorskip("fastapi", reason="needs the server extra: uv run --extra server pytest")

import httpx  # noqa: E402
from starlette.requests import Request  # noqa: E402
from test_play_api import play_env  # noqa: E402, F401  (autouse: dev-mode env)

from societysim.agents.scripted import RandomAgent  # noqa: E402
from societysim.config import GameConfig  # noqa: E402
from societysim.engine.state import GameState  # noqa: E402
from societysim.models.client import CompletionResult  # noqa: E402
from societysim.server import admission, security  # noqa: E402
from societysim.server.app import create_app  # noqa: E402
from societysim.server.ledger import Ledger  # noqa: E402
from societysim.server.manager import Settings, llm_agents_for  # noqa: E402
from societysim.server.security import PlayConfig  # noqa: E402
from societysim.setup import GameSetup  # noqa: E402

TIMEOUT = 30
ROOT = Path(__file__).resolve().parents[2]
SECRETS = {
    "OPENAI_API_KEY": "sk-LEAKCHECK-openai-7f3a",
    "HF_TOKEN": "hf_LEAKCHECK_tok_91bd",
    "GEMINI_API_KEY": "LEAKCHECK-gemini-22c0",
    "AMD_API_KEY": "LEAKCHECK-amd-5e11",
    "TURNSTILE_SECRET_KEY": "LEAKCHECK-turnstile-0x4AAA",
    "NEON_DATABASE_URL": "postgresql://owner:LEAKCHECK-pw-8d2e@ep-x.neon.tech/db",
}


def scripted(cfg):
    return RandomAgent, {}


def play(**kw) -> PlayConfig:
    """Dev mode with open limits, except the ones a test sets."""
    base = dict(
        dev_mode=True,
        rate_per_min=100_000,
        max_active_per_ip=100,
        max_games_per_ip_day=100,
        max_concurrent=100,
    )
    return PlayConfig(**base | kw)


@pytest.fixture
async def make_client(tmp_path):
    clients = []

    def make(seen: list | None = None, **kw):
        settings = Settings(**{"runs_dir": tmp_path, "agents": scripted, "neon": False} | kw)
        app = create_app(settings)
        hooks = {}
        if seen is not None:

            async def keep(response):
                await response.aread()
                seen.append(response)

            hooks = {"response": [keep]}
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", event_hooks=hooks
        )
        client.app, client.manager = app, app.state.manager
        clients.append(client)
        return client

    yield make
    for client in clients:
        await client.manager.shutdown()
        await client.aclose()


@pytest.fixture
def turnstile(monkeypatch):
    """Cloudflare siteverify, mocked: the token "good" passes. Records each form it got."""
    forms = []

    def handler(request: httpx.Request) -> httpx.Response:
        form = dict(x.split("=", 1) for x in request.content.decode().split("&"))
        forms.append(form)
        return httpx.Response(200, json={"success": form.get("response") == "good"})

    monkeypatch.setattr(security, "TURNSTILE_TRANSPORT", httpx.MockTransport(handler))
    return forms


def err(r: httpx.Response, status: int, code: str) -> dict:
    """The error body is exactly {"error": code, "message": text}."""
    assert r.status_code == status, r.text
    body = r.json()
    assert set(body) == {"error", "message"} and body["error"] == code, body
    assert isinstance(body["message"], str) and body["message"]
    return body


def auth(token):
    return {"Authorization": f"Bearer {token}"}


async def start(client, **body) -> dict:
    r = await client.post("/api/games", json=body)
    assert r.status_code == 200, r.text
    return r.json()


async def until(client, started, pred):
    url, headers = f"/api/games/{started['game_id']}/me", auth(started["seat_token"])
    async with asyncio.timeout(TIMEOUT):
        while True:
            r = await client.get(url, headers=headers)
            assert r.status_code == 200, r.text
            if pred(me := r.json()):
                return me
            await asyncio.sleep(0.002)


def move(pending):
    kind, targets = pending["kind"], pending["targets"]
    match kind:
        case "statement":
            action = {"text": "Hello.", "accuse": [], "claim_role": None, "seer_report": None}
        case "vote":
            action = {"vote": targets[0]}
        case "wolf_chat":
            action = {"message": "Them.", "proposed_target": targets[0]}
        case _:
            action = {"target": targets[0]}
    return {"private_reasoning": {"thoughts": "", "suspicion": []}, "action": action}


def waiting(me):
    return me["pending_action"] or me["status"] != "running"


async def play_out(client, started):
    while (me := await until(client, started, waiting))["status"] == "running":
        r = await client.post(
            f"/api/games/{started['game_id']}/action",
            json=move(me["pending_action"]),
            headers=auth(started["seat_token"]),
        )
        assert r.status_code == 200, r.text
    return me


async def finished(client, game_id):
    session = client.manager.games[game_id]
    await asyncio.wait_for(asyncio.shield(session.task), TIMEOUT)
    return session


def events(tmp_path, game_id):
    text = (tmp_path / "visitor" / f"{game_id}.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


def scope(peer: str, headers: dict[str, str] | None = None) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "client": (peer, 1), "headers": raw})


# --- rate limits and caps -------------------------------------------------------------------


async def test_request_rate_limit_gives_429_with_retry_after(make_client):
    c = make_client(play=play(rate_per_min=3))
    for _ in range(3):
        assert (await c.get("/api/health")).status_code == 200
    # A spoofed X-Forwarded-For from an untrusted peer is ignored: still the same client.
    r = await c.get("/api/health", headers={"X-Forwarded-For": "203.0.113.9"})
    err(r, 429, "rate_limited")
    assert int(r.headers["Retry-After"]) >= 1


def test_client_ip_trusts_forwarding_headers_only_from_proxies():
    proxies = PlayConfig.from_env({"PLAY_TRUSTED_PROXIES": "10.0.0.0/8, 172.16.0.0/12"})
    trusted = proxies.trusted_proxies
    xff = {"X-Forwarded-For": "6.6.6.6, 198.51.100.7, 172.16.0.4"}
    assert security.client_ip(scope("203.0.113.1", xff), trusted) == "203.0.113.1"
    # Right to left, skipping trusted hops: the client's own (spoofed) entry is never used.
    assert security.client_ip(scope("10.1.2.3", xff), trusted) == "198.51.100.7"
    cf = {"CF-Connecting-IP": "198.51.100.8"}
    assert security.client_ip(scope("10.1.2.3", cf), trusted) == "198.51.100.8"
    assert security.client_ip(scope("203.0.113.1", cf), trusted) == "203.0.113.1"
    assert security.client_ip(scope("10.1.2.3", {"X-Forwarded-For": "junk"}), trusted) == (
        "10.1.2.3"
    )


async def test_one_active_game_per_ip_and_daily_cap(make_client):
    c = make_client(play=play(max_active_per_ip=1, max_games_per_ip_day=2))
    first = await start(c)  # waits for the visitor's first turn, so it stays running
    err(await c.post("/api/games", json={}), 429, "active_game_exists")
    err(await c.post("/api/games", json={"watch_only": True}), 429, "active_game_exists")
    await until(c, first, lambda m: m["pending_action"])
    c.manager.games[first["game_id"]].task.cancel()
    await asyncio.gather(c.manager.games[first["game_id"]].task, return_exceptions=True)

    second = await start(c, watch_only=True)  # watch-only games count too
    await finished(c, second["game_id"])
    r = await c.post("/api/games", json={})
    err(r, 429, "too_many_games_today")
    assert 0 < int(r.headers["Retry-After"]) <= 86_401


async def test_global_concurrency_cap(make_client):
    c = make_client(play=play(max_concurrent=2))
    await start(c)
    await start(c)
    err(await c.post("/api/games", json={}), 503, "at_capacity")


async def test_game_is_ended_at_max_duration(make_client, tmp_path):
    c = make_client(play=play(max_game_minutes=0.001))  # 60 ms; the visitor never moves
    started = await start(c)
    session = await finished(c, started["game_id"])
    assert session.status == "failed"
    assert "game_end" not in {e["type"] for e in events(tmp_path, started["game_id"])}


async def test_kill_switch(make_client, monkeypatch):
    monkeypatch.setenv("PLAY_ENABLED", "0")
    c = make_client()
    assert (await c.get("/api/limits")).json()["play_enabled"] is False
    err(await c.post("/api/games", json={}), 503, "play_disabled")


# --- Turnstile and startup --------------------------------------------------------------------


async def test_turnstile_pass_and_fail(make_client, turnstile):
    c = make_client(play=play(turnstile_secret="ts-secret"))
    err(await c.post("/api/games", json={"turnstile_token": "bad"}), 403, "bot_check_failed")
    err(await c.post("/api/games", json={}), 403, "bot_check_failed")  # no token: no call
    assert len(turnstile) == 1
    await start(c, turnstile_token="good", watch_only=True)
    sent = turnstile[-1]
    assert sent["secret"] == "ts-secret" and sent["response"] == "good"
    assert sent["remoteip"] == "127.0.0.1"


async def test_turnstile_outage_fails_closed(make_client, monkeypatch):
    def down(request):
        raise httpx.ConnectError("unreachable")

    monkeypatch.setattr(security, "TURNSTILE_TRANSPORT", httpx.MockTransport(down))
    c = make_client(play=play(turnstile_secret="ts-secret"))
    err(await c.post("/api/games", json={"turnstile_token": "good"}), 403, "bot_check_failed")


def test_startup_fails_closed(tmp_path):
    base = {"runs_dir": tmp_path, "agents": scripted}
    with pytest.raises(RuntimeError, match="TURNSTILE_SECRET_KEY and NEON_DATABASE_URL"):
        create_app(Settings(**base, neon=False, play=PlayConfig()))
    with pytest.raises(RuntimeError, match="without NEON_DATABASE_URL"):
        create_app(Settings(**base, neon=False, play=PlayConfig(turnstile_secret="x")))
    create_app(Settings(**base, neon=True, play=PlayConfig(turnstile_secret="x")))
    create_app(Settings(**base, neon=False, play=PlayConfig(dev_mode=True)))
    with pytest.raises(RuntimeError, match="PLAY_MAX_CONCURRENT"):
        PlayConfig.from_env({"PLAY_MAX_CONCURRENT": "lots"})


# --- models -------------------------------------------------------------------------------------


async def test_model_allowlist(make_client):
    c = make_client(play=play())
    for setup in (
        {"mode": "advanced", "model_pool": ["qwen3-235b", "gpt-5-mini"]},
        {
            "mode": "advanced",
            "seats": [
                {"seat": f"p{i}", "model": "human" if i == 0 else "gemini-flash"} for i in range(10)
            ],
        },
    ):
        body = err(await c.post("/api/games", json={"setup": setup}), 400, "invalid_request")
        assert "gpt-5-mini" in body["message"] or "gemini-flash" in body["message"]
    # Basic mode keeps working: its default lineup is narrowed to the allowed models.
    started = await start(c, watch_only=True)
    models = set(c.manager.games[started["game_id"]].game.state.models.values())
    assert models and models <= security.DEFAULT_ALLOWED

    c2 = make_client(play=play(allowed_models=frozenset({"qwen3-235b", "gpt-5-mini"})))
    await start(c2, setup={"mode": "advanced", "model_pool": ["gpt-5-mini"]}, watch_only=True)


def test_max_tokens_cap():
    cfg = GameConfig(n_players=7, models=["qwen3-235b", "gpt-oss-120b"] * 3 + ["human"])
    with mock.patch.dict(os.environ):  # the registry sets provider env vars
        make, _ = llm_agents_for(cfg, max_tokens=300)
    state = GameState(cfg)
    capped = {make(state, s).spec.max_tokens for s in state.seats if state.models[s] != "human"}
    assert capped == {300}


# --- spend ledger -------------------------------------------------------------------------------


class FakeNeon:
    """Just enough of Postgres for the ledger, NeonSink and sync_game."""

    def __init__(self):
        self.events: dict[tuple[str, int], dict] = {}
        self.spend: list[tuple] = []
        self.down = False

    async def connect(self):
        if self.down:
            raise ConnectionError(f"could not reach {SECRETS['NEON_DATABASE_URL']}")
        return FakeConn(self)


class FakeConn:
    def __init__(self, db: FakeNeon):
        self.db = db

    async def execute(self, sql, *args):
        if sql.startswith("delete from events"):
            self.db.events = {k: v for k, v in self.db.events.items() if k[0] != args[0]}
        elif "insert into visitor_spend" in sql:
            self.db.spend.append(args)

    async def executemany(self, sql, rows):
        if "insert into events" in sql:
            for row in rows:
                self.db.events.setdefault((row[0], row[1]), json.loads(row[7]))

    async def fetchval(self, sql, *args):
        assert "from visitor_spend where day = $1" in sql
        return Decimal(str(sum(cost for day, _, cost in self.db.spend if day == args[0])))

    async def close(self):
        pass


async def test_ledger_persists_to_neon_and_fails_closed():
    db = FakeNeon()
    ledger = Ledger(2.0, persistent=True)
    assert ledger.remaining_usd() == 0 and ledger.remaining_today() == 0  # not loaded yet
    db.down = True
    assert not await ledger.load(db.connect)
    db.down = False
    assert await ledger.load(db.connect) and ledger.remaining_usd() == 2.0

    ledger.reserve(0.10)
    assert ledger.remaining_usd() == pytest.approx(1.90)
    await ledger.settle("g1", 0.10, 0.25)  # the real cost
    ledger.reserve(0.10)
    await ledger.settle("g1", 0.10, None)  # never came back: the reservation is booked
    await ledger.record("g1", 0.40)  # at game end: 0.05 the metering missed
    assert ledger.spent_usd() == pytest.approx(0.40)
    assert [round(cost, 6) for _, gid, cost in db.spend] == [0.25, 0.10, 0.05]

    restarted = Ledger(2.0, persistent=True)  # a restart keeps today's spend
    await restarted.load(db.connect)
    assert restarted.remaining_usd() == pytest.approx(1.60)
    restarted.game_estimate_usd = 0.5
    assert restarted.remaining_today() == 3


async def test_start_refused_when_ledger_or_budget_is_out(make_client):
    c = make_client(play=play(daily_budget_usd=0.0))
    assert (await c.get("/api/limits")).json()["remaining_today"] == 0
    body = err(await c.post("/api/games", json={}), 503, "budget_exhausted")
    assert body["message"] == admission.BUDGET_MESSAGE

    c2 = make_client(play=play())
    c2.manager.ledger.persistent, c2.manager.ledger.ready = True, False  # Neon unreachable
    err(await c2.post("/api/games", json={"watch_only": True}), 503, "budget_exhausted")


class CostlyAgent:
    """A model seat that costs `cost` per call (and may be slow); counts what happened."""

    log = {"done": 0, "cancelled": 0, "estimate": 0.0}

    def __init__(self, state, seat, cost=0.01, delay=0.0):
        self.inner, self.cost, self.delay = RandomAgent(state, seat), cost, delay
        self.estimate = admission.call_estimate(state.models[seat])

    async def act(self, turn) -> CompletionResult:
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            CostlyAgent.log["cancelled"] += 1
            CostlyAgent.log["estimate"] += self.estimate
            raise
        result = await self.inner.act(turn)
        CostlyAgent.log["done"] += 1
        usage = result.usage.model_copy(update={"model": "costly", "cost_usd": self.cost})
        return result.model_copy(update={"usage": usage})


@pytest.fixture
def costly():
    CostlyAgent.log = {"done": 0, "cancelled": 0, "estimate": 0.0}
    return CostlyAgent.log


async def test_budget_hit_midgame_falls_back_to_scripted(make_client, tmp_path, costly):
    estimate = admission.estimate_usd(
        admission.allowed_setup(GameSetup(), security.DEFAULT_ALLOWED)
    )
    budget = estimate + 0.05
    c = make_client(play=play(daily_budget_usd=budget), agents=lambda cfg: (CostlyAgent, {}))
    started = await start(c, watch_only=True)
    session = await finished(c, started["game_id"])
    assert session.status == "finished"
    log = events(tmp_path, started["game_id"])
    used = [e["meta"]["usage"]["model"] for e in log if e["meta"].get("usage")]
    assert "scripted" in used, "the cap never kicked in"
    assert "costly" not in used[used.index("scripted") + 10 :]  # at most a vote round in flight
    spent = c.manager.ledger.spent_usd()
    assert spent == pytest.approx(costly["done"] * 0.01)
    assert budget <= spent <= budget + 10 * 0.01


async def test_forfeit_books_calls_in_flight(make_client, monkeypatch, costly):
    """p0 is a wolf (seed 7) and forfeits on night 1 while the Seer's and Doctor's slow calls
    run: they are cancelled, and their reservations are booked."""
    monkeypatch.setattr("societysim.server.manager.secrets.randbelow", lambda n: 7)
    agents = lambda cfg: (lambda st, s: CostlyAgent(st, s, delay=5), {})  # noqa: E731
    c = make_client(play=play(), agents=agents, turn_timeout=0.2, max_timeouts=1)
    started = await start(c)
    me = await until(c, started, lambda m: True)
    assert me["role"] == "wolf"
    session = await finished(c, started["game_id"])
    assert session.status == "forfeit" and not session.calls
    assert costly["cancelled"] >= 1 and costly["done"] == 0
    booked = c.manager.ledger.games[started["game_id"]]
    assert booked == pytest.approx(costly["estimate"]) and booked > 0
    assert c.manager.ledger.spent_usd() == pytest.approx(booked)


# --- Neon: public only while running ------------------------------------------------------------


async def test_live_neon_feed_is_public_only_until_game_end(make_client, tmp_path):
    db = FakeNeon()
    c = make_client(play=play())
    c.manager.neon_connect = db.connect
    started = await start(c)
    gid = started["game_id"]
    snapshots = 0
    while (me := await until(c, started, waiting))["status"] == "running":
        live = [e for (g, _), e in db.events.items() if g == gid]
        snapshots += bool(live)
        for e in live:
            assert e["visibility"] == "public", e
            assert e.get("private_reasoning") is None and e.get("suspicion") is None, e
        r = await c.post(
            f"/api/games/{gid}/action",
            json=move(me["pending_action"]),
            headers=auth(started["seat_token"]),
        )
        assert r.status_code == 200
    await finished(c, gid)
    assert snapshots, "Neon never got live events"
    full = events(tmp_path, gid)
    assert [db.events[(gid, e["seq"])] for e in full] == full  # backfilled after game_end
    assert {"wolves", "narrator"} & {e["visibility"] for e in full}


# --- CORS, headers, body size, errors -----------------------------------------------------------


async def test_cors_allows_only_listed_origins(make_client):
    prod_play = play(dev_mode=False, turnstile_secret="x", space_host="me.hf.space")
    prod = make_client(play=prod_play, neon=True)
    for origin in ("https://society-sim.us", "https://societysim.pages.dev", "https://me.hf.space"):
        r = await prod.get("/api/limits", headers={"Origin": origin})
        assert r.headers["access-control-allow-origin"] == origin
        assert "access-control-allow-credentials" not in r.headers
    for origin in ("https://evil.example", "http://localhost:5173", "null"):
        r = await prod.get("/api/limits", headers={"Origin": origin})
        assert "access-control-allow-origin" not in r.headers
    pre = {"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"}
    assert (await prod.options("/api/games", headers=pre)).status_code == 400

    dev = make_client(play=play())
    r = await dev.get("/api/limits", headers={"Origin": "http://localhost:5173"})
    assert r.headers["access-control-allow-origin"] == "http://localhost:5173"


async def test_security_headers_and_body_limit(make_client):
    c = make_client(play=play())
    started = await start(c)
    r = await c.get(f"/api/games/{started['game_id']}/me", headers=auth(started["seat_token"]))
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["cache-control"] == "no-store"

    big = b"{" + b" " * (16 * 1024)
    err(await c.post("/api/games", content=big), 413, "payload_too_large")

    sent = 0

    async def chunks():  # no Content-Length: chunked, and far bigger than the limit
        nonlocal sent
        for _ in range(1000):
            sent += 1
            yield b" " * 1024

    r = await c.post("/api/games", content=chunks(), headers={"Content-Type": "application/json"})
    err(r, 413, "payload_too_large")
    assert sent <= 20, f"read {sent} KB of an oversized body"


async def test_every_error_is_a_code_and_a_safe_message(make_client, monkeypatch):
    c = make_client(play=play())
    started = await start(c)
    gid, token = started["game_id"], started["seat_token"]
    err(await c.get(f"/api/games/{gid}/me"), 401, "unauthorized")
    err(await c.get("/api/games/visitor-nope/me", headers=auth(token)), 404, "game_not_found")
    err(await c.get("/api/nowhere"), 404, "game_not_found")
    await until(c, started, lambda m: m["pending_action"])
    action = f"/api/games/{gid}/action"
    err(await c.post(action, content=b"nope", headers=auth(token)), 422, "invalid_request")
    err(await c.post(action, content=b"{" * 9000, headers=auth(token)), 413, "payload_too_large")
    # Validation errors name the field but never echo the input.
    body = err(
        await c.post("/api/games", json={"setup": {"n_players": "SNEAKYINPUT"}}),
        422,
        "invalid_request",
    )
    assert "SNEAKYINPUT" not in body["message"] and "n_players" in body["message"]

    me = await until(c, started, lambda m: m["pending_action"])
    assert (await c.post(action, json=move(me["pending_action"]), headers=auth(token))).is_success
    again = await c.post(action, json=move(me["pending_action"]), headers=auth(token))
    err(again, 409, "not_your_turn")

    def boom(*a, **kw):
        raise RuntimeError("provider said: bad key " + SECRETS["OPENAI_API_KEY"])

    monkeypatch.setattr(c.manager, "start", boom)
    body = err(await c.post("/api/games", json={}), 500, "internal_error")
    assert body["message"] == "internal error"

    # A bare HTTP 403 (no code given) still maps to a code.
    exc = security.StarletteHTTPException(403)
    r = await security._http_error(scope("1.2.3.4"), exc)
    assert json.loads(r.body) == {"error": "forbidden", "message": "forbidden"}


# --- keys never leave the server ----------------------------------------------------------------


async def test_no_key_in_any_response_or_log(make_client, monkeypatch, turnstile, caplog):
    for name, value in SECRETS.items():
        monkeypatch.setenv(name, value)
    caplog.set_level(logging.DEBUG)
    seen: list[httpx.Response] = []
    c = make_client(seen=seen)  # play config from env: dev mode, Turnstile on
    assert c.manager.settings.play.turnstile_secret == SECRETS["TURNSTILE_SECRET_KEY"]

    async with c.app.router.lifespan_context(c.app):
        startup = caplog.text
        assert "OPENAI_API_KEY" in startup and "TURNSTILE_SECRET_KEY" in startup

        started = await start(c, turnstile_token="good")
        await c.get("/api/limits")
        await c.get("/api/health")
        await c.get(f"/api/games/{started['game_id']}/me")  # 401
        await c.post("/api/games", json={"turnstile_token": "bad"})  # 403
        await c.post("/api/games", json={"setup": {"n_players": 3}, "turnstile_token": "good"})
        preflight = {"Origin": "https://society-sim.us", "Access-Control-Request-Method": "POST"}
        await c.options("/api/games", headers=preflight)
        assert (await play_out(c, started))["status"] == "finished"
        await finished(c, started["game_id"])

        def boom(*a, **kw):
            raise RuntimeError(f"upstream 401 for key {SECRETS['OPENAI_API_KEY']}")

        monkeypatch.setattr(c.manager, "start", boom)
        await c.post("/api/games", json={"turnstile_token": "good"})  # 500

    assert len(seen) > 20
    assert {r.status_code for r in seen} >= {200, 401, 403, 422, 500}
    for r in seen:
        text = r.content.decode("utf-8", "replace") + json.dumps(dict(r.headers))
        for name, value in SECRETS.items():
            assert value not in text, f"{name} leaked in a {r.status_code} from {r.url}"
            assert "LEAKCHECK" not in text
    assert "LEAKCHECK" not in caplog.text


# --- schema ---------------------------------------------------------------------------------------


def test_visitor_spend_is_never_granted_to_anonymous():
    sql = (ROOT / "neon/schema.sql").read_text(encoding="utf-8")
    assert "create table if not exists visitor_spend" in sql
    grants = [line for line in sql.splitlines() if line.strip().startswith("grant")]
    assert grants and not [line for line in grants if "visitor_spend" in line]
    assert "revoke all on visitor_spend from anonymous" in sql
    assert "alter table visitor_spend enable row level security" in sql


@pytest.mark.skipif(not os.environ.get("NEON_DATABASE_URL"), reason="NEON_DATABASE_URL not set")
async def test_real_neon_ledger_roundtrip():
    from societysim.logging.sinks.neon import apply_schema, connect_neon

    gid = f"it-ledger-{uuid.uuid4().hex[:8]}"
    conn = await connect_neon()
    try:
        await apply_schema(conn)
        first = Ledger(1000.0, persistent=True)
        assert await first.load(connect_neon)
        before = first.spent_usd()
        await first.settle(gid, 0.0, 0.0123)
        await first.close()
        again = Ledger(1000.0, persistent=True)
        assert await again.load(connect_neon)
        assert again.spent_usd() == pytest.approx(before + 0.0123, abs=1e-6)
        await again.close()
        if await conn.fetchval("select 1 from pg_roles where rolname = 'anonymous'"):
            sql = "select has_table_privilege('anonymous', 'visitor_spend', 'select')"
            assert await conn.fetchval(sql) is False
    finally:
        await conn.execute("delete from visitor_spend where game_id = $1", gid)
        await conn.close()
