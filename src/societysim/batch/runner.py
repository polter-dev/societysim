"""Run a batch of games in parallel under a hard dollar cap, with resume.

Layout: runs/<batch_id>/<game_id>.jsonl per game plus runs/<batch_id>/run_meta.json for the
batch. A game counts as finished when its JSONL ends with a game_end event; anything else is
replayed from scratch (the JSONL sink overwrites).
"""

import asyncio
import hashlib
import json
import logging
import os
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from societysim.agents.base import AgentFactory
from societysim.agents.llm_agent import llm_agents
from societysim.agents.scripted import RandomAgent
from societysim.batch.assign import Assignment, assign_games
from societysim.config import BatchConfig, ModelSpec
from societysim.logging.events import EventType
from societysim.logging.sinks.jsonl import JsonlSink
from societysim.logging.sinks.neon import Connect, NeonSink
from societysim.models.client import CompletionResult, ModelClient
from societysim.orchestrator import run_game

log = logging.getLogger(__name__)

_game: ContextVar[str | None] = ContextVar("batch_game", default=None)

Status = Literal["running", "completed", "budget_stopped", "failed", "interrupted"]


class MeteredClient:
    """Wraps a ModelClient: adds up cost and caps in-flight calls per provider."""

    def __init__(
        self,
        inner: ModelClient,
        provider_concurrency: Mapping[str, int] | None = None,
        on_spend: Callable[[], None] | None = None,
    ):
        self.inner = inner
        self.on_spend = on_spend
        self.spent = 0.0
        self.by_game: dict[str, Counter[str]] = defaultdict(Counter)  # game -> model -> USD
        self._caps = dict(provider_concurrency or {})
        self._sems: dict[str, asyncio.Semaphore] = {}

    async def complete(
        self, model: ModelSpec, messages: list[dict[str, Any]], response_model: type[BaseModel]
    ) -> CompletionResult:
        cap = self._caps.get(model.provider)
        if cap is None:
            result = await self.inner.complete(model, messages, response_model)
        else:
            sem = self._sems.setdefault(model.provider, asyncio.Semaphore(cap))
            async with sem:
                result = await self.inner.complete(model, messages, response_model)
        self.spent += result.usage.cost_usd
        if (gid := _game.get()) is not None:
            self.by_game[gid][model.name] += result.usage.cost_usd
        if self.on_spend:
            self.on_spend()
        return result


def game_costs(path: Path) -> Counter[str] | None:
    """USD per model in a finished game log, or None if the game did not finish."""
    try:
        lines = path.read_text().splitlines()
        if not lines or json.loads(lines[-1])["type"] != EventType.GAME_END:
            return None
        events = [json.loads(line) for line in lines]
    except (OSError, ValueError, KeyError):
        return None
    costs: Counter[str] = Counter()
    for e in events:
        usage = (e.get("meta") or {}).get("usage")
        if usage:
            costs[usage.get("model", "?")] += usage.get("cost_usd", 0.0)
    return costs


# Rough size of one seat's whole game, for cost estimates before any data exists. From T5.1 and
# recent logs: 4 to 11 calls per seat, 1.2k to 2.4k input and 250 to 370 output tokens per call.
# Kept on the high side (12 calls, 2k in, 400 out) so the guard errs towards stopping early.
PRIOR_CALLS_PER_SEAT = 12
PRIOR_INPUT_TOKENS = 2000
PRIOR_OUTPUT_TOKENS = 400


def prior_seat_cost(spec: ModelSpec) -> float:
    """Price-based guess of one seat's cost in one game (USD)."""
    from societysim.models.client import estimate_cost

    return PRIOR_CALLS_PER_SEAT * estimate_cost(spec, PRIOR_INPUT_TOKENS, PRIOR_OUTPUT_TOKENS)


