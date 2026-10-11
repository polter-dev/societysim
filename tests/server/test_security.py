"""Security and abuse limits for visitor play (#281). In-process app, scripted or fake agents,
a fake Neon, a mocked Turnstile. No paid calls; the one real-Neon test needs
NEON_DATABASE_URL and skips without it."""

import asyncio
import contextlib
import hashlib
import ipaddress
import json
import logging
import os
import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from unittest import mock

import pytest

pytest.importorskip("fastapi", reason="needs the server extra: uv run --extra server pytest")

import httpx  # noqa: E402
from starlette.requests import Request  # noqa: E402
from test_play_api import play_env  # noqa: E402, F401  (autouse: dev-mode env)

from societysim.agents.base import Turn  # noqa: E402
from societysim.agents.scripted import RandomAgent  # noqa: E402
from societysim.config import GameConfig  # noqa: E402
from societysim.engine.state import GameState  # noqa: E402
from societysim.models.client import CompletionResult, Usage  # noqa: E402
from societysim.server import admission, security  # noqa: E402
from societysim.server import ledger as ledger_module  # noqa: E402
from societysim.server import manager as manager_module  # noqa: E402
from societysim.server.app import create_app  # noqa: E402
from societysim.server.ledger import (
    Ledger,  # noqa: E402
    nanos,  # noqa: E402
)
from societysim.server.manager import MeteredAgent, Settings, llm_agents_for  # noqa: E402
from societysim.server.security import PlayConfig  # noqa: E402
from societysim.setup import GameSetup  # noqa: E402

TIMEOUT = 30
SALT = "test-salt-0123456789abcdef"
SEED = 1  # p0 survives Night 1 and gets a turn
SITE = "https://society-sim.us"
DEV = "http://localhost:5173"
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
async def make_client(tmp_path, monkeypatch):
    # Fixed seed: a random one can kill the visitor on Night 1 before they get a turn.
    monkeypatch.setattr(manager_module.secrets, "randbelow", lambda n: SEED)
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
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            event_hooks=hooks,
            headers={"Origin": SITE},  # a POST without an allowed Origin is refused
        )
        client.app, client.manager = app, app.state.manager
        clients.append(client)
        return client

    yield make
    for client in clients:
        await client.manager.shutdown()
        await client.manager.ledger.close()
        await client.aclose()


@pytest.fixture
def turnstile(monkeypatch):
    """Cloudflare siteverify, mocked: the token "good" passes. Records each form it got."""
    forms = []

    def handler(request: httpx.Request) -> httpx.Response:
        form = dict(x.split("=", 1) for x in request.content.decode().split("&"))
        forms.append(form)
        ok = form.get("response") in ("good", "elsewhere")
        host = "evil.example" if form.get("response") == "elsewhere" else "society-sim.us"
        return httpx.Response(200, json={"success": ok, "hostname": host})

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


async def test_turnstile_token_from_another_site_is_refused(make_client, turnstile):
    """HF's proxy allows any CORS origin, so siteverify's hostname is what ties a token to
    our site: a token solved elsewhere (with our public site key) is a 403."""
    c = make_client(play=play(turnstile_secret="ts-secret"))
    r = await c.post("/api/games", json={"turnstile_token": "elsewhere", "watch_only": True})
    err(r, 403, "bot_check_failed")
    assert turnstile[-1]["response"] == "elsewhere"
    hosts = play(dev_mode=False).hostnames()
    assert hosts == {"society-sim.us", "www.society-sim.us", "societysim.pages.dev"}


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


def test_hf_space_needs_trusted_proxies(tmp_path):
    """On a Space every request comes through HF's proxy: without trusting it, all visitors
    would share one IP and one set of limits."""
    base = {"runs_dir": tmp_path, "agents": scripted, "neon": False}
    space = PlayConfig.from_env({"SPACE_ID": "polter-dev/societysim", "PLAY_DEV_MODE": "1"})
    with pytest.raises(RuntimeError, match=f"PLAY_TRUSTED_PROXIES={security.HF_PROXIES}"):
        create_app(Settings(**base, play=space))
    trusted = PlayConfig.from_env(
        {"SPACE_ID": "x/y", "PLAY_DEV_MODE": "1", "PLAY_TRUSTED_PROXIES": security.HF_PROXIES}
    )
    create_app(Settings(**base, play=trusted))
    hops = {"X-Forwarded-For": "198.51.100.20"}  # what HF's proxy sends
    assert security.client_ip(scope("10.16.4.2", hops), trusted.trusted_proxies) == "198.51.100.20"


