"""Run a batch of games in parallel under a hard dollar cap, with resume.

Layout: runs/<batch_id>/<game_id>.jsonl per game plus runs/<batch_id>/run_meta.json for the
batch. A game counts as finished when its JSONL ends with a game_end event; anything else is
replayed from scratch (the JSONL sink overwrites).
"""

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from societysim.agents.base import AgentFactory
from societysim.agents.llm_agent import llm_agents
from societysim.agents.scripted import RandomAgent
from societysim.batch.assign import assign_games
from societysim.config import BatchConfig, ModelSpec
from societysim.logging.events import EventType
from societysim.logging.sinks.jsonl import JsonlSink
from societysim.models.client import CompletionResult, ModelClient
from societysim.orchestrator import run_game

log = logging.getLogger(__name__)

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
        if self.on_spend:
            self.on_spend()
        return result


def game_cost(path: Path) -> float | None:
    """Total cost of a finished game log, or None if the game did not finish."""
    try:
        lines = path.read_text().splitlines()
        if not lines or json.loads(lines[-1])["type"] != EventType.GAME_END:
            return None
        events = [json.loads(line) for line in lines]
    except (OSError, ValueError, KeyError):
        return None
    return sum((e.get("meta") or {}).get("usage", {}).get("cost_usd", 0.0) for e in events)


class BatchMismatch(Exception):
    """runs/<batch_id> holds games from a different batch config; the message is safe to print."""


def fingerprint(cfg: BatchConfig) -> str:
    """Hash of what decides which games exist. n_games, budget and concurrency may change."""
    keep = cfg.model_dump(mode="json", exclude={"n_games", "budget_usd", "max_concurrency"})
    return hashlib.sha256(json.dumps(keep, sort_keys=True).encode()).hexdigest()[:16]


def _read_meta(path: Path) -> dict[str, Any]:
    try:
        meta = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


async def run_batch(
    cfg: BatchConfig,
    specs: Mapping[str, ModelSpec],
    client: ModelClient | None,
    *,
    provider_concurrency: Mapping[str, int] | None = None,
    runs_dir: str | Path = "runs",
) -> dict[str, Any]:
    """Play every unfinished game of the batch; return the summary also written to run_meta.json.

    client=None plays scripted RandomAgents (free). Spend is kept in run_meta.json after every
    model call, including games that never finished, and carries over to a resume. A new game
    starts only if spend so far plus the cost of the dearest finished game for each game in
    flight and the new one stays within cfg.budget_usd. Until some game has finished there is
    no estimate, so only one game runs at a time. Games already running finish. A game that
    raises is recorded under "failed" and the batch ends "failed". On cancellation the running
    games are cancelled, run_meta.json gets status "interrupted" and CancelledError is
    re-raised; re-running resumes. Resuming into a batch_id made from a different config
    raises BatchMismatch.
    """
    out = Path(runs_dir) / cfg.batch_id
    out.mkdir(parents=True, exist_ok=True)
    meta_path = out / "run_meta.json"
    old = _read_meta(meta_path)
    fp = fingerprint(cfg)
    if old.get("fingerprint") not in (None, fp):
        raise BatchMismatch(
            f"runs/{cfg.batch_id} was made with a different seed, models, mode or game settings; "
            "use a new batch_id or delete the directory"
        )

    started = datetime.now(UTC).isoformat()
    skipped: list[str] = []
    todo = []
    finished_cost: dict[str, float] = {}
    for a in assign_games(cfg):
        cost = game_cost(out / f"{a.game_id}.jsonl")
        if cost is None:
            todo.append(a)
        else:
            skipped.append(a.game_id)
            finished_cost[a.game_id] = cost
    # The ledger also holds money spent on games that were cut off; logs alone would miss it.
    prior_spend = max(float(old.get("spent_usd", 0.0)), sum(finished_cost.values()))

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
        meta_path.write_text(json.dumps(s, indent=2))
        return s

    if client:
        metered = MeteredClient(client, provider_concurrency, on_spend=lambda: save("running"))
    make_agent: AgentFactory = llm_agents(metered, specs) if metered else RandomAgent
    colors = {name: s.color for name, s in specs.items()}

    def verdict() -> Literal["go", "wait", "stop"]:
        if spent() >= cfg.budget_usd:
            return "stop"
        if metered is None:
            return "go"  # scripted games are free
        if not finished_cost:  # no estimate yet: probe with one game
            return "wait" if in_flight else "go"
        fits = spent() + max(finished_cost.values()) * (in_flight + 1) <= cfg.budget_usd
        if fits:
            return "go"
        return "wait" if in_flight else "stop"  # running games may free budget by finishing

    async def play(game_id: str, game_cfg) -> None:
        nonlocal in_flight
        before = metered.spent if metered else 0.0
        try:
            sinks = [JsonlSink(cfg.batch_id, runs_dir)]
            await run_game(game_cfg, make_agent, sinks, game_id, colors)
            finished.append(game_id)
            if metered:  # concurrent games share the meter, so this over-estimates; fine
                finished_cost[game_id] = max(metered.spent - before, 1e-9)
            else:
                finished_cost[game_id] = 0.0
        except Exception as exc:
            log.exception("game %s failed", game_id)
            failed[game_id] = f"{type(exc).__name__}: {exc}"
        finally:
            in_flight -= 1
            sem.release()

    save("running")
    tasks: set[asyncio.Task[None]] = set()
    budget_hit = False
    try:
        for a in todo:
            await sem.acquire()
            while (v := verdict()) == "wait":
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            if v == "stop":
                sem.release()
                budget_hit = True
                break
            in_flight += 1
            started_ids.append(a.game_id)
            task = asyncio.create_task(play(a.game_id, a.game_config(cfg.game)))
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
