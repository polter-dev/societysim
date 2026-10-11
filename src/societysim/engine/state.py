"""In-memory game state. All game randomness belongs to this state."""

import random
from collections.abc import Mapping
from dataclasses import dataclass, field

from societysim.config import GameConfig, Presentation
from societysim.roles import Phase, Role, SeerResult

# One pool per avatar presentation (ModelSpec.avatar_presentation), 10 names each so a game of
# one model still gets distinct names. Initials are distinct within a pool.
NAME_POOLS: dict[Presentation, tuple[str, ...]] = {
    "feminine": ("Ava", "Bea", "Cleo", "Dina", "Elif", "Gia", "Iris", "Lena", "Nora", "Rosa"),
    "masculine": ("Ben", "Dev", "Finn", "Hugo", "Jude", "Karl", "Leo", "Milo", "Omar", "Theo"),
    "neutral": ("Ash", "Blair", "Casey", "Drew", "Eden", "Jules", "Kai", "Quinn", "Remy", "Sky"),
}
# The names before #400, all still in the pools.
# (analysis/narrate.py pre-renders narrator lines for every name in NAME_POOLS.)
NAMES = ("Ava", "Ben", "Cleo", "Dev", "Elif", "Finn", "Gia", "Hugo", "Iris", "Jude")
# During night N, state.day is N; day N keeps the same value.
type NightNumber = int


@dataclass
class GameState:
    """Truth known to the engine; observations must filter it by role."""

    config: GameConfig
    # Model name -> avatar presentation; a model left out (scripted, human) gets neutral names.
    presentations: Mapping[str, Presentation] = field(default_factory=dict, repr=False)
    seats: tuple[str, ...] = field(init=False)
    names: dict[str, str] = field(init=False)
    models: dict[str, str] = field(init=False)
    roles: dict[str, Role] = field(init=False)
    alive: set[str] = field(init=False)
    day: int = 0  # Increment exactly once at each night start, never at day start.
    phase: Phase = Phase.SETUP
    t: int = 0
    last_doctor_protect: str | None = None
    last_doctor_protect_night: NightNumber | None = None
    seer_results: dict[str, list[tuple[NightNumber, str, SeerResult]]] = field(default_factory=dict)
    rng: random.Random = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.rng = random.Random(self.config.seed)
        self.seats = tuple(f"p{i}" for i in range(self.config.n_players))

        models = self.config.seat_models()
        self.models = dict(zip(self.seats, models, strict=True)) if models else {}

        # One shuffled index per seat, distinct, so two seats on one pool get different names.
        # The same single n-item shuffle as before #400: roles and speaking orders per seed are
        # unchanged, and the models never change the draws.
        order = list(range(self.config.n_players))
        self.rng.shuffle(order)
        self.names = {
            seat: NAME_POOLS[self.presentations.get(self.models.get(seat, ""), "neutral")][i]
            for seat, i in zip(self.seats, order, strict=True)
        }

        counts = self.config.roles
        role_list = (
            [Role.WOLF] * counts.wolves
            + [Role.SEER] * counts.seers
            + [Role.DOCTOR] * counts.doctors
            + [Role.VILLAGER] * counts.villagers
        )
        self.rng.shuffle(role_list)
        self.roles = dict(zip(self.seats, role_list, strict=True))
        self.alive = set(self.seats)
        self.seer_results = {seat: [] for seat in self.seats if self.roles[seat] is Role.SEER}

    def speaking_order(self) -> list[str]:
        """Draw a fresh seeded order of living speakers."""
        order = [seat for seat in self.seats if seat in self.alive]
        self.rng.shuffle(order)
        return order
