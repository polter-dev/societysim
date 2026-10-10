"""Command-line entry point. Subcommands are added by their tickets (play, batch, doctor, ...)."""

import asyncio
import os
import sys
from enum import StrEnum
from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv

app = typer.Typer(no_args_is_help=True, help="societysim: LLM Werewolf research harness")


class AgentMode(StrEnum):
    SCRIPTED = "scripted"
    LLM = "llm"


@app.callback()
def main() -> None:
    """Keeps typer in multi-command mode while only one subcommand exists."""


@app.command()
def version() -> None:
    """Print the package version."""
    from societysim import __version__

    typer.echo(__version__)


def _require_served(specs) -> None:
    """Exit before game 1 if a self-hosted server doesn't list the model id we would send."""
    from societysim.models.registry import check_served_models

    errors = [e for e in check_served_models(specs).values() if e]
    for e in errors:
        typer.echo(e)
    if errors:
        raise typer.Exit(1)


NeonFlag = Annotated[
    bool,
    typer.Option("--neon", help="Also stream events to Neon live (needs NEON_DATABASE_URL)."),
]


def _neon_connect(neon: bool):
    """With --neon, create the Neon tables and return the connect function for NeonSink.

    Any Neon problem (no NEON_DATABASE_URL, unreachable) is a warning: the run goes on without it.
    """
    if not neon:
        return None
    from societysim.logging.sinks import neon as neon_sink

    async def setup():
        conn = await neon_sink.connect_neon()
        try:
            await neon_sink.apply_schema(conn)
        finally:
            await conn.close()

    try:
        asyncio.run(setup())
    except Exception as exc:  # the message may hold the DSN, so print only the type
        typer.echo(
            f"warning: Neon unavailable ({type(exc).__name__}); not streaming live", err=True
        )
        return None
    return neon_sink.connect_neon


@app.command()
def play(
    config: Annotated[Path, typer.Option("--config", help="Game config YAML file.")],
    agents: Annotated[AgentMode, typer.Option("--agents", help="Agent type.")] = AgentMode.SCRIPTED,
    seed: Annotated[int | None, typer.Option("--seed", help="Override the config seed.")] = None,
    batch_id: Annotated[
        str | None, typer.Option("--batch-id", help="Output directory name; default: config stem.")
    ] = None,
    neon: NeonFlag = False,
    human_seat: Annotated[
        str | None,
        typer.Option("--human-seat", help="Play this seat yourself, e.g. p3. Logged as 'human'."),
    ] = None,
) -> None:
    """Play one game and write its event log and run metadata."""
    from societysim.agents import human
    from societysim.agents.llm_agent import llm_agents
    from societysim.agents.scripted import RandomAgent
    from societysim.config import GameConfig, load_yaml
    from societysim.logging.sinks.jsonl import JsonlSink
    from societysim.logging.sinks.neon import NeonSink
    from societysim.models.registry import load_registry
    from societysim.orchestrator import run_game

    if not config.is_file():
        typer.echo(f"config file not found: {config}")
        raise typer.Exit(1)
    cfg = load_yaml(config, GameConfig)
    if seed is not None:
        cfg = cfg.model_copy(update={"seed": seed})
    batch_id = batch_id if batch_id is not None else config.stem
    game_id = f"{batch_id}-s{cfg.seed}"
    if human_seat is not None:
        models = cfg.seat_models()
        seats = [f"p{i}" for i in range(cfg.n_players)]
        if human_seat not in seats or not models:
            typer.echo(f"--human-seat must be one of {', '.join(seats)} with models in the config")
            raise typer.Exit(1)
        models[seats.index(human_seat)] = human.MODEL
        cfg = cfg.model_copy(update={"models": models})

    load_dotenv()
    registry_path = Path(__file__).resolve().parents[2] / "configs/models.yaml"
    reg = load_registry(registry_path)
    reg.apply_env()
    try:
        specs = reg.resolve([m for m in cfg.seat_models() if m != human.MODEL])
    except KeyError as exc:
        typer.echo(exc.args[0])
        raise typer.Exit(1) from None

    if agents is AgentMode.SCRIPTED:
        make_agent = RandomAgent
    else:
        provider_keys = {
            "huggingface": ("HF_TOKEN", "HUGGINGFACE_API_KEY"),
            "hosted_vllm": ("HOSTED_VLLM_API_KEY",),
            "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
            "openai": ("OPENAI_API_KEY",),
        }
        missing = []
        for spec in specs:
            keys = (spec.api_key_env,) if spec.api_key_env else provider_keys.get(spec.provider, ())
            if keys and not any(os.environ.get(key) for key in keys):
                missing.append(" or ".join(keys))
            try:
                spec.resolved_api_base()
            except ValueError as exc:
                missing.append(str(exc))
        missing = list(dict.fromkeys(missing))
        if missing:
            typer.echo(f"missing provider key: {', '.join(missing)}")
            raise typer.Exit(1)
        _require_served(specs)

        from societysim.models.client import LiteLLMClient

        client = LiteLLMClient(limits=reg.limits())
        make_agent = llm_agents(client, {s.name: s for s in specs})

    sinks = [JsonlSink(batch_id)]
    if connect := _neon_connect(neon):
        sinks.append(NeonSink(batch_id, connect=connect))
    if human_seat is not None:
        make_agent = human.with_human(make_agent, human_seat)
        sinks = [human.MarkDefaults(sink, human_seat) for sink in sinks]
    try:
        game = asyncio.run(
            run_game(cfg, make_agent, sinks, game_id, colors={s.name: s.color for s in specs})
        )
    except (EOFError, KeyboardInterrupt):
        typer.echo(f"\ngame abandoned; partial log: {Path('runs') / batch_id / f'{game_id}.jsonl'}")
        raise typer.Exit(130) from None
    typer.echo(Path("runs") / batch_id / f"{game_id}.jsonl")
    typer.echo(f"winner: {game.winner}")


