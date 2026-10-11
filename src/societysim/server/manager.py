"""GameManager: the running visitor games, each an asyncio task in this process.

A game runs like `societysim play --agents llm --neon`: the same Game and sinks, logs under
runs/visitor/ with run_meta `agents: llm` and the visitor's seat as model `human`, so it
stays out of the rankings (#172). The seed is always drawn by the server: a seed the visitor
picks would let them replay a known role deal.

Limits (#281): every model call is metered in the Ledger, and once today's budget is spent
the remaining AI turns go to scripted agents. A game runs at most PLAY_MAX_GAME_MINUTES.
When a game ends, however it ends, model calls still running are cancelled and booked.
Neon gets only public events, without reasoning or suspicion, while the game runs; the full
log is backfilled once it has reached game_end, with its rule-based labels (self-tags and
engine, as `label --no-jev`; no paid Jev call), which the global leaderboard reads (#344).
"""

import asyncio
import hashlib
import hmac
import itertools
import json
import logging
import os
import secrets
import time
import unicodedata
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from societysim.agents import human
from societysim.agents.base import Agent, AgentFactory
from societysim.agents.prompts import RESPONSE_MODELS
from societysim.agents.schemas import SuspicionEntry, TargetResponse
from societysim.agents.scripted import RandomAgent
from societysim.config import GameConfig, Presentation
from societysim.engine.state import GameState
from societysim.logging.events import EventLogger
from societysim.logging.sinks.jsonl import JsonlSink
from societysim.models.client import CompletionResult
from societysim.orchestrator import Game
from societysim.server import admission, security
from societysim.server.ledger import Ledger
from societysim.server.security import PlayConfig
from societysim.server.web_agent import Forfeit, NotYourTurn, WebHumanAgent
from societysim.setup import REGISTRY, GameSetup, setup_errors

log = logging.getLogger(__name__)

MAX_TEXT = 280  # statement text and wolf chat message, after control characters are stripped
NEON_SCHEMA_LOCK = 453_000_001  # pg_advisory_xact_lock key around the startup schema apply
KEEP_FINISHED_S = 3600  # how long an ended game stays readable through /me

Status = Literal["running", "finished", "forfeit", "failed"]


class HumanReasoning(BaseModel):
    """A visitor's optional scratchpad. `thoughts` is not logged; `suspicion` may rate any of
    the other living players, or none."""

    model_config = ConfigDict(extra="forbid")

    thoughts: str = ""
    suspicion: list[SuspicionEntry] = []


# A game's agents for every seat but the visitor's, and model name -> viewer color.
type AgentsFor = Callable[[GameConfig], tuple[AgentFactory, Mapping[str, str | None]]]


@dataclass
class Settings:
    runs_dir: Path = Path("runs")
    batch_id: str = "visitor"  # logs go to runs_dir/batch_id/
    turn_timeout: float = 90
    max_timeouts: int = 3  # timeouts in a row that forfeit the game
    play_enabled: bool = field(default_factory=lambda: os.environ.get("PLAY_ENABLED") != "0")
    neon: bool = field(default_factory=lambda: bool(os.environ.get("NEON_DATABASE_URL")))
    agents: AgentsFor | None = None  # None: LLM agents from configs/models.yaml
    play: PlayConfig = field(default_factory=PlayConfig.from_env)  # limits (#281)


# Restricted visitor-play keys -> the names litellm and models/client.py API_KEY_ENV read.
PLAY_KEYS = {
    "PLAY_OPENAI_API_KEY": ("openai", "OPENAI_API_KEY"),
    "PLAY_HF_TOKEN": ("huggingface", "HF_TOKEN"),
    "PLAY_GEMINI_API_KEY": ("gemini", "GEMINI_API_KEY"),
}


def map_play_keys(env: dict[str, str] | None = None) -> None:
    """Copies each PLAY_* key onto its provider name. PLAY_* wins when both are set, so the
    server uses the restricted play keys, not a broader key left in the environment."""
    env = os.environ if env is None else env
    for play, (_, name) in PLAY_KEYS.items():
        if env.get(play):
            env[name] = env[play]


def providers_ready(env: Mapping[str, str] | None = None) -> dict[str, bool]:
    env = os.environ if env is None else env
    return {provider: bool(env.get(name)) for _, (provider, name) in PLAY_KEYS.items()}


def startup_check() -> None:
    """Maps the play keys and logs one line per provider: presence only, never the value."""
    map_play_keys()
    for provider, ready in providers_ready().items():
        log.info("provider %s: key %s", provider, "present" if ready else "MISSING")