# HF Spaces: the peer is HF's proxy (10.16.x.x in the Space logs), behind an AWS load balancer
# that appends the address it saw to X-Forwarded-For, after whatever the client sent.
HF_HEADERS = {
    "Host": "polter-dev-societysim-play.hf.space",
    "X-Forwarded-For": "198.51.100.20",
    "X-Forwarded-Proto": "https",
    "X-Forwarded-Port": "443",
    "X-Amzn-Trace-Id": "Root=1-00000000-000000000000000000000000",
}


def test_client_ip_behind_hf_proxy():
    hf = PlayConfig.from_env({"PLAY_TRUSTED_PROXIES": security.HF_PROXIES}).trusted_proxies
    ip = security.client_ip
    assert ip(scope("10.16.4.2", HF_HEADERS), hf) == "198.51.100.20"
    # A visitor's own X-Forwarded-For comes first in the chain, so it is never used.
    spoofed = HF_HEADERS | {"X-Forwarded-For": "6.6.6.6, 198.51.100.20"}
    assert ip(scope("10.16.4.2", spoofed), hf) == "198.51.100.20"
    # More private hops inside HF are skipped; a port on a hop is tolerated.
    chained = HF_HEADERS | {"X-Forwarded-For": "6.6.6.6, 198.51.100.20:51234, 10.16.0.9"}
    assert ip(scope("10.16.4.2", chained), hf) == "198.51.100.20"
    v6 = HF_HEADERS | {"X-Forwarded-For": "[2001:db8:1:2::5]:443"}
    assert ip(scope("10.16.4.2", v6), hf) == "2001:db8:1:2::"
    # The same spoofed header straight from a public peer: the peer is the client.
    assert ip(scope("203.0.113.1", spoofed), hf) == "203.0.113.1"


def test_ip_hint_masks_the_address():
    assert security.ip_hint("104.53.17.200") == "104.53.x.x"
    assert security.ip_hint("2001:db8:1:2::5") == "2001:0db8:x::"
    assert security.ip_hint("unknown") == "unknown"


@pytest.mark.parametrize("bad", ["%", "junk", "", "1.2.3", None, "fe80::zz%eth0"])
def test_ip_hint_never_raises_on_garbage(bad):
    assert security.ip_hint(bad) == "unknown"


def test_scoped_ipv6_hint_and_client_ip():
    assert security.ip_hint("fe80::1%eth0") == "fe80:0000:x::"
    assert security.ip_hint("[fe80::1%eth0]:80") == "fe80:0000:x::"
    trusted = (ipaddress.ip_network("10.0.0.0/8"),)
    xff = {"X-Forwarded-For": "2001:db8::7%eth0"}
    assert security.client_ip(scope("10.1.2.3", xff), trusted) == "2001:db8::"
    assert security.client_ip(scope("10.1.2.3", {"X-Forwarded-For": "%"}), trusted) == "10.1.2.3"


async def test_limits_show_the_derived_ip_masked(make_client):
    """The live check for #332: /api/limits echoes the IP the limits key on, masked."""
    local = play(trusted_proxies=(ipaddress.ip_network("127.0.0.0/8"),))  # stands in for HF
    c = make_client(play=local)
    hops = HF_HEADERS | {"X-Forwarded-For": "6.6.6.6, 104.53.1.2"}
    r = await c.get("/api/limits", headers=hops)
    assert r.json()["client_ip_hint"] == "104.53.x.x"
    assert "104.53.1.2" not in r.text
    untrusted = make_client(play=play())
    r = await untrusted.get("/api/limits", headers={"X-Forwarded-For": "104.53.1.2"})
    assert r.json()["client_ip_hint"] == "127.0.x.x"  # the peer, header ignored