def plan(cfg: BatchConfig, specs: Mapping[str, ModelSpec]) -> list[dict[str, Any]]:
    """One row per game: id, seed, per-seat model and role, and a prior cost estimate."""
    from societysim.engine.state import GameState

    rows = []
    for a in assign_games(cfg):
        roles = GameState(a.game_config(cfg.game)).roles
        rows.append(
            {
                "game_id": a.game_id,
                "seed": a.seed,
                "seats": [
                    (seat, m, roles[seat].value) for seat, m in zip(roles, a.models, strict=True)
                ],
                "est_usd": sum(prior_seat_cost(specs[m]) for m in a.models),
            }
        )
    return rows


def _tag_agents(path: Path, agents: str) -> None:
    """Record the agent type in a game's run_meta.json, next to its finished JSONL."""
    meta = json.loads(path.read_text())
    meta["agents"] = agents
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(meta, indent=2))
    os.replace(tmp, path)


class BatchMismatch(Exception):
    """runs/<batch_id> holds games from a different batch config; the message is safe to print."""


def fingerprint(cfg: BatchConfig, specs: Mapping[str, ModelSpec], agents: str) -> str:
    """Hash of what decides what a game is: config, agent type and the models' registry entries.

    n_games, budget and concurrency may change between runs.
    """
    keep = cfg.model_dump(mode="json", exclude={"n_games", "budget_usd", "max_concurrency"})
    keep["agents"] = agents
    keep["registry"] = {m: specs[m].model_dump(mode="json") for m in sorted(set(cfg.models))}
    return hashlib.sha256(json.dumps(keep, sort_keys=True).encode()).hexdigest()[:16]


class CorruptMeta(BatchMismatch):
    """run_meta.json exists but cannot be read; it is never treated as a fresh batch."""


