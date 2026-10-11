"""The Play API (docs/DATA_CONTRACT.md, "Play API"): start a game, read your seat, move.

    uvicorn --factory societysim.server.app:create_app

What a visitor sees of the game comes only from agents/observation.py (`observe`), the same
view a model in that seat gets. Every error is `{"error": <code>, "message": <text>}`
(security.Error); limits and hardening are in security.py, ratelimit.py and admission.py.
"""

import json
import logging
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Header, Request
from pydantic import BaseModel, ConfigDict, Field, RootModel
from pydantic.json_schema import models_json_schema

from societysim.agents.observation import observe
from societysim.agents.prompts import Action
from societysim.agents.schemas import AgentResponse
from societysim.roles import Phase, Role, SeatId, Winner
from societysim.server import admission, alerts, security
from societysim.server.ledger import Ledger
from societysim.server.manager import GameManager, Session, Settings, Status
from societysim.server.security import Error, PlayError
from societysim.server.web_agent import NotYourTurn
from societysim.setup import GameSetup

MAX_ACTION_BYTES = 8192

log = logging.getLogger(__name__)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StartGame(_Strict):
    """POST /api/games. The server draws the seed; human_seat defaults to p0 unless watching."""

    setup: GameSetup = Field(default_factory=GameSetup)
    turnstile_token: str = Field("", max_length=2048)
    watch_only: bool = False


class StartedGame(_Strict):
    game_id: str
    seat: SeatId | None  # null when watching
    seat_token: str | None  # send as "Authorization: Bearer <seat_token>"; null when watching


class PendingAction(_Strict):
    kind: Action  # which response shape to send (agents/schemas.py)
    targets: list[str]  # legal choices; a vote may also be "abstain"
    others: list[str]  # the players to rate in suspicion; unrated ones default to 0.5
    deadline: float  # unix seconds; after it the turn falls back


class Player(_Strict):
    seat: SeatId
    name: str
    alive: bool


class Me(_Strict):
    """GET /api/games/{id}/me: what the seat knows, nothing more."""

    game_id: str
    status: Status
    winner: Winner | None
    seat: SeatId
    name: str
    role: Role
    alive: bool
    day: int
    phase: Phase
    observation: str | None  # agents/observation.py's text; null once the seat is dead
    pending_action: PendingAction | None
    players: list[Player]  # public: names and who is alive


class Accepted(_Strict):
    accepted: Literal[True] = True


class Limits(_Strict):
    play_enabled: bool
    remaining_today: int
    client_ip_hint: str  # the caller's IP as the server sees it, masked: "104.53.x.x"


class Health(_Strict):
    ok: Literal[True] = True


