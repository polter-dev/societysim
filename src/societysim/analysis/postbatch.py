"""Everything to run after a batch finishes, in order, free by default.

    uv run python -m societysim.analysis.postbatch runs/<batch> [--jev] [--force]

Runs `label --no-jev`, `report`, `highlights`, then `narrate --dry-run` on the game of the
top highlight (a `lie_moved_vote` if there is one), each as a subprocess of the documented
command. Stops on the first failure. Then prints the numbers DEMO.md needs and the upload
commands to run next. Those are only printed, never run. `--jev` drops `--no-jev` from
`label`, which makes paid Jev calls when TYPESAFE_API_KEY is set (the CLI loads .env itself).
Without `--jev`, `label --no-jev` still keeps Jev labels for every statement in the batch's
Jev cache (no calls).
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

CLI = [sys.executable, "-m", "societysim.cli"]
WARN_STATUSES = ("failed", "budget_stopped", "stopped_invalid", "interrupted")


def run_step(name: str, cmd: list[str]) -> None:
    print(f"\n== {name}: {' '.join(cmd[1:])}", flush=True)
    if subprocess.run(cmd).returncode:
        sys.exit(f"postbatch: step '{name}' failed; stopped")


def _read(path: Path) -> dict | list:
    return json.loads(path.read_text(encoding="utf-8"))


def pick_highlight(moments: list[dict]) -> dict | None:
    """The top lie_moved_vote, else the top moment of any type (list is sorted by score)."""
    return next(
        (m for m in moments if m["type"] == "lie_moved_vote"), moments[0] if moments else None
    )


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.1%}"


def _has_jev_labels(batch: Path) -> bool:
    """True if any labels file holds a Jev label (e.g. `label --no-jev` filled them from cache)."""
    return any(
        '"source":"jev"' in p.read_text(encoding="utf-8") for p in batch.glob("*.labels.jsonl")
    )


def demo_block(batch: Path, top: dict | None, jev: bool = True) -> str:
    meta = _read(batch / "run_meta.json")
    rep = batch / "report"
    metrics, board = _read(rep / "metrics.json"), _read(rep / "leaderboard.json")
    lines = [
        "For DEMO.md",
        f"  games played: {metrics['games']}, unfinished: {metrics['unfinished_games']}",
        f"  spent: ${meta.get('spent_usd', 0):.2f}",
        "  top models by OpenSkill ordinal:",
    ]
    ranked = [m for m in board["models"] if m.get("openskill")][:3]
    for m in ranked:
        lo, hi = m["win_ci"] or (None, None)
        lines.append(
            f"    {m['model']}: ordinal {m['openskill']['ordinal']:.2f}, win rate "
            f"{_pct(m['win_rate'])} (95% CI {_pct(lo)} to {_pct(hi)})"
        )
    if not ranked:
        lines.append("    none (no rated models)")
    lies = metrics["by_group"]["all"]["lies"]
    for source, d in lies.items():
        if source == "jev" and not jev and not _has_jev_labels(batch):
            lines.append("  lie success (jev): Jev skipped (run with --jev)")
            continue
        ls = d["lie_success"]
        lines.append(
            f"  lie success ({source}): {d['lies']} lies, voters followed "
            f"{_pct(ls['share_followed'])} vs base rate {_pct(ls['base_rate'])}, "
            f"votes moved {_pct(ls['share_moved'])}"
        )
    if top:
        lines.append(
            f"  top highlight: {top['type']} in {top['game_id']}, seq "
            f"{top['seq'][0]}-{top['seq'][1]}, link {top['link']}"
        )
    else:
        lines.append("  top highlight: none")
    return "\n".join(lines)


def next_steps(batch: Path) -> str:
    return "\n".join(
        [
            "Next (not run; these upload or cost money):",
            f"  societysim publish {batch}",
            f"  societysim sync-neon {batch}",
            f"  HF_TOKEN=... scripts/deploy-space.sh {batch} polter-dev/societysim",
            "  scripts/databricks_ingest.py --runs runs/",
            "    (each ingest replaces the Delta tables, so it takes the parent runs/ dir,"
            " not one batch)",
        ]
    )


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Free post-batch pipeline: label, report, highlights.")
    p.add_argument("batch", type=Path, help="runs/<batch>")
    p.add_argument("--jev", action="store_true", help="let label call Jev (paid)")
    p.add_argument("--force", action="store_true", help="go on even if the batch is still running")
    a = p.parse_args(argv)
    batch: Path = a.batch

    meta_path = batch / "run_meta.json"
    if not meta_path.is_file():
        sys.exit(f"postbatch: {meta_path} not found")
    status = _read(meta_path).get("status")
    print(f"batch status: {status}")
    if status == "running" and not a.force:
        sys.exit("postbatch: batch is still running; wait for it or pass --force")
    if status in WARN_STATUSES:
        print(f"warning: batch status is {status}; numbers cover finished games only")

    run_step("label", [*CLI, "label", str(batch), *([] if a.jev else ["--no-jev"])])
    run_step("report", [*CLI, "report", str(batch)])
    run_step("highlights", [*CLI, "highlights", str(batch)])
    top = pick_highlight(_read(batch / "highlights.json"))
    if top:
        log = batch / f"{top['game_id']}.jsonl"
        run_step(
            "narrate", [sys.executable, "-m", "societysim.analysis.narrate", str(log), "--dry-run"]
        )
    else:
        print("\n== narrate: skipped, no highlights")
    print("\n" + demo_block(batch, top, a.jev) + "\n\n" + next_steps(batch))


if __name__ == "__main__":
    main()
