"""Render a batch into report.md, metrics.json, charts, leaderboard.json and index.json.

Everything goes to `<batch>/report/`. Metrics come from `analysis.metrics`; this module only
formats them. Lie metrics are computed for both label sources (`self_tag` and `jev`) and are
always shown side by side, never mixed.

leaderboard.json (read by the viewer, T4.9 and T6.2):

    {
      "schema": 1,
      "games": int,                        # distinct games in the batch
      "label_sources": ["self_tag", "jev"],
      "wolf_side_advantage": {"overall": {mu, sigma, ordinal}, "sided": {...}} | null,
      "models": [                          # sorted by openskill.ordinal, best first
        {
          "model": str,
          "games": int | null,             # distinct games the model played in (null if unknown)
          "seats": int,                    # seats it filled, summed over games
          "win_rate": float | null,        # 0..1 per seat (one result per seat), all roles
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
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Annotated, Any, Literal

import matplotlib
from pydantic import BaseModel, ConfigDict, Field

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from societysim.analysis.metrics import (  # noqa: E402
    HUMAN,
    LIE_TYPES,
    WOLF_SIDE,
    GameData,
    compute_metrics,
    load_finished,
)
from societysim.analysis.metrics import (  # noqa: E402
    SOURCES as LABEL_SOURCES,
)
from societysim.roles import Role, Winner  # noqa: E402

SCHEMA = 1


# --- output shapes (the data contract, docs/DATA_CONTRACT.md) --------------------------------
# These models only describe what this module writes; scripts/gen-schema.sh turns them into
# schema/{index,leaderboard,metrics}.schema.json. The writers below build plain dicts.

Seat = Annotated[str, Field(pattern=r"^p[0-8]$")]
Group = Literal["all", "wolf", "village", "seer", "doctor", "villager"]
LieType = Literal["false_role_claim", "false_report", "bussing", "hidden_knowledge"]
Num = float | None


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IndexSeat(_Out):
    name: str
    model: str
    role: Role


class IndexEntry(_Out):
    """One entry of report/index.json. `file` is relative to the batch dir."""

    game_id: str
    file: str
    seed: int
    days: int
    winner: Winner | None
    seats: dict[Seat, IndexSeat]


GameIndex = list[IndexEntry]


class Rating(_Out):
    mu: float
    sigma: float
    ordinal: float


class SideAdvantage(_Out):
    """The wolf-side dummy player's rating per pool; {} when no game had a winner."""

    overall: Rating = None  # type: ignore[assignment]  # absent, never null
    sided: Rating = None  # type: ignore[assignment]


class LieRate(_Out):
    self_tag: Num
    jev: Num


class BoardRow(_Out):
    model: str
    games: int | None
    seats: int
    win_rate: Num
    win_ci: tuple[float, float] | None
    wolf_win_rate: Num
    village_win_rate: Num
    openskill: Rating | None
    detection_auc: Num
    detection_brier: Num
    invalid_output_rate: Num
    cost_usd_per_game: Num
    lie_rate: LieRate


class Leaderboard(_Out):
    """report/leaderboard.json."""

    schema_: Literal[1] = Field(alias="schema")
    games: int
    label_sources: list[Literal["self_tag", "jev"]]
    wolf_side_advantage: SideAdvantage | None
    models: list[BoardRow]


class LieSuccess(_Out):
    exploratory: bool
    comparisons: int
    moved: int
    voters: int
    followed: int
    base_voters: int
    base_followed: int
    base_abstained: int
    share_moved: Num
    share_followed: Num
    base_rate: Num


class Lies(_Out):
    lies: int
    by_type: dict[LieType, int]
    lie_rate: Num
    lie_success: LieSuccess


class LiesBySource(_Out):
    self_tag: Lies
    jev: Lies


class DetectionDay(_Out):
    n: int
    brier: Num
    auc: Num


class Detection(_Out):
    n: int
    brier: Num
    auc: Num
    by_day: dict[Annotated[str, Field(pattern=r"^\d+$")], DetectionDay]


class Persuasion(_Out):
    exploratory: bool
    accusations: int
    voters: int
    hits: int
    share: Num


