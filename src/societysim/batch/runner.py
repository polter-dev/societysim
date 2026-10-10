"""Run a batch of games in parallel under a hard dollar cap, with resume.

Layout: runs/<batch_id>/<game_id>.jsonl per game plus runs/<batch_id>/run_meta.json for the
batch. A game counts as finished when its JSONL ends with a game_end event; anything else is
replayed from scratch (the JSONL sink overwrites).
"""

import asyncio
import json
from collections.abc import Mapping
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

Status = Literal["running", "completed", "budget_stopped", "interrupted"]


class MeteredClient:
    """Wraps a ModelClient: adds up cost and caps in-flight calls per provider."""

    def __init__(self, inner: ModelClient, provider_concurrency: Mapping[str, int] | None = None):
        self.inner = inner
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


async def run_batch(
    cfg: BatchConfig,
    specs: Mapping[str, ModelSpec],
    client: ModelClient | None,
    *,
    provider_concurrency: Mapping[str, int] | None = None,
    runs_dir: str | Path = "runs",
) -> dict[str, Any]:
    """Play every unfinished game of the batch; return the summary also written to run_meta.json.

    client=None plays scripted RandomAgents (free). A new game starts only if the spend so far
    plus the average cost of a game for each game in flight and the new one stays within
    cfg.budget_usd; games already running finish. Spend includes finished games from an
    earlier run. On cancellation the running games are cancelled, run_meta.json gets status
    "interrupted" and CancelledError is re-raised; re-running resumes.
    """
    out = Path(runs_dir) / cfg.batch_id
    out.mkdir(parents=True, exist_ok=True)
    metered = MeteredClient(client, provider_concurrency) if client else None
    make_agent: AgentFactory = llm_agents(metered, specs) if metered else RandomAgent
    colors = {name: s.color for name, s in specs.items()}

    started = datetime.now(UTC).isoformat()
    skipped: list[str] = []
    todo = []
    prior_spend = 0.0
    for a in assign_games(cfg):
        cost = game_cost(out / f"{a.game_id}.jsonl")
        if cost is None:
            todo.append(a)
        else:
            skipped.append(a.game_id)
            prior_spend += cost

    finished: list[str] = []
    started_ids: list[str] = []
    in_flight = 0
    sem = asyncio.Semaphore(cfg.max_concurrency)  # global cap on games in flight

    def spent() -> float:
        return prior_spend + (metered.spent if metered else 0.0)

    def summary(status: Status) -> dict[str, Any]:
        return {
            "batch_id": cfg.batch_id,
            "status": status,
            "started_at": started,
            "updated_at": datetime.now(UTC).isoformat(),
            "spent_usd": round(spent(), 6),
            "budget_usd": cfg.budget_usd,
            "finished": finished,
            "skipped": skipped,
            "not_started": [a.game_id for a in todo if a.game_id not in started_ids],
            "config": cfg.model_dump(mode="json"),
        }

    def save(status: Status) -> dict[str, Any]:
        s = summary(status)
        (out / "run_meta.json").write_text(json.dumps(s, indent=2))
        return s

    def allowed() -> bool:
        done = len(skipped) + len(finished)
        avg = spent() / done if done else 0.0
        return spent() < cfg.budget_usd and spent() + avg * (in_flight + 1) <= cfg.budget_usd

    async def play(game_id: str, game_cfg) -> None:
        nonlocal in_flight
        try:
            sinks = [JsonlSink(cfg.batch_id, runs_dir)]
            await run_game(game_cfg, make_agent, sinks, game_id, colors)
            finished.append(game_id)
        finally:
            in_flight -= 1
            sem.release()

    save("running")
    tasks: set[asyncio.Task[None]] = set()
    budget_hit = False
    try:
        for a in todo:
            await sem.acquire()
            if not allowed():
                sem.release()
                budget_hit = True
                break
            in_flight += 1
            started_ids.append(a.game_id)
            task = asyncio.create_task(play(a.game_id, a.game_config(cfg.game)))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
        while tasks:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()  # surface a crashed game
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        save("interrupted")
        raise
    return save("budget_stopped" if budget_hit else "completed")