async def test_limits_list_the_allowed_models(make_client):
    """#356: the viewer filters its model catalog to this list."""
    c = make_client(play=play(allowed_models=frozenset({"qwen3-235b", "gpt-5-nano"})))
    assert (await c.get("/api/limits")).json()["allowed_models"] == ["gpt-5-nano", "qwen3-235b"]
    r = await make_client(play=play()).get("/api/limits")
    assert r.json()["allowed_models"] == sorted(security.DEFAULT_ALLOWED)


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
        self.spend: list[tuple] = []  # (day, game_id, cost_usd, created_at)
        self.starts: dict[tuple, int] = {}  # (day, client_hash) -> games
        self.down = False
        self.fail_writes = 0  # the next N ledger writes fail
        self.lands = False  # ...after the row was written (a reply lost to a timeout)

    async def connect(self):
        if self.down:
            raise ConnectionError(f"could not reach {SECRETS['NEON_DATABASE_URL']}")
        return FakeConn(self)

    def write(self, sql, args):
        failing = self.fail_writes > 0
        self.fail_writes -= failing
        if failing and not self.lands:
            raise ConnectionError("connection reset")
        if "into visitor_spend" in sql:  # insert ... where not exists (game_id, created_at)
            if not any(r[1] == args[1] and r[3] == args[3] for r in self.spend):
                self.spend.append(args)
        else:  # upsert, keeping the greater count
            day, key, games = args
            self.starts[day, key] = max(self.starts.get((day, key), 0), games)
        if failing:
            raise TimeoutError


class FakeConn:
    def __init__(self, db: FakeNeon):
        self.db = db

    async def execute(self, sql, *args):
        if sql.startswith("delete from events"):
            self.db.events = {k: v for k, v in self.db.events.items() if k[0] != args[0]}
        elif sql.startswith("delete from visitor_starts where day < $1"):
            self.db.starts = {k: v for k, v in self.db.starts.items() if k[0] >= args[0]}
        elif "into visitor_spend" in sql or "into visitor_starts" in sql:
            self.db.write(sql, args)

    async def executemany(self, sql, rows):
        if "insert into events" in sql:
            for row in rows:
                self.db.events.setdefault((row[0], row[1]), json.loads(row[7]))

    async def fetchval(self, sql, *args):
        assert "from visitor_spend where day = $1" in sql
        return Decimal(str(sum(r[2] for r in self.db.spend if r[0] == args[0])))

    async def fetch(self, sql, *args):
        assert "from visitor_starts where day = $1" in sql
        rows = self.db.starts.items()
        return [{"client_hash": k, "games": n} for (day, k), n in rows if day == args[0]]

    def transaction(self):  # sync_game runs in one (#418)
        return contextlib.nullcontext()

    async def close(self):
        pass


async def test_ledger_persists_to_neon_and_fails_closed():
    db = FakeNeon()
    ledger = Ledger(2.0, persistent=True, ip_salt=SALT)
    assert ledger.remaining_usd() == 0 and ledger.remaining_today() == 0  # not loaded yet
    assert ledger.try_reserve(0.01) is None
    db.down = True
    assert not await ledger.load(db.connect)
    db.down = False
    assert await ledger.load(db.connect) and ledger.remaining_usd() == 2.0

    held = ledger.try_reserve(0.30)
    assert ledger.remaining_usd() == pytest.approx(1.70)
    await ledger.settle("g1", held, 0.25)  # the real cost; the rest of the hold is released
    held = ledger.try_reserve(0.10)
    await ledger.settle("g1", held, None)  # never came back: the hold is booked
    await ledger.record("g1", 0.40)  # at game end: 0.05 the metering missed
    assert ledger.spent_usd() == pytest.approx(0.40)
    assert [round(r[2], 6) for r in db.spend] == [0.25, 0.10, 0.05]
    assert ledger.try_reserve(1.61) is None and ledger.try_reserve(1.60) is not None
    assert ledger.spent_nanos == ledger.budget_nanos

    restarted = Ledger(2.0, persistent=True, ip_salt=SALT)  # a restart keeps today's spend
    await restarted.load(db.connect)
    assert restarted.remaining_usd() == pytest.approx(1.60)
    restarted.game_estimate_usd = 0.5
    assert restarted.remaining_today() == 3


