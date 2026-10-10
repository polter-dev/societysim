"""Play server hardening (#281): config from env, client IP, Turnstile, errors, middleware.

Keys live only in the environment (HF Space secrets) and never reach a response or a log
line: errors carry a fixed code and a fixed message, unhandled exceptions become a generic
500 and are logged by type only, and startup logs secret names, never values. The server
fails closed: without TURNSTILE_SECRET_KEY and NEON_DATABASE_URL it refuses to start unless
PLAY_DEV_MODE=1 (local development only).
"""

import ipaddress
import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)

SITEVERIFY = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
TURNSTILE_TRANSPORT: httpx.AsyncBaseTransport | None = None  # tests swap in a mock
MAX_BODY = 16 * 1024
ORIGINS = ("https://society-sim.us", "https://www.society-sim.us", "https://societysim.pages.dev")
DEV_ORIGIN = "http://localhost:5173"
# Cheap cloud models. gpt-5-mini and gemini-flash cost more; add them with PLAY_ALLOWED_MODELS.
DEFAULT_ALLOWED = frozenset(
    {
        "qwen3-235b",
        "llama4-maverick",
        "gemma4-26b",
        "gpt-oss-120b",
        "deepseek-v3.1",
        "gemini-flash-lite",
        "gpt-5-nano",
    }
)
_SECRET_NAME = re.compile(r"(_KEY|_TOKEN|DATABASE_URL|WEBHOOK_URL)$")

type Network = ipaddress.IPv4Network | ipaddress.IPv6Network

ErrorCode = Literal[
    "rate_limited",
    "too_many_games_today",
    "active_game_exists",
    "at_capacity",
    "bot_check_failed",
    "forbidden",
    "budget_exhausted",
    "play_disabled",
    "invalid_request",
    "not_your_turn",
    "game_not_found",
    "unauthorized",
    "payload_too_large",
    "internal_error",
]
_DEFAULT_CODE: dict[int, ErrorCode] = {
    400: "invalid_request",
    401: "unauthorized",
    403: "forbidden",
    404: "game_not_found",
    405: "invalid_request",
    409: "not_your_turn",
    413: "payload_too_large",
    422: "invalid_request",
    429: "rate_limited",
    503: "at_capacity",
}


class Error(BaseModel):
    """Every error response: a fixed code, and a message safe to show the visitor."""

    model_config = ConfigDict(extra="forbid")

    error: ErrorCode
    message: str


class PlayError(StarletteHTTPException):
    def __init__(
        self, status: int, code: ErrorCode, message: str, headers: dict[str, str] | None = None
    ):
        super().__init__(status, message, headers)
        self.code = code


def error_response(
    status: int, code: ErrorCode, message: str, headers: Mapping[str, str] | None = None
) -> JSONResponse:
    return JSONResponse(
        Error(error=code, message=message).model_dump(), status_code=status, headers=headers
    )


def _env_int(env: Mapping[str, str], name: str, default: int) -> int:
    try:
        value = int(env.get(name) or default)
    except ValueError:
        raise RuntimeError(f"{name} must be a whole number") from None
    if value < 0:
        raise RuntimeError(f"{name} must not be negative")
    return value


def _env_float(env: Mapping[str, str], name: str, default: float) -> float:
    try:
        value = float(env.get(name) or default)
    except ValueError:
        raise RuntimeError(f"{name} must be a number") from None
    if not value >= 0:  # also rejects nan
        raise RuntimeError(f"{name} must not be negative")
    return value


