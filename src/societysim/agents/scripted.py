"""RandomAgent: an offline agent that picks legal actions at random. No API calls.

It draws only from its own random.Random, seeded from the game seed and the seat. It never
uses state.rng: votes and night calls run in parallel, so a shared RNG would make replays
depend on scheduling. invalid_rate makes it return unparseable replies at that rate, so the
orchestrator's retry and fallback paths run.
"""

import json
import random

from societysim.agents.base import Turn
from societysim.agents.prompts import RESPONSE_MODELS
from societysim.agents.schemas import TargetResponse
from societysim.engine.state import GameState
from societysim.models.client import CompletionResult, Usage

MODEL = "scripted"


class RandomAgent:
    def __init__(self, state: GameState, seat: str, invalid_rate: float = 0.0):
        self.rng = random.Random(f"{state.config.seed}-{seat}")
        self.invalid_rate = invalid_rate

    async def act(self, turn: Turn) -> CompletionResult:
        usage = Usage(model=MODEL)
        if self.invalid_rate and self.rng.random() < self.invalid_rate:
            return CompletionResult(
                parsed=None, raw="not json", error="scripted invalid reply", usage=usage
            )
        suspicion = [dict(player=s, p=round(self.rng.random(), 2)) for s in turn.others]
        reasoning = dict(thoughts="scripted", suspicion=suspicion)
        reply = dict(private_reasoning=reasoning, action=self._action(turn))
        # Any action without its own response model is target-style (e.g. lone_wolf).
        parsed = RESPONSE_MODELS.get(turn.action, TargetResponse).model_validate(reply)
        return CompletionResult(parsed=parsed, raw=json.dumps(reply), usage=usage)

    def _action(self, turn: Turn) -> dict:
        match turn.action:
            case "statement":
                accuse = [self.rng.choice(turn.targets)] if turn.targets else []
                return dict(
                    text="Nothing new from me.", accuse=accuse, claim_role=None, seer_report=None
                )
            case "vote":
                return dict(vote=self.rng.choice(turn.targets))
            case "wolf_chat":
                return dict(message="Agreed.", proposed_target=self.rng.choice(turn.targets))
            case _:  # seer, doctor, lone_wolf, and any other target-style action
                return dict(target=self.rng.choice(turn.targets))
