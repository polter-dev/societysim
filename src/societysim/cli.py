"""Command-line entry point. Subcommands are added by their tickets (play, batch, doctor, ...)."""

import asyncio
import os
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
    BATCH_DIR/agreement.json. If Jev fails (bad key, no connection), the automatic labels
    are still written and the agreement table is skipped.
    """
    import httpx

    from societysim.analysis import jev
    from societysim.analysis.labeler import jev_verdicts, label_batch

    load_dotenv()
    if no_jev or not os.environ.get("TYPESAFE_API_KEY"):
        label_batch(batch_dir)
        why = "--no-jev" if no_jev else "TYPESAFE_API_KEY not set"
        typer.echo(f"Jev skipped ({why}); no self-tag vs Jev agreement")
        return
    cache = jev_cache.resolve() if jev_cache else jev.cache_dir_for(batch_dir)
    try:
        label_batch(
            batch_dir,
            jev_for=lambda events: jev_verdicts(
                asyncio.run(jev.classify_events(events, cache_dir=cache))
            ),
        )
    except httpx.HTTPError as e:
        # Only the status code or error class: messages can carry request details.
        why = (
            f"HTTP {e.response.status_code}"
            if isinstance(e, httpx.HTTPStatusError)
            else type(e).__name__
        )
        typer.echo(f"Jev failed ({why}); writing labels without Jev, no agreement table")
        label_batch(batch_dir)


if __name__ == "__main__":
    app()