async def test_ledger_needs_a_salt_to_persist():
    db = FakeNeon()
    for salt in ("", "short"):
        ledger = Ledger(2.0, persistent=True, ip_salt=salt)
        assert not await ledger.load(db.connect) and ledger.remaining_usd() == 0
    assert await Ledger(2.0, persistent=True, ip_salt=SALT).load(db.connect)


async def wait_ready(ledger: Ledger) -> None:
    async with asyncio.timeout(TIMEOUT):
        while not ledger.ready:
            await asyncio.sleep(0.005)


async def test_failed_spend_write_fails_closed_and_recovers(monkeypatch):
    """#382: a spend row Neon didn't take stops all paid calls and new games, until a retry
    writes it; then the ledger reloads, and nothing is counted twice."""
    monkeypatch.setattr(ledger_module, "RETRY_MIN_S", 0.01)
    db = FakeNeon()
    ledger = Ledger(2.0, persistent=True, ip_salt=SALT)
    assert await ledger.load(db.connect)
    first, second = ledger.try_reserve(0.30), ledger.try_reserve(0.20)  # two calls in flight
    db.fail_writes, db.down = 1, True  # the write fails, and Neon stays down for a while
    await ledger.settle("g1", first, 0.25)
    assert not ledger.ready and ledger.remaining_usd() == 0 and ledger.remaining_today() == 0
    assert ledger.try_reserve(0.01) is None  # running games fall back to scripted agents
    await ledger.settle("g1", second, 0.10)  # booked while down: kept for the retry
    await asyncio.sleep(0.05)  # a few retries, all failing
    assert not ledger.ready and db.spend == [] and len(ledger.unsaved) == 2
    db.down = False
    await wait_ready(ledger)
    assert [r[2] for r in db.spend] == [0.25, 0.10] and not ledger.unsaved
    assert ledger.spent_usd() == pytest.approx(0.35)
    restarted = Ledger(2.0, persistent=True, ip_salt=SALT)
    assert await restarted.load(db.connect)
    assert restarted.spent_usd() == pytest.approx(0.35)
    await ledger.close()


async def test_spend_row_that_landed_before_the_error_is_not_counted_twice():
    db = FakeNeon()
    ledger = Ledger(2.0, persistent=True, ip_salt=SALT)
    assert await ledger.load(db.connect)
    db.fail_writes, db.lands = 1, True  # written, then the reply was lost
    await ledger.settle("g1", ledger.try_reserve(0.30), 0.25)
    assert not ledger.ready and len(db.spend) == 1
    assert await ledger.load(db.connect)  # writes it again: a no-op
    assert len(db.spend) == 1 and ledger.spent_usd() == pytest.approx(0.25)
    restarted = Ledger(2.0, persistent=True, ip_salt=SALT)
    assert await restarted.load(db.connect) and restarted.spent_usd() == pytest.approx(0.25)
    await ledger.close()


def neon_client(make_client, db: FakeNeon, **kw):
    """A dev-mode app whose ledger persists to `db` and loads on the first start."""
    c = make_client(play=play(ip_salt=SALT, **kw))
    c.manager.ledger.persistent, c.manager.ledger.ready = True, False
    c.manager.neon_connect = db.connect
    return c


