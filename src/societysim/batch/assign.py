"""Balanced game assignments for a batch: which model sits in which seat, game by game.

Roles are dealt by the engine from the game seed, so they cannot be set directly; see
assign_games for how role balance is reached anyway.
"""

import hashlib
from collections import Counter

from pydantic import BaseModel

from societysim.config import BatchConfig, GameConfig
from societysim.engine.state import GameState

ATTEMPTS = 512  # candidate seeds tried per game to balance roles


class Assignment(BaseModel):
    game_id: str  # stable for (batch_id, index), so resume can skip finished games
    index: int
    seed: int
    models: list[str]  # model name for each seat p0..p{n-1}

    def game_config(self, template: GameConfig) -> GameConfig:
        return template.model_copy(update={"seed": self.seed, "models": self.models})


def derive_seed(batch_seed: int, index: int, attempt: int = 0) -> int:
    digest = hashlib.sha256(f"{batch_seed}:{index}:{attempt}".encode()).digest()
    return int.from_bytes(digest[:4], "big")


def game_id(batch_id: str, index: int) -> str:
    return f"{batch_id}-g{index:04d}"


def _role_cost(
    role_count: Counter[tuple[str, str]],
    models: list[str],
    seat_models: list[str],
    roles: list[str],
) -> tuple[int, int]:
    """(worst gap between models for any role, sum of squares) after adding this game."""
    after = role_count.copy()
    after.update(zip(seat_models, roles, strict=True))
    gaps, squares = 0, 0
    for r in set(roles):
        vals = [after[(m, r)] for m in models]
        gaps = max(gaps, max(vals) - min(vals))
        squares += sum(v * v for v in vals)
    return gaps, squares


def assign_games(cfg: BatchConfig) -> list[Assignment]:
    """One Assignment per game, deterministic in the batch config.

    Seats are a Latin square: game i seats models[(k + i) % m] at seat k, so every m games each
    model sits in every seat once. Roles are dealt by the engine from the seed, so to balance
    them we try a few seeds derived from (batch seed, game index, attempt) and keep the one
    that evens out each model's role counts. The seed stays a pure function of the config
    and the earlier games, so a longer batch repeats a shorter one's prefix.
    """
    n = cfg.game.n_players
    played: Counter[str] = Counter()  # games each model appears in, for model_game_limits
    role_count: Counter[tuple[str, str]] = Counter()
    out: list[Assignment] = []

    for i in range(cfg.n_games):
        avail = [m for m in cfg.models if played[m] < cfg.model_game_limits.get(m, i + 1)]
        if not avail:
            raise ValueError(f"model_game_limits leave no model for game {i}")
        m = len(avail)
        seat_models = (
            [avail[i % m]] * n if cfg.mode == "selfplay" else [avail[(k + i) % m] for k in range(n)]
        )

        seed, best = derive_seed(cfg.seed, i), None
        if cfg.mode == "mixed":
            for attempt in range(ATTEMPTS):
                cand = derive_seed(cfg.seed, i, attempt) if attempt else seed
                game_cfg = cfg.game.model_copy(update={"seed": cand, "models": []})
                roles = [r.value for r in GameState(game_cfg).roles.values()]
                cost = _role_cost(role_count, avail, seat_models, roles)
                if best is None or cost < best[0]:
                    best = (cost, cand, roles)
            assert best is not None
            _, seed, roles = best
            for x, r in zip(seat_models, roles, strict=True):
                role_count[(x, r)] += 1

        for x in set(seat_models):
            played[x] += 1
        out.append(
            Assignment(game_id=game_id(cfg.batch_id, i), index=i, seed=seed, models=seat_models)
        )
    return out
