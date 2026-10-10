"""GameManager: the running visitor games, each an asyncio task in this process.

A game runs like `societysim play --agents llm --neon`: the same Game and sinks, logs under
runs/visitor/ with run_meta `agents: llm` and the visitor's seat as model `human`, so it
stays out of the rankings (#172). The seed is always drawn by the server: a seed the visitor
picks would let them replay a known role deal.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from societysim.agents import human
from societysim.agents.base import Agent, AgentFactory
from societysim.agents.prompts import RESPONSE_MODELS
from societysim.agents.schemas import TargetResponse
from societysim.config import GameConfig
from societysim.engine.state import GameState
from societysim.logging.events import EventLogger
from societysim.logging.sinks.jsonl import JsonlSink
from societysim.orchestrator import Game
from societysim.server.ledger import Ledger
from societysim.server.web_agent import Forfeit, NotYourTurn, WebHumanAgent
from societysim.setup import REGISTRY, GameSetup, setup_errors

log = logging.getLogger(__name__)

MAX_TEXT = 280  # statement text and wolf chat message, after control characters are stripped
KEEP_FINISHED_S = 3600  # how long an ended game stays readable through /me

Status = Literal["running", "finished", "forfeit", "failed"]
# A game's agents for every seat but the visitor's, and model name -> viewer color.
type AgentsFor = Callable[[GameConfig], tuple[AgentFactory, Mapping[str, str | None]]]


@dataclass
class Settings:
    runs_dir: Path = Path("runs")
    batch_id: str = "visitor"  # logs go to runs_dir/batch_id/
    turn_timeout: float = 90
    max_timeouts: int = 3  # timeouts in a row that forfeit the game
    play_enabled: bool = True
    neon: bool = field(default_factory=lambda: bool(os.environ.get("NEON_DATABASE_URL")))
    agents: AgentsFor | None = None  # None: LLM agents from configs/models.yaml


@cache
def _registry():
    from societysim.models.registry import load_registry

    reg = load_registry(REGISTRY)
    reg.apply_env()
    return reg


@cache
def _client():
    from societysim.models.client import LiteLLMClient

    return LiteLLMClient(limits=_registry().limits())  # one client, so rate limits are shared


def llm_agents_for(cfg: GameConfig) -> tuple[AgentFactory, Mapping[str, str | None]]:
    from societysim.agents.llm_agent import llm_agents

    specs = _registry().resolve([m for m in cfg.seat_models() if m != human.MODEL])
    return llm_agents(_client(), {s.name: s for s in specs}), {s.name: s.color for s in specs}


def hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def clean_text(text: str) -> str:
    """Line breaks and tabs become spaces; other control and format characters go."""
    kept = "".join(c for c in text if c.isspace() or unicodedata.category(c) not in ("Cc", "Cf"))
    return " ".join(kept.split())


@dataclass
class Session:
    game_id: str
    game: Game
    logger: EventLogger
    seat: str | None  # None for a watch-only game
    agent: WebHumanAgent | None
    token_hash: bytes | None
    status: Status = "running"
    ended_at: float | None = None
    task: asyncio.Task | None = None

    def authorized(self, token: str) -> bool:
        return self.token_hash is not None and hmac.compare_digest(
            self.token_hash, hash_token(token)
        )


class GameManager:
    def __init__(self, settings: Settings, ledger: Ledger):
        self.settings, self.ledger = settings, ledger
        self.games: dict[str, Session] = {}
        self.neon_connect: Any = None  # set by prepare_neon() when Neon is reachable

    async def prepare_neon(self) -> None:
        """Create the Neon tables once; any problem means games run without live streaming."""
        from societysim.logging.sinks import neon

        try:
            conn = await neon.connect_neon()
            try:
                await neon.apply_schema(conn)
            finally:
                await conn.close()
        except Exception as exc:  # the message may hold the DSN, so log only the type
            log.warning("Neon unavailable (%s); not streaming live", type(exc).__name__)
            return
        self.neon_connect = neon.connect_neon

    def start(self, setup: GameSetup, watch_only: bool) -> tuple[Session, str | None]:
        """Start a game in the background. Returns the session and the visitor's seat token
        (None when watching). Raises ValueError for a setup this server can't run."""
        self._prune()
        data = setup.model_dump()
        data["seed"] = secrets.randbelow(2**31)
        if watch_only:
            if setup.human_seat is not None:
                raise ValueError("watch_only games have no human seat; set human_seat to null")
        elif setup.human_seat is None:
            if setup.seats is not None:
                raise ValueError("seats needs one seat with model 'human', or use watch_only")
            data["human_seat"] = "p0"
        try:
            setup = GameSetup.model_validate(data)
        except ValidationError as exc:
            raise ValueError("; ".join(setup_errors(exc))) from None

        s = self.settings
        cfg, seat = setup.game_config(), setup.human_seat
        game_id = f"{s.batch_id}-{secrets.token_hex(8)}"
        make_agent, colors = (s.agents or llm_agents_for)(cfg)
        sinks: list[Any] = [JsonlSink(s.batch_id, s.runs_dir)]
        if self.neon_connect:
            from societysim.logging.sinks.neon import NeonSink

            sinks.append(NeonSink(s.batch_id, connect=self.neon_connect, agents="llm"))
        agent = token = None
        if seat is not None:
            agent = WebHumanAgent(s.turn_timeout, s.max_timeouts)
            make_agent = _seat(make_agent, seat, agent)
            sinks = [human.MarkDefaults(sink, seat) for sink in sinks]
            token = secrets.token_urlsafe(32)  # 256 bits; only its hash is kept
        logger = EventLogger(game_id, sinks)
        game = Game(cfg, make_agent, logger, colors)
        session = Session(game_id, game, logger, seat, agent, token and hash_token(token))
        session.task = asyncio.create_task(self._run(session))
        self.games[game_id] = session
        return session, token

    async def _run(self, session: Session) -> None:
        game, cfg = session.game, session.game.state.config
        try:
            await session.logger.start_game(
                cfg.model_dump(mode="json"), cfg.seed, cfg.seat_models()
            )
            await session.logger.end_game(await game.run())
            session.status = "finished"
        except Forfeit:
            # ponytail: calls already running beside the visitor's turn (gather) finish
            # unobserved; they are seconds long next to the 90 s turn timeout.
            session.status = "forfeit"
        except asyncio.CancelledError:  # server shutdown
            session.status = "failed"
            raise
        except Exception:
            log.exception("game %s failed", session.game_id)
            session.status = "failed"
        finally:
            session.ended_at = time.time()
            await session.logger.close()
            self._tag(session)
            cost = sum(e.meta.usage.cost_usd for e in game.events if e.meta.usage)
            self.ledger.record(session.game_id, cost)

    def _tag(self, session: Session) -> None:
        """run_meta like `play --agents llm` writes it, plus `forfeit: true` for one."""
        path = self.settings.runs_dir / self.settings.batch_id / f"{session.game_id}.run_meta.json"
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
            meta["agents"] = "llm"
            if session.status == "forfeit":
                meta["forfeit"] = True
            path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        except (OSError, ValueError):
            log.warning("game %s: could not tag run_meta.json", session.game_id)

    def submit(self, session: Session, data: Any) -> None:
        """Validate the visitor's reply and hand it to the pending turn. Raises NotYourTurn,
        or ValueError with the reason the reply was rejected."""
        pending = session.agent.pending if session.agent else None
        if pending is None:
            raise NotYourTurn
        turn = pending.turn
        try:
            parsed = RESPONSE_MODELS.get(turn.action, TargetResponse).model_validate(data)
        except ValidationError as exc:
            raise ValueError("; ".join(setup_errors(exc))) from None
        act = parsed.action
        for name in ("text", "message"):  # statement text, wolf chat message
            if (text := getattr(act, name, None)) is not None:
                text = clean_text(text)
                if not 1 <= len(text) <= MAX_TEXT:
                    raise ValueError(f"{name} must be 1 to {MAX_TEXT} characters")
                act = act.model_copy(update={name: text})
        # Unrated players default to 0.5, as for the terminal human seat (meta.suspicion_default).
        rated = {e.player: e.p for e in parsed.private_reasoning.suspicion}
        extra = [p for p in rated if p not in turn.others]
        if extra:
            raise ValueError(f"suspicion must rate only {', '.join(turn.others)}")
        defaulted = len(rated) < len(turn.others)
        reasoning = {
            "thoughts": human.DEFAULTED if defaulted else human.THOUGHTS,
            "suspicion": [{"player": p, "p": rated.get(p, human.NEUTRAL)} for p in turn.others],
        }
        parsed = type(parsed).model_validate(
            {"private_reasoning": reasoning, "action": act.model_dump()}
        )
        if error := session.game._invalid(turn, parsed):  # the orchestrator's own check
            raise ValueError(error)
        session.agent.submit(parsed)

    def _prune(self) -> None:
        cutoff = time.time() - KEEP_FINISHED_S
        for gid in [g for g, s in self.games.items() if s.ended_at and s.ended_at < cutoff]:
            del self.games[gid]

    async def shutdown(self) -> None:
        tasks = [s.task for s in self.games.values() if s.task and not s.task.done()]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def _seat(make_agent: AgentFactory, seat: str, agent: Agent) -> AgentFactory:
    def make(state: GameState, s: str) -> Agent:
        return agent if s == seat else make_agent(state, s)

    return make