@dataclass(frozen=True)
class PlayConfig:
    """Visitor play limits. Every field has an env var (from_env); the defaults are below."""

    dev_mode: bool = False  # PLAY_DEV_MODE=1: no Turnstile or Neon needed, localhost CORS
    turnstile_secret: str = field(default="", repr=False)  # TURNSTILE_SECRET_KEY
    rate_per_min: int = 120  # PLAY_RATE_PER_MIN: requests per minute per client IP
    max_active_per_ip: int = 1  # PLAY_MAX_ACTIVE_PER_IP
    max_games_per_ip_day: int = 5  # PLAY_MAX_GAMES_PER_IP_DAY (UTC day)
    max_concurrent: int = 4  # PLAY_MAX_CONCURRENT: running games, all visitors
    max_game_minutes: float = 45  # PLAY_MAX_GAME_MINUTES: then the game is ended
    daily_budget_usd: float = 2.0  # PLAY_DAILY_BUDGET_USD
    allowed_models: frozenset[str] = DEFAULT_ALLOWED  # PLAY_ALLOWED_MODELS, comma separated
    max_tokens: int = 2048  # PLAY_MAX_TOKENS: per model call
    trusted_proxies: tuple[Network, ...] = ()  # PLAY_TRUSTED_PROXIES, comma separated CIDRs
    space_host: str = ""  # SPACE_HOST (set by HF Spaces): its https origin is allowed
    on_space: bool = False  # SPACE_ID is set: running on HF Spaces, behind its proxy

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "PlayConfig":
        env = os.environ if env is None else env
        allowed = env.get("PLAY_ALLOWED_MODELS", "").strip()
        try:
            proxies = tuple(
                ipaddress.ip_network(p.strip())
                for p in env.get("PLAY_TRUSTED_PROXIES", "").split(",")
                if p.strip()
            )
        except ValueError:
            raise RuntimeError("PLAY_TRUSTED_PROXIES must be comma-separated CIDRs") from None
        return cls(
            dev_mode=env.get("PLAY_DEV_MODE") == "1",
            turnstile_secret=env.get("TURNSTILE_SECRET_KEY", ""),
            rate_per_min=_env_int(env, "PLAY_RATE_PER_MIN", cls.rate_per_min),
            max_active_per_ip=_env_int(env, "PLAY_MAX_ACTIVE_PER_IP", cls.max_active_per_ip),
            max_games_per_ip_day=_env_int(
                env, "PLAY_MAX_GAMES_PER_IP_DAY", cls.max_games_per_ip_day
            ),
            max_concurrent=_env_int(env, "PLAY_MAX_CONCURRENT", cls.max_concurrent),
            max_game_minutes=_env_float(env, "PLAY_MAX_GAME_MINUTES", cls.max_game_minutes),
            daily_budget_usd=_env_float(env, "PLAY_DAILY_BUDGET_USD", cls.daily_budget_usd),
            allowed_models=(
                frozenset(m.strip() for m in allowed.split(",") if m.strip())
                if allowed
                else DEFAULT_ALLOWED
            ),
            max_tokens=_env_int(env, "PLAY_MAX_TOKENS", cls.max_tokens),
            trusted_proxies=proxies,
            space_host=env.get("SPACE_HOST", "").strip(),
            on_space=bool(env.get("SPACE_ID")),
        )

    def origins(self) -> list[str]:
        out = list(ORIGINS)
        if self.space_host:
            out.append(f"https://{self.space_host}")
        if self.dev_mode:
            out.append(DEV_ORIGIN)
        return out


HF_PROXIES = "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"  # documented value for HF Spaces


def check_startup(play: PlayConfig, neon: bool) -> None:
    """Fail closed: production needs Turnstile and the Neon spend ledger, and on HF Spaces
    the proxy must be trusted, or every visitor would share the proxy's IP and its limits."""
    if play.on_space and not play.trusted_proxies:
        raise RuntimeError(
            "play server refuses to start on HF Spaces without PLAY_TRUSTED_PROXIES: every "
            f"visitor would share the proxy's IP. Set PLAY_TRUSTED_PROXIES={HF_PROXIES}"
        )
    if play.dev_mode:
        return
    required = {"TURNSTILE_SECRET_KEY": play.turnstile_secret, "NEON_DATABASE_URL": neon}
    missing = [name for name, ok in required.items() if not ok]
    if missing:
        raise RuntimeError(
            f"play server refuses to start without {' and '.join(missing)} "
            "(set PLAY_DEV_MODE=1 for local development only)"
        )


def secret_names(env: Mapping[str, str] | None = None) -> list[str]:
    """Names (never values) of the set env vars that look like secrets."""
    env = os.environ if env is None else env
    return sorted(k for k, v in env.items() if v and _SECRET_NAME.search(k))


def redact(text: str) -> str:
    """`text` with the value of every secret-looking env var (8+ characters) blanked out."""
    for name in secret_names():
        value = os.environ[name]
        if len(value) >= 8:
            text = text.replace(value, "[redacted]")
    return text


