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
import re
import time
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
from societysim.logging.textio import read_text_lenient
from societysim.models.client import CompletionResult, ModelClient
from societysim.orchestrator import run_game

log = logging.getLogger(__name__)

_game: ContextVar[str | None] = ContextVar("batch_game", default=None)

Status = Literal[
    "running", "completed", "budget_stopped", "stopped_invalid", "failed", "interrupted"
]

_now = time.monotonic  # the clock for durations and the ETA; tests replace it

# A model whose calls fail this many times in a row without getting any tokens back is taken
# as down (no credits, quota hit, key revoked); the batch stops starting games.
PROVIDER_DOWN_AFTER = 10


class ProviderDown(Exception):
    """A model's provider stopped answering. The message names the model, never a key or URL."""

    def __init__(self, model: str, error: str):
        error = re.sub(r"https?://\S+", "<url>", error)
        error = re.sub(r"[A-Za-z0-9_\-]{24,}", "<redacted>", error)[:200]
        super().__init__(f"{model}: {PROVIDER_DOWN_AFTER} calls in a row failed; last: {error}")


class MeteredClient:
    """Wraps a ModelClient: adds up cost, caps in-flight calls per provider and raises
    ProviderDown once a model fails PROVIDER_DOWN_AFTER calls in a row with no tokens."""

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
        self.failures: Counter[str] = Counter()  # model -> consecutive provider failures

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
        if result.usage.input_tokens or result.usage.output_tokens:
            self.failures[model.name] = 0  # it answered, even if the answer was bad JSON
        elif result.error:
            self.failures[model.name] += 1
            if self.failures[model.name] >= PROVIDER_DOWN_AFTER:
                raise ProviderDown(model.name, result.error)
        return result


def game_costs(path: Path) -> Counter[str] | None:
    """USD per model in a finished game log, or None if the game did not finish."""
    try:
        lines = read_text_lenient(path).splitlines()
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


def game_invalid(path: Path, models: list[str]) -> dict[str, list[int]]:
    """Per model in a finished game log: [invalid_output events, model attempts]. `models` is the
    game's model per seat p0..; a log that cannot be read counts as nothing."""
    out: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    try:
        for line in read_text_lenient(path).splitlines():
            e = json.loads(line)
            if e["type"] == EventType.INVALID_OUTPUT and e.get("actor"):
                out[models[int(e["actor"][1:])]][0] += 1
            if usage := (e.get("meta") or {}).get("usage"):
                out[usage["model"]][1] += usage.get("attempts", 1)
    except (OSError, ValueError, KeyError, IndexError):
        return {}
    return dict(out)


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


REPLACE_BACKOFF = (0.05, 0.1, 0.2, 0.4, 0.8, 0.4)  # seconds; about 2 s in all


def _replace(src: Path, dst: Path) -> None:
    """os.replace that waits out a reader holding dst open (Windows: watchdog, OneDrive)."""
    for delay in REPLACE_BACKOFF:
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            time.sleep(delay)
    os.replace(src, dst)


def _tag_agents(path: Path, agents: str) -> None:
    """Record the agent type in a game's run_meta.json, next to its finished JSONL."""
    meta = json.loads(read_text_lenient(path))
    meta["agents"] = agents
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    _replace(tmp, path)


class BatchMismatch(Exception):
    """runs/<batch_id> holds games from a different batch config; the message is safe to print."""


def fingerprint(cfg: BatchConfig, specs: Mapping[str, ModelSpec], agents: str) -> str:
    """Hash of what decides what a game is: config, agent type and the models' registry entries.

    n_games, budget, the invalid-output limits and concurrency may change between runs.
    """
    keep = cfg.model_dump(
        mode="json",
        exclude={
            "n_games",
            "budget_usd",
            "max_concurrency",
            "max_invalid_rate",
            "min_calls_for_invalid_check",
        },
    )
    keep["agents"] = agents
    keep["registry"] = {m: specs[m].model_dump(mode="json") for m in sorted(set(cfg.models))}
    return hashlib.sha256(json.dumps(keep, sort_keys=True).encode()).hexdigest()[:16]


class CorruptMeta(BatchMismatch):
    """run_meta.json exists but cannot be read; it is never treated as a fresh batch."""


