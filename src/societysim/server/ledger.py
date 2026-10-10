"""Spend per game and what is left today. STUB: in-memory, never runs out; #281 replaces it.

The interface is fixed: `record(game_id, cost_usd)` is called once when a game ends,
however it ends, with the sum of its events' usage.cost_usd; `remaining_today()` is the
number of games that can still start today (GET /api/limits shows it, and the app refuses
POST /api/games with 503 at 0).
"""

from datetime import UTC, datetime

DAILY_GAMES = 1000


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


class Ledger:
    def __init__(self) -> None:
        self.spend: dict[str, tuple[str, float]] = {}  # game_id -> (UTC date, USD)

    def record(self, game_id: str, cost_usd: float) -> None:
        self.spend[game_id] = (_today(), cost_usd)

    def remaining_today(self) -> int:
        today = _today()
        return max(0, DAILY_GAMES - sum(day == today for day, _ in self.spend.values()))