@app.command()
def batch(
    config: Annotated[Path, typer.Option("--config", help="Batch config YAML file.")],
    agents: Annotated[AgentMode, typer.Option("--agents", help="Agent type.")] = AgentMode.SCRIPTED,
    registry: Annotated[Path, typer.Option("--registry", help="Model registry file.")] = Path(
        "configs/models.yaml"
    ),
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print the game plan and cost estimate; run nothing.")
    ] = False,
    neon: NeonFlag = False,
) -> None:
    """Play a batch of games in parallel into runs/<batch_id>/. Re-run to resume.

    Stops starting games when the budget would be exceeded; Ctrl-C stops cleanly.
    """
    from societysim.batch.runner import BatchMismatch, plan, run_batch
    from societysim.config import BatchConfig, load_yaml
    from societysim.models.registry import load_registry

    for path in (config, registry):
        if not path.is_file():
            typer.echo(f"config file not found: {path}")
            raise typer.Exit(1)
    cfg = load_yaml(config, BatchConfig)
    load_dotenv()
    reg = load_registry(registry)
    reg.apply_env()
    try:
        specs = {s.name: s for s in reg.resolve(sorted(set(cfg.models)))}
    except KeyError as exc:
        typer.echo(exc.args[0])
        raise typer.Exit(1) from None

    if dry_run:
        rows = plan(cfg, specs)
        for row in rows:
            seats = " ".join(f"{seat}={model}/{role}" for seat, model, role in row["seats"])
            typer.echo(f"{row['game_id']} seed={row['seed']} ${row['est_usd']:.4f} {seats}")
        total = sum(row["est_usd"] for row in rows)
        typer.echo(
            f"{len(rows)} games, estimated ${total:.2f} (price-based guess) "
            f"against a ${cfg.budget_usd:.2f} budget"
        )
        return

    client = None
    if agents is AgentMode.LLM:
        provider_keys = {
            "huggingface": ("HF_TOKEN", "HUGGINGFACE_API_KEY"),
            "hosted_vllm": ("HOSTED_VLLM_API_KEY",),
            "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
            "openai": ("OPENAI_API_KEY",),
        }
        # Self-hosted entries carry their own key (checked by _require_served below).
        providers = {spec.provider for spec in specs.values() if not spec.api_key_env}
        missing = [
            " or ".join(keys)
            for provider, keys in provider_keys.items()
            if provider in providers and not any(os.environ.get(key) for key in keys)
        ]
        if missing:
            typer.echo(f"missing provider key: {', '.join(missing)}")
            raise typer.Exit(1)
        _require_served(specs.values())

        from societysim.models.client import LiteLLMClient

        client = LiteLLMClient(limits=reg.limits())

    try:
        result = asyncio.run(
            run_batch(
                cfg,
                specs,
                client,
                provider_concurrency={p: c.concurrency for p, c in reg.providers.items()},
                neon_connect=_neon_connect(neon),
            )
        )
    except KeyboardInterrupt:
        typer.echo(f"interrupted; re-run to resume: runs/{cfg.batch_id}")
        raise typer.Exit(130) from None
    except BatchMismatch as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from None
    typer.echo(Path("runs") / cfg.batch_id)
    typer.echo(
        f"{result['status']} ({agents.value}): {len(result['finished'])} played, "
        f"{len(result['skipped'])} skipped, "
        f"{len(result['not_started'])} not started; "
        f"spent ${result['spent_usd']:.4f} of ${cfg.budget_usd:.2f}"
    )
    if result["failed"]:
        typer.echo(f"failed games: {', '.join(result['failed'])}")
        for msg in result["provider_down"]:
            typer.echo(f"provider down: {msg}")
        if result["provider_down"]:
            out = Path("runs") / cfg.batch_id
            typer.echo(f"no new games started; re-run once it is back to resume: {out}")
        raise typer.Exit(1)