class GroupStats(_Out):
    games: int
    wins: int
    draws: int
    losses: int
    win_rate: Num
    win_ci: tuple[float, float] | None
    statements: int
    lies: LiesBySource
    detection: Detection
    persuasion: Persuasion
    survival_days: Num
    night_targeted: Num
    voted_out: Num
    invalid_output_rate: Num
    tokens_per_game: Num
    cost_usd_per_game: Num


class OpenSkill(_Out):
    overall: dict[str, Rating]
    wolf: dict[str, Rating]
    village: dict[str, Rating]
    wolf_side: SideAdvantage


class SummaryLies(_Out):
    lies: int
    statements: int
    lie_rate: Num
    lie_success: LieSuccess


class TopOpenSkill(_Out):
    model: str
    ordinal: float


class TopWolfWin(_Out):
    model: str
    win_rate: float
    seats: int


class TopShareMoved(_Out):
    model: str
    share_moved: float
    comparisons: int


class Summary(_Out):
    games: int
    unfinished_games: int
    cost_usd: float
    input_tokens: int
    output_tokens: int
    wolf_wins: int
    village_wins: int
    draws: int
    wolf_win_share: Num
    village_win_share: Num
    lies: dict[Literal["self_tag", "jev"], SummaryLies]
    top_openskill: TopOpenSkill | None
    top_wolf_win: TopWolfWin | None
    top_share_moved: dict[Literal["self_tag", "jev"], TopShareMoved | None]
    min_comparisons: int


class Speed(_Out):
    calls: int
    timed_calls: int
    median_latency_ms: Num
    p90_latency_ms: int | None
    output_tokens_per_s: Num
    tokens_per_game: Num
    cost_usd_per_game: Num
    self_hosted: bool


class Metrics(_Out):
    """report/metrics.json: analysis.metrics.compute_metrics plus summary and speed."""

    games: int
    unfinished_games: int
    label_sources: list[Literal["self_tag", "jev"]]
    exploratory: list[Literal["lie_success", "persuasion"]]
    models: dict[str, dict[Group, GroupStats]]
    by_group: dict[Group, GroupStats]
    openskill: OpenSkill
    summary: Summary
    speed: dict[str, Speed]


def _load(batch_dir: Path) -> tuple[list[tuple[Path, GameData]], int]:
    return load_finished(batch_dir)


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


def model_game_counts(games: list[GameData]) -> dict[str, int]:
    """Distinct games each model played in (a model can fill several seats in one game)."""
    seen: dict[str, set[str]] = {}
    for g in games:
        for model in set(g.models.values()):
            seen.setdefault(model, set()).add(g.game_id)
    return {m: len(ids) for m, ids in seen.items()}