def _read_meta(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        meta = json.loads(read_text_lenient(path))
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
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Play every unfinished game of the batch; return the summary also written to run_meta.json.

    client=None plays scripted RandomAgents (free). Spend is kept in run_meta.json after every
    model call, including games that never finished, and carries over to a resume. A new game
    starts only if spend so far, plus what each in-flight game is still expected to spend,
    plus the new game's estimate stays within cfg.budget_usd. A game is estimated as the sum
    over its seats of the highest per-seat cost seen so far for that seat's model, or
    prior_seat_cost (the same estimator --dry-run prints) for a model with no finished game yet,
    so fresh models run in parallel too. The prior is a safe reservation: T5.1 measured real
    games at about a quarter of it (a cheap model's seat cost about $0.023 a game against a
    $0.09 prior), and the live T5.2 batch averages about $0.06 a game against $0.09.
    A model with no finished game and a prior of 0 (no listed price, e.g. AMD) cannot be
    bounded that way, so a game with such a seat runs alone: it waits for every game in flight
    to end, and nothing else starts while it runs. Once it ends, its observed cost takes over.
    Overshoot bound: spend can pass cfg.budget_usd only by the amount real games exceed their
    estimates. For a model with a price this needs a model far dearer than its price table
    (the first max_concurrency games can each exceed the prior, so at most max_concurrency
    games' worth of excess); for an unpriced model it is at most one game, the one running
    alone. Games already running at the stop always finish.
    Games already running finish. After each finished game (and on resume, over the games
    already on disk) every model's invalid_output rate is checked; one above
    cfg.max_invalid_rate with at least cfg.min_calls_for_invalid_check attempts stops new games,
    the batch ends "stopped_invalid" and "invalid_stop" names the model. A game that
    raises is recorded under "failed" and the batch ends "failed". A model that fails
    PROVIDER_DOWN_AFTER calls in a row with no tokens raises ProviderDown in its game; no new
    game starts after that, and the message is kept under "provider_down". On cancellation the
    running games are cancelled, run_meta.json gets status "interrupted" and CancelledError is
    re-raised; re-running resumes. Resuming into a batch_id made from a different config
    raises BatchMismatch. With neon_connect, every game also streams to Neon (NeonSink) while
    it runs; Neon errors are logged and never stop a game.

    on_progress, if given, gets a dict after each game ends (event "finished" or "failed", with
    done, total, game_id, winner or reason, seconds, spent, budget, running, eta_seconds) and
    once when the guard stops new games (event "stopped", with reason, spent, budget, running).
    done counts games skipped on resume. eta_seconds is remaining games divided by the observed
    finish rate (games finished in this run per wall second since it began, so it already
    reflects concurrency, slow models and the lone first game of each unseen model). It is None
    until a game finishes in this run (failed games say nothing about pace); callers show
    "estimating..." for None.
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
    invalid: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # model -> [invalid, attempts]
    invalid_stop: list[str] = []  # at most one message; its presence stops new games

    def count_invalid(a: Assignment) -> None:
        for model, (bad, calls) in game_invalid(out / f"{a.game_id}.jsonl", a.models).items():
            invalid[model][0] += bad
            invalid[model][1] += calls
        for model, (bad, calls) in invalid.items():
            if calls >= cfg.min_calls_for_invalid_check and bad / calls > cfg.max_invalid_rate:
                invalid_stop[:] = [
                    f"{model}: {bad / calls:.1%} invalid output ({bad} of {calls} calls), "
                    f"over the limit of {cfg.max_invalid_rate:.1%}; no new games started"
                ]
                return

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
            count_invalid(a)
            prior_spend += sum(costs.values())
    # The ledger also holds money spent on games that were cut off; logs alone would miss it.
    prior_spend = max(float(old.get("spent_usd", 0.0)), prior_spend)

    finished: list[str] = []
    failed: dict[str, str] = {}
    # game_id -> events its JSONL sink could not write (holes in the log). On resume only the
    # finished games keep theirs; a game played again starts from a fresh log.
    write_errors: dict[str, int] = {
        g: n for g, n in (old.get("write_errors") or {}).items() if g in skipped
    }
    down: list[str] = []  # ProviderDown messages; any one stops new games
    started_ids: list[str] = []
    in_flight = 0
    sem = asyncio.Semaphore(cfg.max_concurrency)  # global cap on games in flight
    metered: MeteredClient | None = None
    run_t0 = _now()
    total = cfg.n_games

    def progress(event: str, **info: Any) -> None:
        if on_progress:
            on_progress({"event": event, "spent": spent(), "budget": cfg.budget_usd, **info})

    def spent() -> float:
        return prior_spend + (metered.spent if metered else 0.0)

    def save_quietly() -> None:
        """Mid-game ledger write: a stuck run_meta.json must not kill the game being played."""
        try:
            save("running")
        except OSError:
            log.warning("batch %s: run_meta.json not written; the next save retries", cfg.batch_id)

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
            "write_errors": write_errors,
            "provider_down": down,
            "invalid_output": {m: {"invalid": b, "calls": c} for m, (b, c) in invalid.items()},
            "invalid_stop": invalid_stop[0] if invalid_stop else None,
            "not_started": [a.game_id for a in todo if a.game_id not in started_ids],
            "config": cfg.model_dump(mode="json"),
        }
        tmp = meta_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(s, indent=2), encoding="utf-8")
        _replace(tmp, meta_path)  # a hard kill leaves the old file, never half of one
        return s

    if client:
        metered = MeteredClient(client, provider_concurrency, on_spend=save_quietly)
    make_agent: AgentFactory = llm_agents(metered, specs) if metered else RandomAgent
    colors = {name: s.color for name, s in specs.items()}

    reserved: dict[str, float] = {}  # in-flight game -> estimated USD
    solo: set[str] = set()  # in-flight games with a seat no estimate can bound

    def unbounded(a: Assignment) -> bool:
        """A model with no finished game and no listed price: its prior is 0."""
        return any(m not in seat_cost and prior_seat_cost(specs[m]) == 0 for m in a.models)

    def estimate(a: Assignment) -> float:
        """Sum of each seat's observed per-seat cost, or the prior for a model with no data."""
        return sum(seat_cost[m] if m in seat_cost else prior_seat_cost(specs[m]) for m in a.models)

    def verdict(a: Assignment) -> Literal["go", "wait", "stop"]:
        if down or invalid_stop or spent() >= cfg.budget_usd:
            return "stop"
        if metered is None:
            return "go"  # scripted games are free
        if in_flight and (solo or unbounded(a)):
            return "wait"  # an unpriced game runs alone, so its spend is the only one moving
        est = estimate(a)
        # An in-flight game only still needs what it has not spent yet.
        left = sum(max(e - sum(metered.by_game[g].values()), 0.0) for g, e in reserved.items())
        if spent() + left + est <= cfg.budget_usd:
            return "go"
        return "wait" if in_flight else "stop"  # running games may free budget by finishing

    async def play(a: Assignment) -> None:
        nonlocal in_flight
        _game.set(a.game_id)
        t0 = _now()
        outcome: dict[str, Any] = {}
        jsonl = JsonlSink(cfg.batch_id, runs_dir)
        try:
            sinks: list[Any] = [jsonl]
            if neon_connect:
                sinks.append(NeonSink(cfg.batch_id, connect=neon_connect, agents=agents))
            game = await run_game(a.game_config(cfg.game), make_agent, sinks, a.game_id, colors)
            outcome = {"event": "finished", "winner": getattr(game.winner, "value", game.winner)}
            try:
                _tag_agents(out / f"{a.game_id}.run_meta.json", agents)
            except OSError:  # the game is done; its JSONL and game_end are the result
                log.warning("game %s: could not tag run_meta.json with agents", a.game_id)
            finished.append(a.game_id)
            count_invalid(a)
            if invalid_stop:
                log.warning("batch %s: %s", cfg.batch_id, invalid_stop[0])
            if metered:
                observe(a, metered.by_game[a.game_id])
        except Exception as exc:
            log.exception("game %s failed", a.game_id)
            failed[a.game_id] = f"{type(exc).__name__}: {exc}"
            outcome = {"event": "failed", "reason": failed[a.game_id]}
            if isinstance(exc, ProviderDown) and str(exc) not in down:
                down.append(str(exc))
            # asyncio.gather does not cancel the siblings of a call that raised; they inherit
            # this task's context, so find them by game id.
            me = asyncio.current_task()
            stray = [
                t
                for t in asyncio.all_tasks()
                if t is not me and t.get_context().get(_game) == a.game_id
            ]
            for t in stray:
                t.cancel()
            await asyncio.gather(*stray, return_exceptions=True)
        finally:
            if jsonl.write_errors:
                write_errors[a.game_id] = jsonl.write_errors
                log.error(
                    "game %s: %d events missing from its JSONL log", a.game_id, jsonl.write_errors
                )
            in_flight -= 1
            reserved.pop(a.game_id, None)
            solo.discard(a.game_id)
            sem.release()
            if outcome:
                seconds = _now() - t0
                done = len(skipped) + len(finished) + len(failed)
                eta = (_now() - run_t0) / len(finished) * (total - done) if finished else None
                progress(
                    **outcome,
                    done=done,
                    total=total,
                    game_id=a.game_id,
                    seconds=seconds,
                    running=in_flight,
                    eta_seconds=eta,
                )

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
                progress(
                    "stopped",
                    reason="provider_down"
                    if down
                    else "invalid_output"
                    if invalid_stop
                    else "budget",
                    running=in_flight,
                    detail=down[0] if down else invalid_stop[0] if invalid_stop else "",
                )
                break
            in_flight += 1
            reserved[a.game_id] = estimate(a)
            if unbounded(a):
                solo.add(a.game_id)
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
    if failed:
        return save("failed")
    if invalid_stop and len(started_ids) < len(todo):
        return save("stopped_invalid")
    return save("budget_stopped" if budget_hit else "completed")