@cache
def _registry():
    from societysim.models.registry import load_registry

    map_play_keys()
    reg = load_registry(REGISTRY)
    reg.apply_env()
    return reg


@cache
def _presentations() -> dict[str, Presentation]:
    """Model -> avatar presentation, for seat names. A plain read: no env changes."""
    from societysim.models.registry import load_registry

    return {m.name: m.avatar_presentation for m in load_registry(REGISTRY).models}


@cache
def _client():
    import litellm

    from societysim.models.client import LiteLLMClient

    litellm.suppress_debug_info = True  # its help banner can print provider error text
    return LiteLLMClient(limits=_registry().limits())  # one client, so rate limits are shared


def llm_agents_for(
    cfg: GameConfig, max_tokens: int | None = None
) -> tuple[AgentFactory, Mapping[str, str | None]]:
    from societysim.agents.llm_agent import llm_agents

    specs = _registry().resolve([m for m in cfg.seat_models() if m != human.MODEL])
    if max_tokens is not None:
        specs = [s.model_copy(update={"max_tokens": min(s.max_tokens, max_tokens)}) for s in specs]
    return llm_agents(_client(), {s.name: s for s in specs}), {s.name: s.color for s in specs}


# What a failed model call may say in the log, in Neon and in the retry prompt: a category,
# never the provider's text, which can echo request details (a URL with ?key=..., headers).
INVALID_REPLY = "invalid_output: the reply was not JSON matching the required schema"
TIMED_OUT = "timeout: the model did not answer in time"
CALL_FAILED = "provider_error: the model call failed"


def scrub(result: CompletionResult) -> CompletionResult:
    """A failed call's error replaced by its category, and secrets blanked out of raw."""
    if result.error is None:
        return result
    if result.raw.strip():
        error = INVALID_REPLY
    elif result.error.startswith("Timeout:"):
        error = TIMED_OUT
    else:
        error = CALL_FAILED
        if "missing or invalid API key" in result.error:  # message names the model, not the key
            log.warning("model auth failure: %s", result.error)
    return result.model_copy(update={"error": error, "raw": security.redact(result.raw)})


class MeteredAgent:
    """Books each model call in the ledger. The call's worst-case cost is held first; if it
    doesn't fit in what is left today, a scripted agent answers instead (no spend), so the
    game still finishes. The hold settles to the real cost, or stays if the call never
    returns. A call that failed (the client reports $0 for a timeout) books the worst case
    for each attempt it made, so it is never under-counted. Failed calls are scrubbed of
    provider text."""

    def __init__(
        self,
        inner: Agent,
        fallback: Agent,
        ledger: Ledger,
        game_id: str,
        worst_case: Callable[[str], float],  # turn prompt -> most the call can cost
        calls: set[asyncio.Task],
    ):
        self.inner, self.fallback, self.ledger = inner, fallback, ledger
        self.game_id, self.worst_case, self.calls = game_id, worst_case, calls

    async def act(self, turn: Any) -> CompletionResult:
        held = self.ledger.try_reserve(self.worst_case(turn.prompt))
        if held is None:
            return await self.fallback.act(turn)
        call = asyncio.ensure_future(self._call(turn, held))
        self.calls.add(call)
        call.add_done_callback(self.calls.discard)
        return await call  # cancelling the game cancels the call too

    async def _call(self, turn: Any, held: int) -> CompletionResult:
        cost = None
        try:
            result = scrub(await self.inner.act(turn))
            cost = result.usage.cost_usd
            if result.error is not None:  # a timed-out or failed attempt may still be billed:
                cost = max(cost, held / 1e9 * result.usage.attempts)  # its worst case each
            return result
        finally:  # also when cancelled: then the hold is what gets booked
            await self.ledger.settle(self.game_id, held, cost)


def hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def clean_text(text: str) -> str:
    """Line breaks and tabs become spaces; control, format and surrogate characters go."""
    kept = "".join(
        c for c in text if c.isspace() or unicodedata.category(c) not in ("Cc", "Cf", "Cs")
    )
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
    client: str = "unknown"  # the starting client's IP, for per-client limits
    calls: set[asyncio.Task] = field(default_factory=set)  # model calls in flight

    def authorized(self, token: str) -> bool:
        return self.token_hash is not None and hmac.compare_digest(
            self.token_hash, hash_token(token)
        )


