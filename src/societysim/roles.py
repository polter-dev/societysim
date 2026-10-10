"""Shared enums and small types: roles, teams, phases, seat ids."""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field


class Team(StrEnum):
    WOLVES = "wolves"
    VILLAGERS = "villagers"


class Role(StrEnum):
    WOLF = "wolf"
    SEER = "seer"
    DOCTOR = "doctor"
    VILLAGER = "villager"

    @property
    def team(self) -> Team:
        return Team.WOLVES if self is Role.WOLF else Team.VILLAGERS


class Phase(StrEnum):
    SETUP = "setup"  # game_start only
    NIGHT = "night"
    DAY = "day"  # discussion rounds
    VOTE = "vote"


# Seat ids are the only way events and responses refer to players: "p0".."p8".
SeatId = Annotated[str, Field(pattern=r"^p[0-8]$")]
SeerResult = Literal["wolf", "not_wolf"]
Winner = Literal["wolves", "villagers", "draw"]
