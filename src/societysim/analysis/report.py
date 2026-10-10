"""Render a batch into report.md, metrics.json, charts, leaderboard.json and index.json.

Everything goes to `<batch>/report/`. Metrics come from `analysis.metrics`; this module only
formats them. Lie metrics are computed for both label sources (`self_tag` and `jev`) and are
always shown side by side, never mixed.

leaderboard.json (read by the viewer, T4.9 and T6.2):

    {
      "schema": 1,
      "games": int,                        # games in the batch
      "label_sources": ["self_tag", "jev"],
      "wolf_side_advantage": {"overall": {mu, sigma, ordinal}, "sided": {...}} | null,
      "models": [                          # sorted by openskill.ordinal, best first
        {
          "model": str,
          "games": int,                    # seat-games played
          "win_rate": float | null,        # 0..1, all roles
          "win_ci": [lo, hi] | null,       # Wilson 95%
          "wolf_win_rate": float | null,   # null when the model never played wolf
          "village_win_rate": float | null,
          "openskill": {"mu": float, "sigma": float, "ordinal": float} | null,
          "detection_auc": float | null,   # village seats: suspicion vs true wolf
          "detection_brier": float | null,
          "invalid_output_rate": float | null,
          "cost_usd_per_game": float | null,
          "lie_rate": {"self_tag": float | null, "jev": float | null}
        }
      ]
    }

index.json: a list of {game_id, file, seed, days, winner, seats: {seat: {name, model, role}}}.
"""

import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from societysim.analysis.metrics import (  # noqa: E402
    SOURCES as LABEL_SOURCES,
)
from societysim.analysis.metrics import (  # noqa: E402
    WOLF_SIDE,
    GameData,
    compute_metrics,
    load_game,
)

SCHEMA = 1


def _load(batch_dir: Path) -> list[tuple[Path, GameData]]:
    logs = sorted(p for p in batch_dir.glob("*.jsonl") if not p.name.endswith(".labels.jsonl"))
    return [(p, load_game(p)) for p in logs if p.stat().st_size]


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.0f}%"


def _num(x: float | None, nd: int = 2) -> str:
    return "n/a" if x is None else f"{x:.{nd}f}"


def build_index(games: list[tuple[Path, GameData]]) -> list[dict]:
    out = []
    for path, g in games:
        start = g.events[0].meta
        out.append(
            {
                "game_id": g.game_id,
                "file": path.name,
                "seed": start.seed,
                "days": max(e.day for e in g.events),
                "winner": g.winner,
                "seats": {
                    s: {"name": seat.name, "model": seat.model, "role": seat.role.value}
                    for s, seat in (start.seats or {}).items()
                },
            }
        )
    return out


def build_leaderboard(metrics: dict, present: set[str] | None = None) -> dict:
    present = set(LABEL_SOURCES) if present is None else present
    base = metrics
    ratings = base["openskill"]["overall"]
    rows = []
    for model, groups in base["models"].items():
        a = groups.get("all", {})
        rows.append(
            {
                "model": model,
                "games": a.get("games", 0),
                "win_rate": a.get("win_rate"),
                "win_ci": a.get("win_ci"),
                "wolf_win_rate": groups.get("wolf", {}).get("win_rate"),
                "village_win_rate": groups.get("village", {}).get("win_rate"),
                "openskill": ratings.get(model),
                "detection_auc": groups.get("village", {}).get("detection", {}).get("auc"),
                "detection_brier": groups.get("village", {}).get("detection", {}).get("brier"),
                "invalid_output_rate": a.get("invalid_output_rate"),
                "cost_usd_per_game": a.get("cost_usd_per_game"),
                "lie_rate": {
                    # no labels from a source means unknown, not a 0% lie rate
                    src: groups["all"]["lies"][src]["lie_rate"] if src in present else None
                    for src in LABEL_SOURCES
                },
            }
        )
    rows.sort(key=lambda r: -(r["openskill"]["ordinal"] if r["openskill"] else float("-inf")))
    return {
        "schema": SCHEMA,
        "games": base["games"],
        "label_sources": list(LABEL_SOURCES),
        "wolf_side_advantage": base["openskill"].get(WOLF_SIDE),
        "models": rows,
    }