@app.command()
def doctor(
    model: Annotated[
        str, typer.Option("--model", help="Glob of model names from models.yaml.")
    ] = "*",
    config: Annotated[Path, typer.Option("--config", help="Model registry file.")] = Path(
        "configs/models.yaml"
    ),
) -> None:
    """Ping each model with a tiny structured call in every structured mode.

    Prints which modes work with latency and cost. Exits 1 if any model has no working mode
    or if its configured `structured` mode fails.
    """
    from societysim.models.client import LiteLLMClient
    from societysim.models.registry import (
        check_served_models,
        load_registry,
        probe_model,
        served_key,
    )

    load_dotenv()
    if not config.is_file():
        typer.echo(f"config file not found: {config}")
        raise typer.Exit(1)
    reg = load_registry(config)
    reg.apply_env()
    specs = reg.match(model)
    if not specs:
        typer.echo(f"no model matches {model!r}")
        raise typer.Exit(1)
    client = LiteLLMClient(limits=reg.limits())

    failed = False
    reachable = []
    served = check_served_models(specs)
    for spec in specs:
        if error := served[served_key(spec)]:
            failed = True
            typer.echo(f"FAIL {error}")  # the error already names the model
        else:
            reachable.append(spec)
    specs = reachable

    async def run():
        return await asyncio.gather(*(probe_model(client, s) for s in specs))

    for spec, results in zip(specs, asyncio.run(run()), strict=True):
        working = [r.mode for r in results if r.ok]
        configured_ok = any(r.mode == spec.structured and r.ok for r in results)
        status = "ok" if configured_ok else ("WORKS ONLY WITH " if working else "FAIL")
        if not configured_ok:
            failed = True
        typer.echo(f"{spec.name}: {status}{', '.join(working) if not configured_ok else ''}")
        for r in results:
            mark = "ok  " if r.ok else "fail"
            detail = f"{r.latency_ms} ms, ${r.cost_usd:.5f}" if r.ok else (r.error or "")[:120]
            # Provider errors can hold characters the console can't encode (Windows cp1252).
            enc = getattr(sys.stdout, "encoding", None) or "utf-8"
            detail = detail.encode(enc, "replace").decode(enc)
            typer.echo(f"    {mark} {r.mode:<12} {detail}")
    if failed:
        raise typer.Exit(1)


class JevFailed(Exception):
    """Jev could not classify a game; the message is safe to print (no key, no URL)."""


@app.command()
def label(
    batch_dir: Path,
    no_jev: bool = typer.Option(False, "--no-jev", help="Skip Jev and the agreement table."),
    jev_cache: Annotated[
        Path | None,
        typer.Option("--jev-cache", help="Jev response cache. Default: BATCH_DIR/.cache/jev."),
    ] = None,
) -> None:
    """Write <game_id>.labels.jsonl next to each game log in BATCH_DIR.

    With TYPESAFE_API_KEY set, also classify statements with Jev (cached in
    BATCH_DIR/.cache/jev) and write the self-tag vs Jev agreement table to
    BATCH_DIR/agreement.json. If Jev fails (bad key, no connection, malformed response),
    the automatic labels are still written and no agreement table is left behind.
    """
    import httpx

    from societysim.analysis import jev
    from societysim.analysis.labeler import jev_verdicts, label_batch

    def without_jev(why: str) -> None:
        (batch_dir / "agreement.json").unlink(missing_ok=True)  # a stale table would mislead
        label_batch(batch_dir)
        typer.echo(f"Jev skipped ({why}); no self-tag vs Jev agreement")

    load_dotenv()
    if no_jev or not os.environ.get("TYPESAFE_API_KEY"):
        without_jev("--no-jev" if no_jev else "TYPESAFE_API_KEY not set")
        return
    cache = jev_cache.resolve() if jev_cache else jev.cache_dir_for(batch_dir)

    def jev_for(events):
        try:
            return jev_verdicts(asyncio.run(jev.classify_events(events, cache_dir=cache)))
        except httpx.HTTPStatusError as e:
            raise JevFailed(f"HTTP {e.response.status_code}") from None
        except httpx.HTTPError as e:
            raise JevFailed(type(e).__name__) from None
        except (KeyError, TypeError, ValueError) as e:  # ValidationError is a ValueError
            raise JevFailed(f"malformed response, {type(e).__name__}") from None

    try:
        label_batch(batch_dir, jev_for=jev_for)
    except JevFailed as e:
        without_jev(f"Jev failed: {e}")


