"""Who may start a game: kill switch, Turnstile, model allowlist, game caps, spend cap.

`await check_start(request, setup)` returns an Admission or raises PlayError. The app sets
`request.state.turnstile_token` and `request.state.watch_only` from the POST /api/games body
first. After the last await below the checks run without yielding, and the app starts the
game right after, so two requests can't both take the last slot.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import cache

from fastapi import Request

from societysim.config import ModelSpec
from societysim.server import security
from societysim.server.security import PlayError
from societysim.setup import DEFAULT_LINEUP, HUMAN, REGISTRY, GameSetup

BUDGET_MESSAGE = "Daily play budget used up, try again tomorrow, or watch a replay"


@dataclass(frozen=True)
class Admission:
    client: str  # the client IP, for per-client limits
    setup: GameSetup  # the setup to run: the default lineup narrowed to allowed models


@cache
def _specs() -> dict[str, ModelSpec]:
    from societysim.models.registry import load_registry

    return {m.name: m for m in load_registry(REGISTRY).models}


def call_estimate(model: str) -> float:
    """Prior USD of one call to `model` (the batch runner's price prior); 0 if unknown."""
    from societysim.batch.runner import PRIOR_CALLS_PER_SEAT, prior_seat_cost

    spec = _specs().get(model)
    return prior_seat_cost(spec) / PRIOR_CALLS_PER_SEAT if spec else 0.0


PROMPT_OVERHEAD_TOKENS = 2000  # chat template, the response schema and the like


def call_worst_case(model: str, prompt_bytes: int, max_tokens: int) -> float:
    """The most one call can cost: a token per prompt byte (byte-level tokenizers never use
    more) plus overhead in, and the full output cap (the model's, at most `max_tokens`) out.
    A model the registry doesn't know can't be priced, so it is never called."""
    from societysim.models.client import estimate_cost

    spec = _specs().get(model)
    if spec is None:
        return float("inf")
    out = min(spec.max_tokens, max_tokens)
    return estimate_cost(spec, prompt_bytes + PROMPT_OVERHEAD_TOKENS, out)


def estimate_usd(setup: GameSetup) -> float:
    """Prior USD of a whole game: the batch price prior summed over its model seats."""
    from societysim.batch.runner import PRIOR_CALLS_PER_SEAT

    return sum(call_estimate(m) * PRIOR_CALLS_PER_SEAT for m in setup.seat_models() if m != HUMAN)


def allowed_setup(setup: GameSetup, allowed: frozenset[str]) -> GameSetup:
    """The default lineup is narrowed to the allowed models; a setup that names a model
    outside the allowlist is a 400."""
    if setup.seats is None and setup.model_pool is None:
        pool = [m for m in DEFAULT_LINEUP if m in allowed]
        if not pool:
            raise PlayError(503, "play_disabled", "no models are enabled for visitor games")
        if pool == DEFAULT_LINEUP:
            return setup
        data = setup.model_dump() | {"mode": "advanced", "model_pool": pool}
        return GameSetup.model_validate(data)
    named = {s.model for s in setup.seats} if setup.seats else set(setup.model_pool or ())
    if bad := sorted(named - allowed - {HUMAN}):
        raise PlayError(
            400,
            "invalid_request",
            f"not available in visitor games: {', '.join(bad)}; "
            f"allowed: {', '.join(sorted(allowed))}",
        )
    return setup


def default_estimate(allowed: frozenset[str]) -> float:
    try:
        return estimate_usd(allowed_setup(GameSetup.basic(human=True), allowed))
    except PlayError:
        return 0.0


def _until_midnight() -> str:
    now = datetime.now(UTC)
    midnight = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), UTC)
    return str(int((midnight - now).total_seconds()) + 1)


async def check_start(request: Request, setup: GameSetup) -> Admission:
    manager = request.app.state.manager
    settings, ledger = manager.settings, manager.ledger
    play = settings.play
    if not settings.play_enabled:
        raise PlayError(503, "play_disabled", "Play is switched off right now; watch a replay")
    ip = security.client_ip(request, play.trusted_proxies)

    if play.turnstile_secret:
        token = getattr(request.state, "turnstile_token", "")
        if not await security.verify_turnstile(token, ip, play.turnstile_secret, play.hostnames()):
            raise PlayError(403, "bot_check_failed", "Bot check failed; reload and try again")
    elif not play.dev_mode:  # check_startup prevents this; fail closed anyway
        raise PlayError(403, "bot_check_failed", "Bot check unavailable")

    setup = allowed_setup(setup, play.allowed_models)
    if not ledger.ready:
        if settings.neon and manager.neon_connect is None:
            await manager.prepare_neon()
        await ledger.load(manager.neon_connect)

    # No awaits from here to the game's start.
    if not ledger.ready:
        raise PlayError(503, "budget_exhausted", BUDGET_MESSAGE)
    running = [s for s in manager.games.values() if s.status == "running"]
    if len(running) >= play.max_concurrent:
        raise PlayError(503, "at_capacity", "All game tables are busy; try again in a few minutes")
    if sum(s.client == ip for s in running) >= play.max_active_per_ip:
        raise PlayError(429, "active_game_exists", "You already have a game running")
    if manager.started_today(ip) >= play.max_games_per_ip_day:
        raise PlayError(
            429,
            "too_many_games_today",
            f"That's {play.max_games_per_ip_day} games today; come back tomorrow",
            {"Retry-After": _until_midnight()},
        )
    if ledger.remaining_usd() < estimate_usd(setup):
        raise PlayError(503, "budget_exhausted", BUDGET_MESSAGE)
    return Admission(client=ip, setup=setup)