def _ip(text: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(text.strip())
    except ValueError:
        return None


def client_ip(request: Request, trusted: tuple[Network, ...]) -> str:
    """The visitor's address. Forwarding headers count only when the socket peer is a trusted
    proxy; X-Forwarded-For is read right to left, skipping trusted hops, so a client can't
    spoof it by sending its own. CF-Connecting-IP is used when there is no X-Forwarded-For."""
    peer = request.client.host if request.client else ""

    def is_trusted(addr) -> bool:
        return addr is not None and any(addr in net for net in trusted)

    if not is_trusted(_ip(peer)):
        return peer or "unknown"
    hops = [h for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
    for hop in reversed(hops):
        addr = _ip(hop)
        if addr is None:
            return peer
        if not is_trusted(addr):
            return str(addr)
    if hops:
        return str(_ip(hops[0]))
    cf = _ip(request.headers.get("cf-connecting-ip", ""))
    return str(cf) if cf else peer


async def verify_turnstile(token: str, ip: str, secret: str) -> bool:
    """Cloudflare's siteverify. Any failure (network, timeout, bad reply) is a no."""
    if not token:
        return False
    try:
        async with httpx.AsyncClient(timeout=5, transport=TURNSTILE_TRANSPORT) as http:
            data = {"secret": secret, "response": token, "remoteip": ip}
            r = await http.post(SITEVERIFY, data=data)
        return r.status_code == 200 and r.json().get("success") is True
    except Exception as exc:  # the message could hold the request, secret included
        log.warning("Turnstile check failed (%s)", type(exc).__name__)
        return False


# --- errors and middleware -------------------------------------------------------------------


async def _http_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code = getattr(exc, "code", None) or _DEFAULT_CODE.get(exc.status_code, "invalid_request")
    message = exc.detail if isinstance(exc, PlayError) else _GENERIC.get(code, "request failed")
    return error_response(exc.status_code, code, message, exc.headers)


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    # Field paths and pydantic's messages only; never the input (`input`, `ctx`).
    lines = [
        f"{'.'.join(str(p) for p in e['loc'] if p != 'body')}: {e['msg']}".lstrip(": ")
        for e in exc.errors()
    ]
    return error_response(422, "invalid_request", "; ".join(lines)[:1000] or "invalid request")


_GENERIC: dict[str, str] = {
    "invalid_request": "invalid request",
    "unauthorized": "bad or missing seat token",
    "forbidden": "forbidden",
    "game_not_found": "not found",
    "not_your_turn": "not your turn",
    "payload_too_large": "request body too large",
    "rate_limited": "too many requests; slow down",
    "at_capacity": "the server is busy; try again in a few minutes",
}


class CatchAll:
    """Outermost: an unhandled exception is logged by type only and becomes a generic 500,
    so no traceback or provider text reaches the client or the server log."""

    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        started = False

        async def tracking_send(message: dict) -> None:
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception as exc:
            log.error("unhandled %s on %s %s", type(exc).__name__, scope["method"], scope["path"])
            if not started:
                await error_response(500, "internal_error", "internal error")(scope, receive, send)


class SecurityHeaders:
    HEADERS = [
        (b"x-content-type-options", b"nosniff"),
        (b"referrer-policy", b"no-referrer"),
        (b"cache-control", b"no-store"),  # /me holds a seat's private view
    ]

    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        async def add_headers(message: dict) -> None:
            if message["type"] == "http.response.start":
                names = {k for k, _ in self.HEADERS}
                kept = [(k, v) for k, v in message.get("headers", []) if k.lower() not in names]
                message["headers"] = kept + self.HEADERS
            await send(message)

        await self.app(scope, receive, add_headers)


class BodyLimit:
    """Rejects a body over `limit` bytes with 413 while streaming: at once on Content-Length,
    and for a chunked body as soon as the bytes read pass the limit. The app then gets the
    body replayed from memory, so at most `limit` bytes are ever held."""

    def __init__(self, app: Any, limit: int = MAX_BODY):
        self.app, self.limit = app, limit

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        too_large = error_response(413, "payload_too_large", _GENERIC["payload_too_large"])
        headers = dict(scope.get("headers") or [])
        length = headers.get(b"content-length")
        if length is not None and (not length.isdigit() or int(length) > self.limit):
            return await too_large(scope, receive, send)
        chunks, size, more = [], 0, True
        while more:
            message = await receive()
            if message["type"] != "http.request":  # client went away
                return
            body = message.get("body", b"")
            size += len(body)
            if size > self.limit:
                return await too_large(scope, receive, send)
            chunks.append(body)
            more = message.get("more_body", False)
        replayed = False

        async def replay() -> dict:
            nonlocal replayed
            if replayed:
                return await receive()
            replayed = True
            return {"type": "http.request", "body": b"".join(chunks), "more_body": False}

        await self.app(scope, replay, send)


def install(app: FastAPI, play: PlayConfig) -> None:
    """Error handlers and middleware, outermost first: CatchAll, SecurityHeaders, CORS,
    BodyLimit, RateLimit. CORS sits outside the limits so a browser can read a 429 or 413."""
    from societysim.server.ratelimit import RateLimit

    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_middleware(RateLimit, play=play)  # add_middleware wraps: last added is outermost
    app.add_middleware(BodyLimit)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=play.origins(),
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
        max_age=600,
    )
    app.add_middleware(SecurityHeaders)
    app.add_middleware(CatchAll)
