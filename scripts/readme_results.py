"""Rewrite the results block in README.md from finished batch reports.

  python scripts/readme_results.py runs/<batch> [runs/<batch> ...] [--readme README.md]

Reads <batch>/report/leaderboard.json and metrics.json (and report/index.json for the player
count) and replaces only the text between the results markers in the README. Same input gives
the same bytes, so a second run changes nothing. With no batches the block is a placeholder.
No network, nothing is written except the README.
"""

import argparse
import json
import sys
from pathlib import Path

from societysim.logging.textio import read_text_lenient

START = "<!-- results:start -->"
END = "<!-- results:end -->"
PLACEHOLDER = (
    "No results yet. After a batch has a report, run "
    "`python scripts/readme_results.py runs/<batch>` to fill this in."
)


def _load(path: Path) -> dict | list:
    return json.loads(read_text_lenient(path))


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def _num(x: float | None, nd: int = 2) -> str:
    return "n/a" if x is None else f"{x:.{nd}f}"


def _win(row: dict) -> str:
    ci = row.get("win_ci")
    if row.get("win_rate") is None:
        return "n/a"
    if not ci:
        return _pct(row["win_rate"])
    return f"{_pct(row['win_rate'])} ({_pct(ci[0])} to {_pct(ci[1])})"


def _players(report: Path) -> int | None:
    index = report / "index.json"
    if not index.exists():
        return None
    sizes = [len(g.get("seats") or {}) for g in _load(index)]
    return max(sizes) if sizes and max(sizes) else None


def _wolf_line(adv: dict) -> str:
    if not adv:
        return "Wolf-side advantage: not available (no game had a winner)."
    parts = []
    for key, label in (("overall", "overall pool"), ("sided", "sided pool")):
        r = adv.get(key)
        if r:
            parts.append(f"{label} mu {r['mu']:+.2f} (sigma {r['sigma']:.2f})")
    main = adv.get("overall") or adv.get("sided")
    verdict = ""
    if main:
        if abs(main["mu"]) > 2 * main["sigma"]:
            verdict = " Clearly away from 0."
        else:
            verdict = " Within 2 sigma of 0, so no clear advantage yet."
    return (
        "Wolf-side advantage (OpenSkill rating of the wolf side as a player; 0 is even, above 0 "
        "favors the wolves): " + "; ".join(parts) + "." + verdict
    )


def render_batch(batch: Path) -> str:
    report = batch / "report"
    lb = _load(report / "leaderboard.json")
    metrics = _load(report / "metrics.json")
    rows = lb["models"]
    players = _players(report)
    unfinished = metrics.get("unfinished_games") or 0
    spend = metrics.get("summary", {}).get("cost_usd")
    adv = lb.get("wolf_side_advantage") or metrics.get("openskill", {}).get("wolf_side") or {}

    head = [f"batch `{batch.name}`"]
    if players:
        head.append(f"{players} players")
    head.append(f"{lb['games']} games")
    head.append("spend n/a" if spend is None else f"spend ${spend:.2f}")
    out = ["### " + ", ".join(head), ""]
    out.append(
        "| Model | Games / seats | Win rate (95% CI) | OpenSkill ordinal | Detection AUC "
        "| Lie rate: self-tag | Lie rate: Jev | Invalid output rate |"
    )
    out.append("|---|---|---|---|---|---|---|---|")
    for r in rows:
        rating = r.get("openskill")
        lie = r.get("lie_rate") or {}
        out.append(
            f"| {r['model']} | {r.get('games', 'n/a')} / {r.get('seats', 'n/a')} | {_win(r)} "
            f"| {_num(rating['ordinal']) if rating else 'n/a'} | {_num(r.get('detection_auc'))} "
            f"| {_pct(lie.get('self_tag'))} | {_pct(lie.get('jev'))} "
            f"| {_pct(r.get('invalid_output_rate'))} |"
        )
    out.append("")
    out.append(_wolf_line(adv))
    out.append("")
    seats = [r["seats"] for r in rows if r.get("seats") is not None]
    sample = f"Sample size: {lb['games']} finished games"
    if unfinished:
        sample += f" ({unfinished} more did not finish and are not counted)"
    if seats:
        sample += f"; each model filled {min(seats)} to {max(seats)} seats"
    sample += (
        ". Wide intervals mean a gap between two models may be chance. Lie rates are lies per "
        "statement; n/a means that label source has no labels in this batch."
    )
    out.append(sample)
    return "\n".join(out)


def render(batches: list[Path]) -> str:
    if not batches:
        return PLACEHOLDER
    return "\n\n".join(render_batch(b) for b in batches)


def update_readme(readme: Path, batches: list[Path]) -> bool:
    """Rewrite the block between the markers. Returns True if the file changed."""
    text = read_text_lenient(readme)
    start, end = text.find(START), text.find(END)
    if start == -1 or end == -1 or end < start:
        raise SystemExit(f"{readme}: results markers not found ({START} ... {END})")
    new = text[: start + len(START)] + "\n" + render(batches) + "\n" + text[end:]
    if new == text:
        return False
    readme.write_bytes(new.encode("utf-8"))
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("runs", nargs="*", type=Path, help="batch dirs, e.g. runs/<batch_id>")
    ap.add_argument("--readme", type=Path, default=Path("README.md"))
    args = ap.parse_args(argv)
    try:
        changed = update_readme(args.readme, args.runs)
    except FileNotFoundError as e:
        print(f"missing file: {e.filename}", file=sys.stderr)
        return 1
    print(f"{args.readme}: {'updated' if changed else 'unchanged'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