def _read_meta(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        meta = json.loads(path.read_text())
    except (OSError, ValueError):
        meta = None
    if not isinstance(meta, dict):
        raise CorruptMeta(f"{path} is unreadable; fix or delete it (and check the games) to go on")
    return meta


async def run_batch(
    cfg: BatchConfig,
    specs: Mapping[str, ModelSpec],
    client: ModelClient | None,
    *,
    provider_concurrency: Mapping[str, int] | None = None,
    runs_dir: str | Path = "runs",
    neon_connect: Connect | None = None,
) -> dict[str, Any]:
    """Play every unfinished game of the batch; return the summary also written to run_meta.json.

    client=None plays scripted RandomAgents (free). Spend is kept in run_meta.json after every
    model call, including games that never finished, and carries over to a resume. A new game
    starts only if spend so far, plus what each in-flight game is still expected to spend,
    plus the new game's estimate stays within cfg.budget_usd. A game is estimated as the sum
    over its seats of the highest per-seat cost seen so far for that seat's model, or
    prior_seat_cost for a model with no finished game yet, so fresh models run in parallel too.
    Overshoot happens only when a game costs more than its estimate: a model's first games are
    bounded by the prior, so a model far dearer than its price table can overshoot there.
    Games already running finish. A game that
    raises is recorded under "failed" and the batch ends "failed". On cancellation the running
    games are cancelled, run_meta.json gets status "interrupted" and CancelledError is
    re-raised; re-running resumes. Resuming into a batch_id made from a different config
    raises BatchMismatch. With neon_connect, every game also streams to Neon (NeonSink) while
    it runs; Neon errors are logged and never stop a game.
    """
    out = Path(runs_dir) / cfg.batch_id
    out.mkdir(parents=True, exist_ok=True)
    meta_path = out / "run_meta.json"
    old = _read_meta(meta_path)
    agents = "llm" if client else "scripted"
    fp = fingerprint(cfg, specs, agents)
    if old.get("agents") not in (None, agents):
        raise BatchMismatch(
            f"runs/{cfg.batch_id} was played with {old['agents']} agents, not {agents}; "
            "use a new batch_id or delete the directory"
        )
    if old.get("fingerprint") not in (None, fp):
        raise BatchMismatch(
            f"runs/{cfg.batch_id} was made with a different seed, models (or their registry "
            "entries), mode or game settings; use a new batch_id or delete the directory"
        )

    started = datetime.now(UTC).isoformat()
    skipped: list[str] = []
    todo = []
    seat_cost: dict[str, float] = {}  # model -> highest observed USD per seat per game
    prior_spend = 0.0

    def observe(a: Assignment, costs: Mapping[str, float]) -> None:
        for model, seats in Counter(a.models).items():
            if model in costs:
                seat_cost[model] = max(seat_cost.get(model, 0.0), costs[model] / seats)

    for a in assign_games(cfg):
        costs = game_costs(out / f"{a.game_id}.jsonl")
        if costs is None:
            todo.append(a)
        else:
            skipped.append(a.game_id)
            observe(a, costs)
            prior_spend += sum(costs.values())
    # The ledger also holds money spent on games that were cut off; logs alone would miss it.
    prior_spend = max(float(old.get("spent_usd", 0.0)), prior_spend)

    finished: list[str] = []
    failed: dict[str, str] = {}
    started_ids: list[str] = []
    in_flight = 0
    sem = asyncio.Semaphore(cfg.max_concurrency)  # global cap on games in flight
    metered: MeteredClient | None = None

    def spent() -> float:
        return prior_spend + (metered.spent if metered else 0.0)

    def save(status: Status) -> dict[str, Any]:
        s = {
            "batch_id": cfg.batch_id,
            "status": status,
            "fingerprint": fp,
            "agents": agents,
            "started_at": started,
            "updated_at": datetime.now(UTC).isoformat(),
            "spent_usd": round(spent(), 6),
            "budget_usd": cfg.budget_usd,
            "finished": finished,
            "skipped": skipped,
            "failed": failed,
            "not_started": [a.game_id for a in todo if a.game_id not in started_ids],
            "config": cfg.model_dump(mode="json"),
        }
        tmp = meta_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(s, indent=2))
        os.replace(tmp, meta_path)  # a hard kill leaves the old file, never half of one
        return s

    if client:
        metered = MeteredClient(client, provider_concurrency, on_spend=lambda: save("running"))
    make_agent: AgentFactory = llm_agents(metered, specs) if metered else RandomAgent
    colors = {name: s.color for name, s in specs.items()}

    reserved: dict[str, float] = {}  # in-flight game -> estimated USD

    def estimate(a: Assignment) -> float:
        """Sum of each seat's observed per-seat cost, or the prior for a model with no data."""
        return sum(seat_cost[m] if m in seat_cost else prior_seat_cost(specs[m]) for m in a.models)

    def verdict(a: Assignment) -> Literal["go", "wait", "stop"]:
        if spent() >= cfg.budget_usd:
            return "stop"
        if metered is None:
            return "go"  # scripted games are free
        est = estimate(a)
        # An in-flight game only still needs what it has not spent yet.
        left = sum(max(e - sum(metered.by_game[g].values()), 0.0) for g, e in reserved.items())
        if spent() + left + est <= cfg.budget_usd:
            return "go"
        return "wait" if in_flight else "stop"  # running games may free budget by finishing

    async def play(a: Assignment) -> None:
        nonlocal in_flight
        _game.set(a.game_id)
        try:
            sinks: list[Any] = [JsonlSink(cfg.batch_id, runs_dir)]
            if neon_connect:
                sinks.append(NeonSink(cfg.batch_id, connect=neon_connect))
            await run_game(a.game_config(cfg.game), make_agent, sinks, a.game_id, colors)
            _tag_agents(out / f"{a.game_id}.run_meta.json", agents)
            finished.append(a.game_id)
            if metered:
                observe(a, metered.by_game[a.game_id])
        except Exception as exc:
            log.exception("game %s failed", a.game_id)
            failed[a.game_id] = f"{type(exc).__name__}: {exc}"
        finally:
            in_flight -= 1
            reserved.pop(a.game_id, None)
            sem.release()

    save("running")
    tasks: set[asyncio.Task[None]] = set()
    budget_hit = False
    try:
        for a in todo:
            await sem.acquire()
            while (v := verdict(a)) == "wait":
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            if v == "stop":
                sem.release()
                budget_hit = True
                break
            in_flight += 1
            reserved[a.game_id] = estimate(a)
            started_ids.append(a.game_id)
            task = asyncio.create_task(play(a))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
        while tasks:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        save("interrupted")
        raise
    return save("failed" if failed else "budget_stopped" if budget_hit else "completed")