def _table(head: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return lines + [""]


def render_markdown(metrics: dict, board: dict, present: set[str] | None = None) -> str:
    present = set(LABEL_SOURCES) if present is None else present
    lines = [
        "# Batch report",
        "",
        f"{board['games']} games. Win rates carry a Wilson 95% interval; small batches are noisy.",
        "",
        "## Leaderboard",
        "",
        "Ranked by OpenSkill ordinal (equal-weight sides plus a side-advantage dummy player).",
        "",
    ]
    side = board.get("wolf_side_advantage")
    if side:
        o = side["overall"]
        lines += [
            f"Wolf-side advantage (dummy player): mu {_num(o['mu'])}, compared with its prior "
            "mu of 0 (mu, not ordinal). Above 0 means the wolf side wins more than expected; "
            "it is not a model.",
            "",
        ]
    lines += _table(
        ["Model", "Games", "Win rate", "95% CI", "Wolf win", "Village win", "Ordinal"],
        [
            [
                r["model"],
                str(r["games"]),
                _pct(r["win_rate"]),
                "n/a" if not r["win_ci"] else f"{_pct(r['win_ci'][0])} to {_pct(r['win_ci'][1])}",
                _pct(r["wolf_win_rate"]),
                _pct(r["village_win_rate"]),
                _num(r["openskill"]["ordinal"]) if r["openskill"] else "n/a",
            ]
            for r in board["models"]
        ],
    )
    lines += [
        "## Detection",
        "",
        "How well village seats rank the real wolves, from each seat's last suspicion of the day.",
        "AUC 0.5 is chance; Brier lower is better.",
        "",
    ]
    lines += _table(
        ["Model", "AUC", "Brier"],
        [
            [r["model"], _num(r["detection_auc"]), _num(r["detection_brier"])]
            for r in board["models"]
        ],
    )
    lines += [
        "## Lies",
        "",
        "Lie labels come from two sources, shown side by side and never mixed: `self_tag` (the "
        "speaker marked it) and `jev` (a judge model found it).",
        "",
    ]
    lines += _table(
        ["Model", "Statements", "Lie rate (self_tag)", "Lie rate (jev)"],
        [
            [
                m,
                str(metrics["models"][m]["all"]["statements"]),
                *(
                    _pct(
                        metrics["models"][m]["all"]["lies"][s]["lie_rate"] if s in present else None
                    )
                    for s in LABEL_SOURCES
                ),
            ]
            for m in metrics["models"]
        ],
    )
    lines += [
        "### Lie success (exploratory)",
        "",
        "Exploratory: this reads correlation, not cause. 'Moved' is the share of observer "
        "suspicions that shifted the way the liar wanted. 'Voted as lie implied' is read against "
        "the base rate of the same vote when no lie was told.",
        "",
    ]
    head = ["Model", "Source", "Moved", "Voted as lie implied", "No-lie base rate"]
    rows = []
    for m in metrics["models"]:
        for s in LABEL_SOURCES:
            ls = metrics["models"][m]["all"]["lies"][s]["lie_success"]
            rows.append(
                [
                    m,
                    s,
                    f"{_pct(ls['share_moved'])} ({ls['comparisons']})",
                    f"{_pct(ls['share_followed'])} ({ls['voters']})",
                    _pct(ls["base_rate"]),
                ]
            )
    lines += _table(head, rows)
    lines += [
        "### Persuasion (exploratory)",
        "",
        "Exploratory: share of voters who voted for a player after the speaker accused them.",
        "",
    ]
    prow = []
    for m in metrics["models"]:
        p = metrics["models"][m]["all"]["persuasion"]
        prow.append([m, str(p["accusations"]), str(p["voters"]), _pct(p["share"])])
    lines += _table(["Model", "Accusations", "Voters", "Followed"], prow)
    lines += [
        "## Cost and reliability",
        "",
    ]
    lines += _table(
        ["Model", "Invalid output rate", "USD per game"],
        [
            [
                r["model"],
                _pct(r["invalid_output_rate"]),
                "n/a" if r["cost_usd_per_game"] is None else f"{r['cost_usd_per_game']:.4f}",
            ]
            for r in board["models"]
        ],
    )
    lines += [
        "## Charts",
        "",
        "![Win rate](win_rate.png)",
        "",
        "![Detection AUC](detection_auc.png)",
        "",
        "![OpenSkill](openskill.png)",
        "",
        "## leaderboard.json shape",
        "",
        "`leaderboard.json` holds `{schema, games, label_sources, models[]}`. Each entry in "
        "`models` (best OpenSkill ordinal first) has `model`, `games`, `win_rate`, `win_ci`, "
        "`wolf_win_rate`, `village_win_rate`, `openskill {mu, sigma, ordinal}`, `detection_auc`, "
        "`detection_brier`, `invalid_output_rate`, `cost_usd_per_game` and "
        "`lie_rate {self_tag, jev}`, plus top-level `wolf_side_advantage`. "
        "Any value can be null when there is no data. The full "
        "field list is in the `analysis/report.py` docstring.",
        "",
    ]
    return "\n".join(lines)


def _bar(path: Path, title: str, ylabel: str, series: dict[str, list[float | None]], labels):
    fig, ax = plt.subplots(figsize=(7, 4))
    width = 0.8 / max(len(series), 1)
    for i, (name, vals) in enumerate(series.items()):
        xs = [j + i * width for j in range(len(labels))]
        ax.bar(xs, [v or 0 for v in vals], width, label=name)
    ax.set_xticks([j + 0.4 - width / 2 for j in range(len(labels))])
    ax.set_xticklabels(labels, rotation=30, ha="right")
    ax.set_title(title)
    ax.set_ylabel(ylabel)
    if len(series) > 1:
        ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def write_charts(board: dict, out: Path) -> list[str]:
    rows = board["models"]
    names = [r["model"] for r in rows]
    _bar(
        out / "win_rate.png",
        "Win rate",
        "win rate",
        {
            "all": [r["win_rate"] for r in rows],
            "wolf": [r["wolf_win_rate"] for r in rows],
            "village": [r["village_win_rate"] for r in rows],
        },
        names,
    )
    _bar(
        out / "detection_auc.png",
        "Wolf detection (village seats)",
        "AUC",
        {"auc": [r["detection_auc"] for r in rows]},
        names,
    )
    _bar(
        out / "openskill.png",
        "OpenSkill ordinal",
        "ordinal",
        {"ordinal": [r["openskill"]["ordinal"] if r["openskill"] else None for r in rows]},
        names,
    )
    return ["win_rate.png", "detection_auc.png", "openskill.png"]


def write_report(batch_dir: Path) -> Path:
    """Write the whole report directory; returns its path."""
    games = _load(batch_dir)
    if not games:
        raise ValueError(f"no game logs in {batch_dir}")
    out = batch_dir / "report"
    out.mkdir(exist_ok=True)
    data = [g for _, g in games]
    metrics: dict[str, Any] = compute_metrics(data)
    present = {lb.source for g in data for lb in g.labels}
    board = build_leaderboard(metrics, present)
    write_charts(board, out)

    def dump(name: str, obj) -> None:
        (out / name).write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")

    dump("metrics.json", metrics)
    dump("leaderboard.json", board)
    dump("index.json", build_index(games))
    (out / "report.md").write_text(render_markdown(metrics, board, present), encoding="utf-8")
    return out