async def test_daily_cap_per_ip_survives_a_restart(make_client):
    db = FakeNeon()
    c = neon_client(make_client, db, max_games_per_ip_day=2)
    for _ in range(2):
        await finished(c, (await start(c, watch_only=True))["game_id"])
    await asyncio.gather(*c.manager.ledger._tasks)
    err(await c.post("/api/games", json={}), 429, "too_many_games_today")
    # Neon holds a salted hash per client, never the IP or a plain hash of it.
    ((day, key), games), *rest = db.starts.items()
    assert not rest and games == 2 and len(key) == 64
    assert key == c.manager.ledger.client_key("127.0.0.1")
    assert key != hashlib.sha256(b"127.0.0.1").hexdigest()

    restarted = neon_client(make_client, db, max_games_per_ip_day=2)  # same UTC day
    r = await restarted.post("/api/games", json={})
    err(r, 429, "too_many_games_today")
    assert restarted.manager.started_today("127.0.0.1") == 2
    assert restarted.manager.started_today("198.51.100.7") == 0


async def test_failed_start_count_write_refuses_games_until_written(make_client):
    db = FakeNeon()
    c = neon_client(make_client, db)
    db.fail_writes = 1
    await finished(c, (await start(c, watch_only=True))["game_id"])
    await asyncio.gather(*c.manager.ledger._tasks)
    assert not c.manager.ledger.ready and not db.starts
    db.down = True
    err(await c.post("/api/games", json={"watch_only": True}), 503, "budget_exhausted")
    db.down = False  # the next start reconnects, writes the count, then admits
    await start(c, watch_only=True)
    await asyncio.gather(*c.manager.ledger._tasks)
    assert list(db.starts.values()) == [2]


async def test_failed_call_books_its_worst_case_per_attempt():
    """A timed-out call reports $0 (models/client.py), but the provider may still bill it:
    the ledger books the hold for each attempt, never $0."""
    ledger, calls = Ledger(1.0), set()

    class Hangs:
        async def act(self, turn):
            usage = Usage(model="qwen3-235b", attempts=2, cost_usd=0.0)
            return CompletionResult(parsed=None, raw="", error="Timeout: took 90 s", usage=usage)

    state = GameState(GameConfig(n_players=7, models=["qwen3-235b"] * 7))
    agent = MeteredAgent(Hangs(), RandomAgent(state, "p1"), ledger, "g", lambda _: 0.0011, calls)
    turn = Turn(action="vote", prompt="vote", others=["p2"], targets=["p2"])
    result = await agent.act(turn)
    assert result.error == manager_module.TIMED_OUT
    assert ledger.games["g"] == pytest.approx(0.0022)
    assert ledger.spent_usd() == pytest.approx(0.0022)


async def test_game_across_midnight_is_not_booked_twice(monkeypatch):
    ledger = Ledger(1.0)
    await ledger.settle("g1", ledger.try_reserve(0.30), 0.25)
    tomorrow = ledger_module._today() + timedelta(days=1)
    monkeypatch.setattr(ledger_module, "_today", lambda: tomorrow)
    await ledger.settle("g1", ledger.try_reserve(0.30), 0.10)  # after midnight
    await ledger.record("g1", 0.35)  # game end: the metering missed nothing
    assert ledger.spent_usd() == pytest.approx(0.10)  # today's share only
    assert ledger.games["g1"] == pytest.approx(0.35)


async def test_start_refused_when_ledger_or_budget_is_out(make_client):
    c = make_client(play=play(daily_budget_usd=0.0))
    assert (await c.get("/api/limits")).json()["remaining_today"] == 0
    body = err(await c.post("/api/games", json={}), 503, "budget_exhausted")
    assert body["message"] == admission.BUDGET_MESSAGE

    c2 = make_client(play=play())
    c2.manager.ledger.persistent, c2.manager.ledger.ready = True, False  # Neon unreachable
    err(await c2.post("/api/games", json={"watch_only": True}), 503, "budget_exhausted")


COST = 0.0003  # per call: below every allowed model's worst case (1024 output tokens or more)


class CostlyAgent:
    """A model seat that costs COST per call (and may be slow); counts what happened."""

    log = {"done": 0, "cancelled": 0, "held": 0.0}

    def __init__(self, state, seat, delay=0.0):
        self.inner, self.delay = RandomAgent(state, seat), delay
        self.model = state.models[seat]

    async def act(self, turn) -> CompletionResult:
        worst = admission.call_worst_case(self.model, len(turn.prompt.encode()), 2048)
        assert worst >= COST
        try:
            await asyncio.sleep(self.delay)
        except asyncio.CancelledError:
            CostlyAgent.log["cancelled"] += 1
            CostlyAgent.log["held"] += worst
            raise
        result = await self.inner.act(turn)
        CostlyAgent.log["done"] += 1
        usage = result.usage.model_copy(update={"model": "costly", "cost_usd": COST})
        return result.model_copy(update={"usage": usage})