class GameManager:
    def __init__(self, settings: Settings, ledger: Ledger):
        self.settings, self.ledger = settings, ledger
        self.games: dict[str, Session] = {}
        self.neon_connect: Any = None  # set by prepare_neon() when Neon is reachable
        self.neon_backoff: tuple[float, ...] = (1, 2, 5, 15, 60)  # seconds; the last repeats
        self._neon_up = True  # False from the first failure until Neon is back
        self._neon_task: asyncio.Task | None = None  # the one background retry loop
        self._neon_on_ready: Callable[[], Awaitable[bool]] | None = None

    def started_today(self, client: str) -> int:
        """Games this client IP started today; kept in the ledger, so a restart keeps it."""
        return self.ledger.started_today(client)

    async def prepare_neon(self, on_ready: Callable[[], Awaitable[bool]] | None = None) -> None:
        """Create the Neon tables, then call `on_ready` (the ledger load; kept for later calls).
        If either fails, games run without live streaming and one background task keeps retrying
        with backoff (#453). While it runs, further calls (admission) leave it to that task."""
        if on_ready is not None:
            self._neon_on_ready = on_ready
        if self._neon_retrying() or await self._try_neon() or self._neon_retrying():
            return
        self._neon_task = asyncio.create_task(self._retry_neon())

    def _neon_retrying(self) -> bool:
        return self._neon_task is not None and not self._neon_task.done()

    async def _try_neon(self) -> bool:
        from societysim.logging.sinks import neon

        try:
            conn = await neon.connect_neon()
            try:
                # Two instances overlap during a deploy: one applies the schema at a time.
                async with conn.transaction():
                    await conn.execute("select pg_advisory_xact_lock($1)", NEON_SCHEMA_LOCK)
                    await neon.apply_schema(conn)
            finally:
                await conn.close()
        except Exception as exc:  # the message may hold the DSN, so log only the type
            if self._neon_up:
                log.warning("Neon unavailable (%s); not streaming live", type(exc).__name__)
            self._neon_up = False
            return False
        self.neon_connect = neon.connect_neon
        on_ready = self._neon_on_ready
        if on_ready and not await on_ready():  # the ledger logs its own failure
            self._neon_up = False
            return False
        if not self._neon_up:
            log.warning("Neon available again; streaming live")
        self._neon_up = True
        return True

    async def _retry_neon(self) -> None:
        for attempt in itertools.count():
            await asyncio.sleep(self.neon_backoff[min(attempt, len(self.neon_backoff) - 1)])
            if await self._try_neon():
                return

    def start(
        self, setup: GameSetup, watch_only: bool, client: str = "unknown"
    ) -> tuple[Session, str | None]:
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
        if s.agents:
            make_agent, colors = s.agents(cfg)
        else:
            make_agent, colors = llm_agents_for(cfg, s.play.max_tokens)
        calls: set[asyncio.Task] = set()
        make_agent = _metered(make_agent, self.ledger, game_id, calls, s.play.max_tokens)
        sinks: list[Any] = [JsonlSink(s.batch_id, s.runs_dir)]
        if self.neon_connect:
            from societysim.logging.sinks.neon import NeonSink

            sinks.append(
                NeonSink(s.batch_id, connect=self.neon_connect, agents="llm", public_only=True)
            )
        agent = token = None
        if seat is not None:
            agent = WebHumanAgent(s.turn_timeout, s.max_timeouts)
            make_agent = _seat(make_agent, seat, agent)
            sinks = [human.HumanSink(sink, seat) for sink in sinks]
            token = secrets.token_urlsafe(32)  # 256 bits; only its hash is kept
        logger = EventLogger(game_id, sinks)
        game = Game(cfg, make_agent, logger, colors, _presentations())
        session = Session(game_id, game, logger, seat, agent, token and hash_token(token))
        session.client, session.calls = client, calls
        session.task = asyncio.create_task(self._run(session))
        self.games[game_id] = session
        self.ledger.count_start(client)
        return session, token

    async def _run(self, session: Session) -> None:
        game, cfg = session.game, session.game.state.config
        try:
            async with asyncio.timeout(self.settings.play.max_game_minutes * 60):
                await session.logger.start_game(
                    cfg.model_dump(mode="json"), cfg.seed, cfg.seat_models()
                )
                winner = await game.run()
            await session.logger.end_game(winner)
            session.status = "finished"
        except TimeoutError:
            log.warning("game %s hit the time limit; ended", session.game_id)
            session.status = "failed"
        except Forfeit:
            session.status = "forfeit"
        except asyncio.CancelledError:  # server shutdown
            session.status = "failed"
            raise
        except Exception as exc:  # by type only: provider text can echo request details
            log.error("game %s failed (%s)", session.game_id, type(exc).__name__)
            session.status = "failed"
        finally:
            session.ended_at = time.time()
            # Calls still running (beside a forfeited turn, say) are cancelled and booked.
            for call in list(session.calls):
                call.cancel()
            await asyncio.gather(*session.calls, return_exceptions=True)
            await session.logger.close()
            self._tag(session)
            if session.status == "finished":
                self._label(session)
                await self._backfill(session)
            cost = sum(e.meta.usage.cost_usd for e in game.events if e.meta.usage)
            await self.ledger.record(session.game_id, cost)

    async def _backfill(self, session: Session) -> None:
        """The full log to Neon, now that the game is over (live, it got public events only)."""
        if not self.neon_connect:
            return
        from societysim.logging.sinks import neon

        path = self.settings.runs_dir / self.settings.batch_id / f"{session.game_id}.jsonl"
        try:
            conn = await self.neon_connect()
            try:
                await neon.sync_game(path, conn)
            finally:
                await conn.close()
        except Exception as exc:
            log.warning("game %s: Neon backfill failed (%s)", session.game_id, type(exc).__name__)

    def _label(self, session: Session) -> None:
        """<game_id>.labels.jsonl next to the log, as `label --no-jev` writes it."""
        from societysim.analysis.labeler import label_game, read_log

        path = self.settings.runs_dir / self.settings.batch_id / f"{session.game_id}.jsonl"
        try:
            labels = label_game(read_log(path))
            out = path.with_name(f"{session.game_id}.labels.jsonl")
            out.write_text("".join(lb.model_dump_json() + "\n" for lb in labels), encoding="utf-8")
        except Exception as exc:
            log.warning("game %s: labelling failed (%s)", session.game_id, type(exc).__name__)

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
        if not isinstance(data, dict):
            raise ValueError("the body must be a JSON object")
        try:  # the scratchpad is optional for a visitor
            given = HumanReasoning.model_validate(data.get("private_reasoning") or {})
            model = RESPONSE_MODELS.get(turn.action, TargetResponse)
            parsed = model.model_validate({**data, "private_reasoning": human.reasoning({}, [])})
        except ValidationError as exc:
            raise ValueError("; ".join(setup_errors(exc))) from None
        act = parsed.action
        for name in ("text", "message"):  # statement text, wolf chat message
            if (text := getattr(act, name, None)) is not None:
                text = clean_text(text)
                if not 1 <= len(text) <= MAX_TEXT:
                    raise ValueError(f"{name} must be 1 to {MAX_TEXT} characters")
                act = act.model_copy(update={name: text})
        # Players the visitor didn't rate are logged as not rated (human.HumanSink), never 0.5.
        rated = {e.player: e.p for e in given.suspicion}
        extra = [p for p in rated if p not in turn.others]
        if extra:
            raise ValueError(f"suspicion must rate only {', '.join(turn.others)}")
        reasoning = human.reasoning(rated, turn.others)
        parsed = model.model_validate({"private_reasoning": reasoning, "action": act.model_dump()})
        if error := session.game._invalid(turn, parsed):  # the orchestrator's own check
            raise ValueError(error)
        session.agent.submit(parsed)

    def _prune(self) -> None:
        cutoff = time.time() - KEEP_FINISHED_S
        for gid in [g for g, s in self.games.items() if s.ended_at and s.ended_at < cutoff]:
            del self.games[gid]

    async def shutdown(self) -> None:
        tasks = [s.task for s in self.games.values() if s.task and not s.task.done()]
        if self._neon_task:
            tasks.append(self._neon_task)
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def _metered(
    make_agent: AgentFactory,
    ledger: Ledger,
    game_id: str,
    calls: set[asyncio.Task],
    max_tokens: int,
) -> AgentFactory:
    def make(state: GameState, s: str) -> Agent:
        inner, model = make_agent(state, s), state.models[s]
        system_bytes = len(getattr(inner, "system", "").encode())

        def worst_case(prompt: str) -> float:
            prompt_bytes = system_bytes + len(prompt.encode())
            return admission.call_worst_case(model, prompt_bytes, max_tokens)

        fallback = RandomAgent(state, s)
        return MeteredAgent(inner, fallback, ledger, game_id, worst_case, calls)

    return make


def _seat(make_agent: AgentFactory, seat: str, agent: Agent) -> AgentFactory:
    def make(state: GameState, s: str) -> Agent:
        return agent if s == seat else make_agent(state, s)

    return make