@app.command("sync-neon")
def sync_neon(
    batch_dir: Annotated[
        Path | None, typer.Argument(help="Batch directory, e.g. runs/<batch>.")
    ] = None,
    delete: Annotated[
        list[str] | None, typer.Option("--delete", help="Delete this game id (repeatable).")
    ] = None,
) -> None:
    """Backfill BATCH_DIR into Neon, or --delete games. Needs NEON_DATABASE_URL."""
    from societysim.logging.sinks.neon import (
        apply_schema,
        connect_neon,
        delete_games,
        sync_batch,
    )

    load_dotenv()
    if not os.environ.get("NEON_DATABASE_URL"):
        typer.echo("NEON_DATABASE_URL is not set")
        raise typer.Exit(1)
    if delete:
        if batch_dir is not None:
            typer.echo("give either BATCH_DIR or --delete, not both")
            raise typer.Exit(1)
    elif batch_dir is None or not batch_dir.is_dir():
        typer.echo(f"not a directory: {batch_dir}")
        raise typer.Exit(1)

    async def run():
        conn = await connect_neon()
        try:
            if delete:
                return await delete_games(conn, delete)
            await apply_schema(conn)
            return await sync_batch(batch_dir, conn)
        finally:
            await conn.close()

    n = asyncio.run(run())
    if delete:
        typer.echo(f"deleted {len(n['deleted'])} games, {n['events']} events, {n['labels']} labels")
        if n["missing"]:
            typer.echo(f"not found: {', '.join(n['missing'])}")
        return
    typer.echo(f"synced {n['games']} games, {n['events']} events, {n['labels']} labels")


@app.command()
def report(
    batch: Annotated[Path, typer.Argument(help="Batch directory, e.g. runs/<batch>.")],
) -> None:
    """Write report.md, metrics.json, charts, leaderboard.json and index.json to <batch>/report."""
    from societysim.analysis.report import write_report

    try:
        out = write_report(batch)
    except ValueError as e:
        typer.echo(str(e))
        raise typer.Exit(1) from e
    typer.echo(f"wrote {out}")


@app.command()
def highlights(
    batch: Annotated[Path, typer.Argument(help="Batch directory, e.g. runs/<batch>.")],
    top: Annotated[int, typer.Option("--top", help="How many moments to keep.")] = 10,
    out: Annotated[
        Path | None, typer.Option("--out", help="Output file. Default: <batch>/highlights.json.")
    ] = None,
) -> None:
    """Print the batch's best demo moments and write them to <batch>/highlights.json."""
    from societysim.analysis.highlights import write_highlights

    if not batch.is_dir():
        typer.echo(f"not a directory: {batch}")
        raise typer.Exit(1)
    out = out or batch / "highlights.json"
    moments = write_highlights(batch, top, out)
    typer.echo(
        f"{'score':>5}  {'type':<20} {'game':<20} {'day':>3}  {'seq':<9} {'actor':<16} target"
    )
    for m in moments:
        seq = f"{m['seq'][0]}-{m['seq'][1]}"
        actor = f"{m['actor']} {m['actor_model']}"
        target = f"{m['target']} {m['target_model']}" if m["target"] else "-"
        typer.echo(
            f"{m['score']:>5}  {m['type']:<20} {m['game_id']:<20} {m['day']:>3}  {seq:<9} "
            f"{actor:<16} {target}\n       {m['description']}\n       {m['link']}"
        )
    typer.echo(f"wrote {out} ({len(moments)} moments)")


if __name__ == "__main__":
    app()