@pytest.fixture
def costly():
    CostlyAgent.log = {"done": 0, "cancelled": 0, "held": 0.0}
    return CostlyAgent.log


async def test_budget_hit_midgame_falls_back_to_scripted(make_client, tmp_path, costly):
    c = make_client(play=play(), agents=lambda cfg: (CostlyAgent, {}))
    started = await start(c, watch_only=True)
    ledger = c.manager.ledger
    ledger.budget_nanos = nanos(0.01)  # about 30 calls' worth, set once the game is running
    session = await finished(c, started["game_id"])
    assert session.status == "finished"
    log = events(tmp_path, started["game_id"])
    used = [e["meta"]["usage"]["model"] for e in log if e["meta"].get("usage")]
    assert "scripted" in used, "the cap never kicked in"
    assert ledger.spent_nanos <= ledger.budget_nanos  # exactly: worst cases are held first
    assert ledger.spent_usd() == pytest.approx(costly["done"] * COST)


async def test_concurrent_calls_and_games_never_pass_the_cap(make_client, costly):
    """Fifty calls race for a budget that fits five worst cases: five run, none overshoots."""
    ledger = Ledger(0.005)
    calls, ran = set(), []

    class Paid:
        async def act(self, turn):
            ran.append(turn)
            await asyncio.sleep(0.01)
            return await RandomAgent(state, "p1").act(turn)  # costs 0: settles below the hold

    state = GameState(GameConfig(n_players=7, models=["qwen3-235b"] * 7))
    agent = MeteredAgent(Paid(), RandomAgent(state, "p1"), ledger, "g", lambda _: 0.001, calls)
    turn = Turn(action="vote", prompt="vote", others=["p2"], targets=["p2"])
    replies = await asyncio.gather(*(agent.act(turn) for _ in range(50)))
    assert len(ran) == 5 and len(replies) == 50 and not calls
    assert ledger.spent_nanos == 0  # all five settled to their real cost

    # Several games started at once share one budget; the holds keep the total under it.
    c = make_client(play=play(), agents=lambda cfg: (CostlyAgent, {}))
    c.manager.ledger.budget_nanos = nanos(admission.estimate_usd(GameSetup()) + 0.002)
    games = await asyncio.gather(
        *(c.post("/api/games", json={"watch_only": True}) for _ in range(3))
    )
    assert [r.status_code for r in games] == [200, 200, 200]
    c.manager.ledger.budget_nanos = nanos(0.02)
    for r in games:
        await finished(c, r.json()["game_id"])
    assert c.manager.ledger.spent_nanos <= c.manager.ledger.budget_nanos
    assert costly["done"] * COST <= 0.02


async def test_forfeit_books_calls_in_flight(make_client, monkeypatch, costly):
    """p0 is a wolf (seed 7) and forfeits on night 1 while the Seer's and Doctor's slow calls
    run: they are cancelled, and their worst-case holds are booked."""
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
    assert booked == pytest.approx(costly["held"]) and booked > 0
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


async def test_post_needs_an_allowed_origin(make_client):
    """HF's proxy answers CORS preflights itself for any origin, so the server checks Origin
    on every POST: a missing or foreign one is 403 before any work is done."""
    prod = play(dev_mode=False, turnstile_secret="x", space_host="me.hf.space")
    c = make_client(play=prod, neon=True)
    for origin in ("https://evil.example", "null", DEV, None):
        if origin is None:
            del c.headers["Origin"]
        headers = {"Origin": origin} if origin else {}
        for path in ("/api/games", "/api/games/g1/action"):
            r = await c.post(path, json={"watch_only": True}, headers=headers)
            err(r, 403, "forbidden")
    assert not c.manager.games
    assert (await c.get("/api/limits", headers={"Origin": "https://evil.example"})).is_success
    dev = make_client(play=play())
    r = await dev.post("/api/games", json={"watch_only": True}, headers={"Origin": DEV})
    assert r.status_code == 200, r.text


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


