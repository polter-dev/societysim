"""Command-line entry point. Subcommands are added by their tickets (play, batch, doctor, ...)."""

import asyncio
import os
import sys
from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv

app = typer.Typer(no_args_is_help=True, help="societysim: LLM Werewolf research harness")


@app.callback()
def main() -> None:
    """Keeps typer in multi-command mode while only one subcommand exists."""


@app.command()
def version() -> None:
    """Print the package version."""
    from societysim import __version__

    typer.echo(__version__)


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
    from societysim.models.registry import load_registry, probe_model

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

    async def run():
        return await asyncio.gather(*(probe_model(client, s) for s in specs))

    failed = False
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


if __name__ == "__main__":
    app()
