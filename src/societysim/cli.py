"""Command-line entry point. Subcommands are added by their tickets (play, batch, doctor, ...)."""

from pathlib import Path

import typer

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
def label(batch_dir: Path) -> None:
    """Write <game_id>.labels.jsonl next to each game log in BATCH_DIR."""
    from societysim.analysis.labeler import label_batch

    label_batch(batch_dir)


if __name__ == "__main__":
    app()