class LeakyProvider:
    """A model seat whose provider errors echo the key, as a Gemini URL with ?key=... would.
    Its first call gets a bad reply plus such an error, the retry a bare provider error, and
    later calls answer normally. Keeps every prompt it is shown."""

    prompts: list[str] = []

    def __init__(self, state, seat):
        self.inner, self.model, self.calls = RandomAgent(state, seat), state.models[seat], 0

    async def act(self, turn) -> CompletionResult:
        LeakyProvider.prompts.append(turn.prompt)
        self.calls += 1
        key = SECRETS["GEMINI_API_KEY"]
        url = f"https://generativelanguage.googleapis.com/v1/models/x:generate?key={key}"
        usage = Usage(model=self.model)
        if self.calls == 1:
            return CompletionResult(
                parsed=None, raw=f'{{"echo": "{key}"}}', error=f"400 for {url}", usage=usage
            )
        if self.calls == 2:
            error = f"BadRequestError: GeminiException - {url} Authorization: Bearer {key}"
            return CompletionResult(parsed=None, raw="", error=error, usage=usage)
        return await self.inner.act(turn)


async def test_no_key_in_any_response_or_log(make_client, monkeypatch, turnstile, caplog, tmp_path):
    for name, value in SECRETS.items():
        monkeypatch.setenv(name, value)
    caplog.set_level(logging.DEBUG)
    seen: list[httpx.Response] = []
    LeakyProvider.prompts = []
    # Play config from env: dev mode, Turnstile on. Seats are leaky providers; Neon is a fake.
    c = make_client(seen=seen, agents=lambda cfg: (LeakyProvider, {}))
    db = FakeNeon()
    c.manager.neon_connect = db.connect
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
    # Provider error text reaches neither the game log, Neon (backfilled after game_end, where
    # the Data API serves it publicly) nor the retry prompt; only its category does.
    log_text = (tmp_path / "visitor" / f"{started['game_id']}.jsonl").read_text(encoding="utf-8")
    neon_text = json.dumps(list(db.events.values()))
    assert "LEAKCHECK" not in log_text and "LEAKCHECK" not in neon_text
    assert "provider_error" in log_text and "invalid_output: the reply" in log_text
    assert '"type": "invalid_output"' in neon_text  # the full log did reach Neon
    retries = [p for p in LeakyProvider.prompts if "previous reply was rejected" in p]
    assert retries and not [p for p in LeakyProvider.prompts if "LEAKCHECK" in p]


# --- schema ---------------------------------------------------------------------------------------


def test_visitor_spend_is_never_granted_to_anonymous():
    sql = (ROOT / "neon/schema.sql").read_text(encoding="utf-8")
    assert "create table if not exists visitor_spend" in sql
    grants = [line for line in sql.splitlines() if line.strip().startswith("grant")]
    assert grants and not [line for line in grants if "visitor_spend" in line]
    assert "revoke all on visitor_spend from anonymous" in sql
    assert "alter table visitor_spend enable row level security" in sql
    assert "create table if not exists visitor_starts" in sql
    assert not [line for line in grants if "visitor_starts" in line]
    assert "revoke all on visitor_starts from anonymous" in sql
    assert "alter table visitor_starts enable row level security" in sql


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("NEON_DATABASE_URL"), reason="NEON_DATABASE_URL not set")
async def test_real_neon_ledger_roundtrip():
    from societysim.logging.sinks.neon import apply_schema, connect_neon

    gid = f"it-ledger-{uuid.uuid4().hex[:8]}"
    conn = await connect_neon()
    try:
        await apply_schema(conn)
        first = Ledger(1000.0, persistent=True, ip_salt=SALT)
        assert await first.load(connect_neon)
        before = first.spent_usd()
        await first.settle(gid, 0.0, 0.0123)
        await first.close()
        again = Ledger(1000.0, persistent=True, ip_salt=SALT)
        assert await again.load(connect_neon)
        assert again.spent_usd() == pytest.approx(before + 0.0123, abs=1e-6)
        await again.close()
        if await conn.fetchval("select 1 from pg_roles where rolname = 'anonymous'"):
            sql = "select has_table_privilege('anonymous', 'visitor_spend', 'select')"
            assert await conn.fetchval(sql) is False
    finally:
        await conn.execute("delete from visitor_spend where game_id = $1", gid)
        await conn.close()