def build_leaderboard(
    metrics: dict,
    present: set[str] | None = None,
    model_games: dict[str, int] | None = None,
) -> dict:
    present = set(LABEL_SOURCES) if present is None else present
    base = metrics
    ratings = base["openskill"]["overall"]
    rows = []
    for model, groups in base["models"].items():
        a = groups.get("all", {})
        rows.append(
            {
                "model": model,
                "games": (model_games or {}).get(model),
                "seats": a.get("games", 0),
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


MIN_COMPARISONS = 5  # fewer lie-success comparisons than this is too thin to rank a model on


def build_summary(metrics: dict, games: list[GameData]) -> dict:
    """The headline numbers: batch-wide totals and the top model on a few measures."""
    allg = metrics["by_group"]["all"]
    wins: dict[str | None, int] = defaultdict(int)
    for g in games:
        wins[g.winner] += 1
    n = len(games)
    usages = [e.meta.usage for g in games for e in g.events if e.meta.usage]
    ratings = metrics["openskill"]["overall"]
    best = max(ratings.items(), key=lambda kv: kv[1]["ordinal"], default=None)
    wolf_rows = [
        (m, g["wolf"]) for m, g in metrics["models"].items() if g.get("wolf", {}).get("games")
    ]
    wolf_best = max(wolf_rows, key=lambda kv: kv[1]["win_rate"], default=None)
    lies, moved = {}, {}
    for src in LABEL_SOURCES:
        lies[src] = {
            "lies": allg["lies"][src]["lies"],
            "statements": allg["statements"],
            "lie_rate": allg["lies"][src]["lie_rate"],
            "lie_success": allg["lies"][src]["lie_success"],
        }
        cands = [
            (m, g["all"]["lies"][src]["lie_success"])
            for m, g in metrics["models"].items()
            if g["all"]["lies"][src]["lie_success"]["comparisons"] >= MIN_COMPARISONS
        ]
        top = max(cands, key=lambda kv: kv[1]["share_moved"], default=None)
        moved[src] = (
            {
                "model": top[0],
                "share_moved": top[1]["share_moved"],
                "comparisons": top[1]["comparisons"],
            }
            if top
            else None
        )
    return {
        "games": n,
        "unfinished_games": metrics["unfinished_games"],
        "cost_usd": sum(u.cost_usd for u in usages),
        "input_tokens": sum(u.input_tokens for u in usages),
        "output_tokens": sum(u.output_tokens for u in usages),
        "wolf_wins": wins["wolves"],
        "village_wins": wins["villagers"],
        "draws": wins["draw"],
        "wolf_win_share": wins["wolves"] / n if n else None,
        "village_win_share": wins["villagers"] / n if n else None,
        "lies": lies,
        "top_openskill": {"model": best[0], "ordinal": best[1]["ordinal"]} if best else None,
        "top_wolf_win": (
            {
                "model": wolf_best[0],
                "win_rate": wolf_best[1]["win_rate"],
                "seats": wolf_best[1]["games"],
            }
            if wolf_best
            else None
        ),
        "top_share_moved": moved,
        "min_comparisons": MIN_COMPARISONS,
    }


def build_speed(metrics: dict, games: list[GameData]) -> dict[str, dict]:
    """Per model: calls, latency and throughput. Cached calls and zero latency are left out."""
    lat: dict[str, list[int]] = defaultdict(list)
    out_tok: dict[str, int] = defaultdict(int)
    calls: dict[str, int] = defaultdict(int)
    cost: dict[str, float] = defaultdict(float)
    for g in games:
        models = g.models
        for e in g.events:
            u = e.meta.usage
            m = models.get(e.actor) if e.actor else None
            if not u or not m or m == HUMAN:
                continue
            calls[m] += 1
            cost[m] += u.cost_usd
            if not u.cached and u.latency_ms > 0:
                lat[m].append(u.latency_ms)
                out_tok[m] += u.output_tokens
    res = {}
    for m, groups in metrics["models"].items():
        a = groups["all"]
        ls = sorted(lat[m])
        total_ms = sum(ls)
        res[m] = {
            "calls": calls[m],
            "timed_calls": len(ls),
            "median_latency_ms": statistics.median(ls) if ls else None,
            "p90_latency_ms": ls[max(math.ceil(0.9 * len(ls)) - 1, 0)] if ls else None,
            "output_tokens_per_s": out_tok[m] / (total_ms / 1000) if total_ms else None,
            "tokens_per_game": a["tokens_per_game"],
            "cost_usd_per_game": a["cost_usd_per_game"],
            "self_hosted": calls[m] > 0 and cost[m] == 0,
        }
    return res


def _table(head: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return lines + [""]


def _n(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _headline(sm: dict, present: set[str]) -> list[str]:
    lines = ["## Headline", ""]
    lines.append(f"- Games: {sm['games']} finished, {sm['unfinished_games']} unfinished.")
    lines.append(
        f"- Spend: ${sm['cost_usd']:.2f}; tokens: {sm['input_tokens'] + sm['output_tokens']:,} "
        f"({sm['input_tokens']:,} in, {sm['output_tokens']:,} out)."
    )
    lines.append(
        f"- Wins: wolves {sm['wolf_wins']} ({_pct(sm['wolf_win_share'])}), villagers "
        f"{sm['village_wins']} ({_pct(sm['village_win_share'])})"
        + (f", draws {sm['draws']}." if sm["draws"] else ".")
    )
    for src in LABEL_SOURCES:
        if src not in present:
            lines.append(f"- Lies ({src}): no labels.")
            continue
        li = sm["lies"][src]
        ls = li["lie_success"]
        lines.append(
            f"- Lies ({src}): {_n(li['lies'], 'lie')} in {_n(li['statements'], 'statement')} "
            f"({_pct(li['lie_rate'])}). Lies swayed {_pct(ls['share_moved'])} of the "
            f"{_n(ls['comparisons'], 'suspicion')} they targeted; {_pct(ls['share_followed'])} of "
            f"{_n(ls['voters'], 'vote')} followed the lie, against a no-lie base rate of "
            f"{_pct(ls['base_rate'])} ({_n(ls['base_voters'], 'vote')})."
        )
    if t := sm["top_openskill"]:
        lines.append(f"- Top OpenSkill: {t['model']} (ordinal {_num(t['ordinal'])}).")
    if t := sm["top_wolf_win"]:
        lines.append(
            f"- Top wolf win rate: {t['model']} "
            f"({_pct(t['win_rate'])} over {_n(t['seats'], 'wolf seat')})."
        )
    for src in LABEL_SOURCES:
        if src not in present:
            continue
        t = sm["top_share_moved"][src]
        lines.append(
            f"- Highest lie success ({src}, at least {sm['min_comparisons']} comparisons): "
            + (
                f"{t['model']} ({_pct(t['share_moved'])} of {_n(t['comparisons'], 'comparison')})."
                if t
                else "none qualifies."
            )
        )
    return lines + [""]


def _results(metrics: dict, board: dict, present: set[str]) -> list[str]:
    def rate(m: str, src: str) -> str:
        if src not in present:
            return "n/a"
        return _pct(metrics["models"][m]["all"]["lies"][src]["lie_rate"])

    def moved(m: str, src: str) -> str:
        if src not in present:
            return "n/a"
        ls = metrics["models"][m]["all"]["lies"][src]["lie_success"]
        return f"{_pct(ls['share_moved'])} (n={ls['comparisons']})"

    rows = []
    for r in board["models"]:
        m, ci = r["model"], r["win_ci"]
        rows.append(
            [
                m,
                "n/a" if r["games"] is None else str(r["games"]),
                _pct(r["win_rate"]) + (f" ({_pct(ci[0])} to {_pct(ci[1])})" if ci else ""),
                _num(r["openskill"]["ordinal"]) if r["openskill"] else "n/a",
                f"{rate(m, 'self_tag')} / {rate(m, 'jev')}",
                f"{moved(m, 'self_tag')} / {moved(m, 'jev')}",
                _num(r["detection_auc"]),
            ]
        )
    head = [
        "Model",
        "Games",
        "Win rate (95% CI)",
        "OpenSkill ordinal",
        "Lie rate (self_tag / jev)",
        "Lie success, moved (self_tag / jev)",
        "Detection AUC",
    ]
    return [
        "## Results",
        "",
        "Win rate is per seat. Lie columns show self_tag / jev.",
        "",
        *_table(head, rows),
    ]


def _lie_types(metrics: dict, present: set[str]) -> list[str]:
    types = [t.value for t in LIE_TYPES]
    rows = []
    for m, groups in metrics["models"].items():
        row = [m]
        for src in LABEL_SOURCES:
            by_type = groups["all"]["lies"][src]["by_type"]
            row += [str(by_type.get(t, 0)) if src in present else "n/a" for t in types]
        rows.append(row)
    head = ["Model"] + [f"{t} ({src})" for src in LABEL_SOURCES for t in types]
    return ["### Lie types", "", "Lie counts by kind, per label source.", "", *_table(head, rows)]


def _speed(speed: dict) -> list[str]:
    def secs(x):
        return "n/a" if x is None else f"{x / 1000:.1f} s"

    rows = []
    for m, s in speed.items():
        cost, tps, tpg = s["cost_usd_per_game"], s["output_tokens_per_s"], s["tokens_per_game"]
        usd = "0 (self-hosted)" if s["self_hosted"] else "n/a" if cost is None else f"{cost:.4f}"
        rows.append(
            [
                m,
                str(s["calls"]),
                secs(s["median_latency_ms"]),
                secs(s["p90_latency_ms"]),
                "n/a" if tps is None else f"{tps:.0f}",
                "n/a" if tpg is None else f"{tpg:,.0f}",
                usd,
            ]
        )
    head = [
        "Model",
        "Calls",
        "Median latency",
        "p90 latency",
        "Output tok/s",
        "Tokens per game",
        "USD per game",
    ]
    return [
        "## Speed",
        "",
        "Latency is per call; tok/s is total output tokens over total latency. Cached calls and "
        "calls with zero latency are left out of both.",
        "",
        *_table(head, rows),
    ]


def render_markdown(
    metrics: dict,
    board: dict,
    present: set[str] | None = None,
    summary: dict | None = None,
    speed: dict | None = None,
) -> str:
    present = set(LABEL_SOURCES) if present is None else present
    lines = [
        "# Batch report",
        "",
        *(_headline(summary, present) if summary else []),
        *_results(metrics, board, present),
        f"{board['games']} games. Win rate is per seat: each seat a model fills counts once, "
        "and a model can fill several seats in one game. Win rates carry a Wilson 95% interval; "
        "small batches are noisy.",
        "",
        "## Leaderboard",
        "",
        "Ranked by OpenSkill ordinal (equal-weight sides plus a side-advantage dummy player).",
        "",
    ]
    if metrics.get("unfinished_games"):
        n = metrics["unfinished_games"]
        at = lines.index("## Leaderboard")
        lines[at:at] = [f"{n} unfinished games (no game_end) were skipped.", ""]
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
        [
            "Model",
            "Games",
            "Seats",
            "Win rate (per seat)",
            "95% CI",
            "Wolf win",
            "Village win",
            "Ordinal",
        ],
        [
            [
                r["model"],
                "n/a" if r["games"] is None else str(r["games"]),
                str(r["seats"]),
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
        "the base rate of the same vote when no lie was told. Counts in brackets are the "
        "denominators; a base rate on a handful of votes means little. Abstains count in the base "
        "and never follow the lie; where the base has any, the bracket shows how many.",
        "",
    ]
    head = ["Model", "Source", "Moved", "Voted as lie implied", "No-lie base rate"]
    rows = []
    for m in metrics["models"]:
        for s in LABEL_SOURCES:
            ls = metrics["models"][m]["all"]["lies"][s]["lie_success"]
            abst = ls["base_abstained"]
            base_n = f"{ls['base_voters']}, {abst} abstain" if abst else f"{ls['base_voters']}"
            rows.append(
                [
                    m,
                    s,
                    f"{_pct(ls['share_moved'])} ({ls['comparisons']})",
                    f"{_pct(ls['share_followed'])} ({ls['voters']})",
                    f"{_pct(ls['base_rate'])} ({base_n})",
                ]
            )
    lines += _table(head, rows)
    lines += _lie_types(metrics, present)
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
    if speed:
        lines += _speed(speed)
    lines += [
        "## Charts",
        "",
        "![Win rate](win_rate.png)",
        "",
        "![Detection AUC](detection_auc.png)",
        "",
        "![OpenSkill](openskill.png)",
        "",
        "![Lie rate](lie_rate.png)",
        "",
        "## leaderboard.json shape",
        "",
        "`leaderboard.json` holds `{schema, games, label_sources, models[]}`. Each entry in "
        "`models` (best OpenSkill ordinal first) has `model`, `games` (distinct games), `seats` "
        "(seats filled, which is what win rate is counted over), `win_rate`, `win_ci`, "
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
    _bar(
        out / "lie_rate.png",
        "Lie rate",
        "lies per statement",
        {src: [r["lie_rate"][src] for r in rows] for src in LABEL_SOURCES},
        names,
    )
    return ["win_rate.png", "detection_auc.png", "openskill.png", "lie_rate.png"]


def write_report(batch_dir: Path) -> Path:
    """Write the whole report directory; returns its path."""
    games, unfinished = _load(batch_dir)
    if not games:
        raise ValueError(f"no game logs in {batch_dir}")
    out = batch_dir / "report"
    out.mkdir(exist_ok=True)
    data = [g for _, g in games]
    metrics: dict[str, Any] = compute_metrics(data, unfinished)
    present = {lb.source for g in data for lb in g.labels}
    board = build_leaderboard(metrics, present, model_game_counts(data))
    write_charts(board, out)

    def dump(name: str, obj) -> None:
        (out / name).write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")

    summary, speed = build_summary(metrics, data), build_speed(metrics, data)
    dump("metrics.json", {**metrics, "summary": summary, "speed": speed})
    dump("leaderboard.json", board)
    dump("index.json", build_index(games))
    (out / "report.md").write_text(
        render_markdown(metrics, board, present, summary, speed), encoding="utf-8"
    )
    return out
