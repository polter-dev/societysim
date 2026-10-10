"""In-memory game state. All game randomness belongs to this state."""

import random
from dataclasses import dataclass, field

from societysim.config import GameConfig
from societysim.roles import Phase, Role, SeerResult

NAMES = ("Ava", "Ben", "Cleo", "Dev", "Elif", "Finn", "Gia", "Hugo", "Iris", "Jude")
# During night N, state.day is N; day N keeps the same value.
type NightNumber = int


@dataclass
class GameState:
    """Truth known to the engine; observations must filter it by role."""

    config: GameConfig
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

        names = list(NAMES[: self.config.n_players])
        self.rng.shuffle(names)
        self.names = dict(zip(self.seats, names, strict=True))

        models = self.config.seat_models()
        self.models = dict(zip(self.seats, models, strict=True)) if models else {}

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