# --- Render: private hop, client in CF-Connecting-IP (PLAY_CLIENT_IP_HEADER) -------------------


def _render_play() -> PlayConfig:
    import pathlib

    import yaml

    root = pathlib.Path(__file__).resolve().parents[2]
    envs = yaml.safe_load((root / "render.yaml").read_text())["services"][0]["envVars"]
    return PlayConfig.from_env({e["key"]: e["value"] for e in envs if "value" in e})


def test_client_ip_behind_render_proxy():
    play = _render_play()
    assert play.client_ip_header == "cf-connecting-ip"

    def ip(peer, headers):
        return security.client_ip(scope(peer, headers), play.trusted_proxies, play.client_ip_header)

    # Render shape: peer is a 10.x hop, CF-Connecting-IP is the real client.
    real = {"CF-Connecting-IP": "203.0.113.50", "X-Forwarded-For": "203.0.113.50, 172.71.10.4"}
    assert ip("10.20.0.7", real) == "203.0.113.50"
    # A forged X-Forwarded-For (even naming a Cloudflare address) doesn't matter.
    forged = {"CF-Connecting-IP": "203.0.113.50", "X-Forwarded-For": "6.6.6.6, 104.28.1.1"}
    assert ip("10.20.0.7", forged) == "203.0.113.50"
    # A peer outside 10/8 (e.g. Cloudflare's own address) is not trusted: headers ignored.
    assert ip("104.28.1.1", forged) == "104.28.1.1"


def test_client_ip_header_is_opt_in():
    """With PLAY_CLIENT_IP_HEADER unset (HF Spaces), a client-sent CF-Connecting-IP is ignored."""
    space = PlayConfig.from_env({"PLAY_TRUSTED_PROXIES": security.HF_PROXIES})
    assert space.client_ip_header == ""
    hdr = {"CF-Connecting-IP": "6.6.6.6", "X-Forwarded-For": "198.51.100.20"}
    got = security.client_ip(scope("10.16.4.2", hdr), space.trusted_proxies, space.client_ip_header)
    assert got == "198.51.100.20"
    # And set but sent by an untrusted peer: ignored too.
    assert security.client_ip(
        scope("203.0.113.1", hdr), space.trusted_proxies, "cf-connecting-ip"
    ) == ("203.0.113.1")


def test_client_ip_buckets_ipv6_by_slash_64():
    ip = security.client_ip
    a, b = ip(scope("2001:db8:1:2::1"), ()), ip(scope("2001:db8:1:2:aaaa:bbbb:cccc:dddd"), ())
    assert a == b == "2001:db8:1:2::"
    assert ip(scope("2001:db8:1:3::1"), ()) != a
    assert ip(scope("203.0.113.5"), ()) == "203.0.113.5"
    assert security.ip_hint(a) == security.ip_hint("2001:db8:1:2::1")


def test_client_ip_keeps_ipv4_mapped_addresses_distinct():
    ip = security.client_ip
    assert ip(scope("::ffff:1.2.3.4"), ()) == "1.2.3.4"
    assert ip(scope("::ffff:5.6.7.8"), ()) == "5.6.7.8"


async def test_refused_start_does_not_burn_the_turnstile_token(make_client, turnstile):
    c = make_client(play=play(turnstile_secret="ts-secret", max_concurrent=1))
    await start(c, turnstile_token="good")
    assert len(turnstile) == 1
    err(await c.post("/api/games", json={"turnstile_token": "good"}), 503, "at_capacity")
    assert len(turnstile) == 1  # refused on capacity before siteverify
