"""Who may start a game. STUB: allows everything; #281 (security and limits) replaces it.

The interface is fixed: `await check_start(request, setup)` returns an Admission or raises
HTTPException (429 rate limited, 403 failed Turnstile or banned, 503 play disabled or out
of budget). Before calling it, the app sets `request.state.turnstile_token` and
`request.state.watch_only` from the POST /api/games body.
"""

from dataclasses import dataclass

from fastapi import Request

from societysim.setup import GameSetup


@dataclass(frozen=True)
class Admission:
    client: str  # who is starting the game (the client address), for per-client limits


async def check_start(request: Request, setup: GameSetup) -> Admission:
    return Admission(client=request.client.host if request.client else "unknown")