class ActionBody(RootModel[AgentResponse]):
    """POST /api/games/{id}/action: the response shape for `pending_action.kind`.
    Statement text and wolf chat messages: 1 to 280 characters after control characters are
    stripped. `thoughts` is not logged."""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    play = settings.play
    try:
        security.check_startup(play, settings.neon)  # fails closed
    except Exception as exc:
        alerts.send(f"Play server failed to start: {type(exc).__name__}")  # type only
        raise
    ledger = Ledger(play.daily_budget_usd, persistent=settings.neon)
    watch = alerts.Alerts()
    ledger.on_spend = watch.spend
    ledger.game_estimate_usd = admission.default_estimate(play.allowed_models)
    manager = GameManager(settings, ledger)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        names = security.secret_names()
        log.info("play server: secrets set: %s", ", ".join(names) or "none")  # names only
        await watch.kill_switch(settings.play_enabled)
        if settings.neon:
            await manager.prepare_neon()
            await ledger.load(manager.neon_connect)
        yield
        await manager.shutdown()
        await ledger.close()

    app = FastAPI(title="societysim Play API", lifespan=lifespan)
    app.state.manager = manager
    security.install(app, play)

    def session_for(game_id: str, authorization: str | None) -> Session:
        session = manager.games.get(game_id)
        if session is None:
            raise PlayError(404, "game_not_found", "no such game")
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not session.authorized(token.strip()):
            raise PlayError(401, "unauthorized", "bad or missing seat token")
        return session

    @app.get("/api/health")
    async def health() -> Health:
        return Health()

    @app.get("/api/limits")
    async def limits(request: Request) -> Limits:
        await watch.kill_switch(settings.play_enabled)  # the uptime check polls this
        return Limits(
            play_enabled=settings.play_enabled,
            remaining_today=manager.ledger.remaining_today(),
            client_ip_hint=security.ip_hint(
                security.client_ip(request, play.trusted_proxies, play.client_ip_header)
            ),
        )

    @app.post("/api/games")
    async def start(body: StartGame, request: Request) -> StartedGame:
        request.state.turnstile_token = body.turnstile_token
        request.state.watch_only = body.watch_only
        admitted = await admission.check_start(request, body.setup)
        try:
            session, token = manager.start(admitted.setup, body.watch_only, admitted.client)
        except ValueError as exc:
            raise PlayError(422, "invalid_request", str(exc)) from None
        return StartedGame(game_id=session.game_id, seat=session.seat, seat_token=token)

    @app.get("/api/games/{game_id}/me")
    async def me(game_id: str, authorization: Annotated[str | None, Header()] = None) -> Me:
        s = session_for(game_id, authorization)
        st, seat = s.game.state, s.seat
        alive = seat in st.alive
        p = s.agent.pending
        return Me(
            game_id=game_id,
            status=s.status,
            winner=s.game.winner,
            seat=seat,
            name=st.names[seat],
            role=st.roles[seat],  # the seat's own role only
            alive=alive,
            day=st.day,
            phase=st.phase,
            observation=observe(st, s.game.events, seat) if alive else None,
            pending_action=p
            and PendingAction(
                kind=p.turn.action,
                targets=p.turn.targets,
                others=p.turn.others,
                deadline=p.deadline,
            ),
            players=[Player(seat=x, name=st.names[x], alive=x in st.alive) for x in st.seats],
        )

    @app.post("/api/games/{game_id}/action")
    async def action(
        game_id: str, request: Request, authorization: Annotated[str | None, Header()] = None
    ) -> Accepted:
        s = session_for(game_id, authorization)
        raw = await request.body()
        if len(raw) > MAX_ACTION_BYTES:  # security.BodyLimit already capped the read at 16 KB
            raise PlayError(413, "payload_too_large", f"body over {MAX_ACTION_BYTES} bytes")
        try:
            data = json.loads(raw)
        except ValueError:
            raise PlayError(422, "invalid_request", "body is not JSON") from None
        try:
            manager.submit(s, data)
        except NotYourTurn:
            raise PlayError(409, "not_your_turn", "not your turn") from None
        except ValueError as exc:
            raise PlayError(422, "invalid_request", str(exc)) from None
        return Accepted()

    return app


def play_api_json_schema() -> dict[str, Any]:
    """schema/play_api.schema.json (written by scripts/gen-schema.sh)."""
    from societysim.analysis.report import SCHEMA as CONTRACT_VERSION

    models = [
        (StartGame, "validation"),
        (StartedGame, "serialization"),
        (Me, "serialization"),
        (ActionBody, "validation"),
        (Accepted, "serialization"),
        (Limits, "serialization"),
        (Health, "serialization"),
        (Error, "serialization"),
    ]
    _, schema = models_json_schema(models, ref_template="#/$defs/{model}")
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "play_api.schema.json",
        "x-contract-version": CONTRACT_VERSION,
        "x-endpoints": {
            "POST /api/games": {"request": "StartGame", "response": "StartedGame"},
            "GET /api/games/{id}/me": {"auth": "seat_token", "response": "Me"},
            "POST /api/games/{id}/action": {
                "auth": "seat_token",
                "request": "ActionBody",
                "response": "Accepted",
            },
            "GET /api/limits": {"response": "Limits"},
            "GET /api/health": {"response": "Health"},
        },
        "x-error": "Error",
        **schema,
    }
