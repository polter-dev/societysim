"""Command-line entry point. Subcommands are added by their tickets (play, batch, doctor, ...)."""

import asyncio
import os
from pathlib import Path

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
) -> None:
    """Write <game_id>.labels.jsonl next to each game log in BATCH_DIR.

    With TYPESAFE_API_KEY set, also classify statements with Jev (cached in .cache/jev)
    and write the self-tag vs Jev agreement table to BATCH_DIR/agreement.json.
    """
    from societysim.analysis import jev
    from societysim.analysis.labeler import jev_verdicts, label_batch

    load_dotenv()
    if no_jev or not os.environ.get("TYPESAFE_API_KEY"):
        label_batch(batch_dir)
        why = "--no-jev" if no_jev else "TYPESAFE_API_KEY not set"
        typer.echo(f"Jev skipped ({why}); no self-tag vs Jev agreement")
        return
    label_batch(
        batch_dir, jev_for=lambda events: jev_verdicts(asyncio.run(jev.classify_events(events)))
    )


if __name__ == "__main__":
    app()
