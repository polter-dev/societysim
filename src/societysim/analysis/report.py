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
          "invalid_output_rate": float | null,   # rejected replies / model call attempts
          "invalid_attempt_rate": float | null,  # same number, clearer name
          "fallback_rate": float | null,         # turns that ended in a fallback move / turns
          "retry_success_rate": float | null,    # accepted retries / retries
          "cost_usd_per_game": float | null,
          "lie_rate": {"self_tag": float | null, "jev": float | null}
        }
      ]
    }

index.json: a list of {game_id, file, seed, days, winner, seats: {seat: {name, model, role}}}.

metrics.json `profiles`: model -> one model's profile page (leaderboard numbers, detection,
median latency, narration voice, and who it fools / who fools it from the deception matrix).

The contract for these files (and the models below that generate schema/*.schema.json) is
docs/DATA_CONTRACT.md.
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
from societysim.analysis.narrate import Voices, load_voices  # noqa: E402
from societysim.roles import Role, SeatId, Winner  # noqa: E402

SCHEMA = 1


# --- output shapes (the data contract, docs/DATA_CONTRACT.md) --------------------------------
# These models only describe what this module writes; scripts/gen-schema.sh turns them into
# schema/{index,leaderboard,metrics}.schema.json. The writers below build plain dicts.

Seat = SeatId
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
    invalid_output_rate: Num  # rejected replies per model call attempt; same as the next
    invalid_attempt_rate: Num
    fallback_rate: Num  # turns that ended in a fallback move / turns
    retry_success_rate: Num  # retries whose second attempt was accepted / retries
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
    invalid_output_rate: Num  # rejected replies per model call attempt; excludes provider errors
    invalid_attempt_rate: Num  # same number as invalid_output_rate
    fallback_rate: Num  # turns that ended in a fallback move / turns; excludes provider errors
    retry_success_rate: Num  # retries whose second attempt was accepted / retries
    provider_errors: int  # calls that failed at the provider (5xx, 429, timeout) after retries
    provider_error_rate: Num
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


CI = tuple[float, float] | None


class DeceptionBaseline(_Out):
    heard: int
    voters: int
    followed: int
    vote_follow_rate: Num
    vote_follow_ci: CI


class DeceptionCell(DeceptionBaseline):
    comparisons: int
    moved: int
    belief_shift_rate: Num
    belief_shift_ci: CI
    baseline_rate: Num
    baseline_voters: int


ClaimKind = Literal["role_claim", "seer_wolf", "seer_clear", "accusation"]
DeceptionPairs = dict[str, dict[str, DeceptionCell]]  # liar model -> listener model


class DeceptionBySource(_Out):
    self_tag: DeceptionPairs
    jev: DeceptionPairs


class DeceptionMatrix(_Out):
    exploratory: bool
    pairs: DeceptionBySource
    baseline: dict[ClaimKind, dict[str, DeceptionBaseline]]  # kind -> listener model


class HumanSeats(_Out):
    seats: int
    wins: int
    draws: int
    losses: int
    win_rate: Num
    win_ci: CI


class HumanFlow(DeceptionBaseline):
    """deception_matrix counts summed by direction, plus `caught`: next vote for the liar."""

    caught: int
    comparisons: int
    moved: int
    belief_shift_rate: Num
    belief_shift_ci: CI
    catch_rate: Num
    catch_ci: CI


class HumanFlows(_Out):
    human_to_model: HumanFlow
    model_to_human: HumanFlow
    model_to_model: HumanFlow


class HumanFlowsBySource(_Out):
    self_tag: HumanFlows
    jev: HumanFlows


class Humans(_Out):
    """Humans vs models (#305): visitor seats against the AI seats. Counts only, no handles."""

    exploratory: bool
    games: int
    seats: int
    by_role: dict[Group, HumanSeats]
    models_by_role: dict[Group, HumanSeats]
    lies: HumanFlowsBySource


class PairRow(DeceptionCell):
    """One deception_matrix cell seen from one side: `model` is the other model."""

    model: str


class PairsBySource(_Out):
    self_tag: list[PairRow]
    jev: list[PairRow]


class ProfileDetection(_Out):
    n: int
    auc: Num
    brier: Num


class Voice(_Out):
    voice_id: str
    fallback: bool  # true when configs/voices.yaml has no entry and default_model_voice is used


class ModelProfile(_Out):
    """One model's profile page: leaderboard numbers, detection, who it fools, speed, voice."""

    model: str
    games: int | None
    seats: int
    win_rate: Num
    win_ci: tuple[float, float] | None
    wolf_win_rate: Num
    village_win_rate: Num
    openskill: Rating | None
    statements: int
    lie_rate: LieRate
    detection: ProfileDetection
    median_latency_ms: Num
    voice: Voice
    fools: PairsBySource
    fooled_by: PairsBySource


class Metrics(_Out):
    """report/metrics.json: analysis.metrics.compute_metrics plus summary, speed and profiles."""

    games: int
    unfinished_games: int
    label_sources: list[Literal["self_tag", "jev"]]
    exploratory: list[Literal["lie_success", "persuasion"]]
    models: dict[str, dict[Group, GroupStats]]
    by_group: dict[Group, GroupStats]
    openskill: OpenSkill
    deception_matrix: DeceptionMatrix
    humans: Humans
    summary: Summary
    speed: dict[str, Speed]
    profiles: dict[str, ModelProfile]


def _load(batch_dir: Path) -> tuple[list[tuple[Path, GameData]], int]:
    return load_finished(batch_dir)


def _pct(x: float | None) -> str:
    return "n/a" if x is None else f"{x * 100:.0f}%"


def _pct1(x: float | None) -> str:
    """One decimal, for the reliability rates: 2 fallbacks in 330 turns is 0.6%, not 1%."""
    return "n/a" if x is None else f"{x * 100:.1f}%"


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
                "invalid_attempt_rate": a.get("invalid_attempt_rate"),
                "fallback_rate": a.get("fallback_rate"),
                "retry_success_rate": a.get("retry_success_rate"),
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


def _pair_rows(cells: dict[str, dict]) -> list[dict]:
    """Highest vote-follow rate first (no rate last), then more voters, then model name."""
    rows = [{**c, "model": m} for m, c in cells.items()]
    return sorted(
        rows,
        key=lambda r: (
            r["vote_follow_rate"] is None,
            -(r["vote_follow_rate"] or 0),
            -r["voters"],
            r["model"],
        ),
    )


def build_profiles(
    metrics: dict, board: dict, speed: dict[str, dict], voices: Voices | None = None
) -> dict[str, dict]:
    """One profile per model in `board`: what the viewer's model page shows."""
    voices = voices or load_voices()
    pairs = metrics["deception_matrix"]["pairs"]
    out = {}
    for r in board["models"]:
        m = r["model"]
        groups = metrics["models"][m]
        det = groups.get("village", {}).get("detection", {})
        out[m] = {
            "model": m,
            **{
                k: r[k]
                for k in (
                    "games",
                    "seats",
                    "win_rate",
                    "win_ci",
                    "wolf_win_rate",
                    "village_win_rate",
                    "openskill",
                )
            },
            "statements": groups["all"]["statements"],
            "lie_rate": r["lie_rate"],
            "detection": {
                "n": det.get("n", 0),
                "auc": det.get("auc"),
                "brier": det.get("brier"),
            },
            "median_latency_ms": speed.get(m, {}).get("median_latency_ms"),
            "voice": {"voice_id": voices.model(m), "fallback": m not in voices.models},
            "fools": {src: _pair_rows(pairs[src].get(m, {})) for src in LABEL_SOURCES},
            "fooled_by": {
                src: _pair_rows({liar: row[m] for liar, row in pairs[src].items() if m in row})
                for src in LABEL_SOURCES
            },
        }
    return out


def _provider_errors(group: dict | None) -> str:
    """ "3 (2%)" for a model's provider errors; the invalid rate next to it excludes them."""
    if not group or "provider_errors" not in group:
        return "n/a"
    n = group["provider_errors"]
    return f"{n} ({_pct(group['provider_error_rate'])})" if n else "0"


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


MIN_PAIR_VOTES = 5  # fewer listener votes than this is too thin to rank a pair on


def _has_matrix(dm: dict, src: str, present: set[str]) -> bool:
    return src in present and bool(dm["pairs"][src])


def _follow(c: dict) -> str:
    return f"{_pct(c['vote_follow_rate'])} (n={c['voters']})"


def _vs_base(c: dict) -> str:
    return f"{_pct(c['baseline_rate'])} (n={c['baseline_voters']})"


CLAIM_KIND_TEXT = {
    "role_claim": "True role claim: did not vote the claimant",
    "seer_wolf": "True seer report of a wolf: voted that wolf",
    "seer_clear": "True seer clear: voted neither the cleared player nor the seer",
    "accusation": "A non-wolf accused only real wolves: voted one of them",
}


def _deception(dm: dict, present: set[str]) -> list[str]:
    lines = [
        "## Who fools whom (exploratory)",
        "",
        "Each lie counts once for every living non-wolf seat that heard it, other than the liar "
        "and any player the lie names. Rows are the liar's model, columns the listener's. A cell "
        "is how often the listener's next vote that day went the way the lie implied, with the "
        "number of votes, then the listener's no-lie baseline for the same mix of claims. The "
        "baseline is how often that listener's next vote went the same way after a truthful "
        "claim of the same kind (table below), so a high cell is not just a listener that "
        "believes every claim. Belief shift is whether the listener's next suspicion update "
        "moved the way the lie wanted: down on the liar for a role claim, toward the claimed side "
        "of the reported player for a seer report, up on the accused wolf for bussing. "
        "Correlation, not cause.",
        "",
    ]
    for src in LABEL_SOURCES:
        lines += [f"### {src}", ""]
        if src not in present:
            lines += [f"No {src} labels.", ""]
            continue
        pairs = dm["pairs"][src]
        if not pairs:
            lines += ["No lies were heard by another model.", ""]
            continue
        cols = sorted({m for row in pairs.values() for m in row})
        rows = [
            [liar]
            + [
                f"{_follow(pairs[liar][c])}, base {_pct(pairs[liar][c]['baseline_rate'])}"
                if c in pairs[liar]
                else ""
                for c in cols
            ]
            for liar in pairs
        ]
        lines += _table(["Liar \\ listener", *cols], rows)
        top = sorted(
            (
                (liar, listener, c)
                for liar, row in pairs.items()
                for listener, c in row.items()
                if c["voters"] >= MIN_PAIR_VOTES
            ),
            key=lambda t: (-t[2]["vote_follow_rate"], -t[2]["voters"], t[0], t[1]),
        )[:5]
        lines += [f"Top pairs by vote-follow rate (at least {MIN_PAIR_VOTES} votes):", ""]
        if top:
            lines += _table(
                [
                    "Liar",
                    "Listener",
                    "Voted as lie implied (95% CI)",
                    "Votes",
                    "Belief shift",
                    "Listener baseline",
                ],
                [
                    [
                        liar,
                        listener,
                        f"{_pct(c['vote_follow_rate'])} ({_pct(c['vote_follow_ci'][0])} to "
                        f"{_pct(c['vote_follow_ci'][1])})",
                        str(c["voters"]),
                        f"{_pct(c['belief_shift_rate'])} (n={c['comparisons']})",
                        _vs_base(c),
                    ]
                    for liar, listener, c in top
                ],
            )
        else:
            lines += ["None qualifies yet.", ""]
        lines += [f"![Who fools whom ({src})](deception_{src}.png)", ""]
    base = dm["baseline"]
    lines += [
        "### No-lie baseline",
        "",
        "Truthful claims from the log, the same for both label sources: how often each "
        "listener's next vote went the way the claim implied.",
        "",
    ]
    if not base:
        return lines + ["No truthful claims were heard.", ""]
    cols = sorted({m for row in base.values() for m in row})
    return lines + _table(
        ["Claim", *cols],
        [
            [CLAIM_KIND_TEXT[kind]] + [_follow(row[c]) if c in row else "" for c in cols]
            for kind, row in base.items()
        ],
    )


FLOW_TEXT = {
    "human_to_model": "Human lies, model listeners",
    "model_to_human": "Model lies, human listeners",
    "model_to_model": "Model lies, model listeners (whole batch)",
}


def _ci(ci) -> str:
    return "n/a" if not ci else f"{_pct(ci[0])} to {_pct(ci[1])}"


def _humans(h: dict, present: set[str]) -> list[str]:
    lines = ["## Humans vs models (exploratory)", ""]
    if not h["games"]:
        return lines + ["No visitor games yet.", ""]
    lines += [
        f"{_n(h['games'], 'visitor game')}, {_n(h['seats'], 'human seat')}. Human seats are "
        "never ranked. Model seats here are the AI seats in the same games.",
        "",
    ]
    by_role, models = h["by_role"], h["models_by_role"]
    lines += _table(
        ["Role", "Human seats", "Human win rate", "95% CI", "Model seats", "Model win rate"],
        [
            [
                grp,
                str(by_role[grp]["seats"]) if grp in by_role else "0",
                _pct(by_role[grp]["win_rate"]) if grp in by_role else "n/a",
                _ci(by_role[grp]["win_ci"]) if grp in by_role else "n/a",
                str(models[grp]["seats"]) if grp in models else "0",
                _pct(models[grp]["win_rate"]) if grp in models else "n/a",
            ]
            for grp in sorted({*by_role, *models})
        ],
    )
    lines += [
        "Lies by direction, the deception matrix's counts summed. 'Voted as lie implied' is the "
        "matrix's vote rule; 'Caught' is a next vote for the liar. Correlation, not cause.",
        "",
    ]
    for src in LABEL_SOURCES:
        lines += [f"### {src}", ""]
        if src not in present:
            lines += [f"No {src} labels.", ""]
            continue
        lines += _table(
            ["Direction", "Heard", "Voted as lie implied", "Caught", "Belief shift"],
            [
                [
                    FLOW_TEXT[f],
                    str(c["heard"]),
                    _follow(c),
                    f"{_pct(c['catch_rate'])} (n={c['voters']})",
                    f"{_pct(c['belief_shift_rate'])} (n={c['comparisons']})",
                ]
                for f, c in h["lies"][src].items()
            ],
        )
    return lines


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
    if "deception_matrix" in metrics:
        lines += _deception(metrics["deception_matrix"], present)
    if "humans" in metrics:
        lines += _humans(metrics["humans"], present)
    lines += [
        "## Cost and reliability",
        "",
    ]
    lines += _table(
        [
            "Model",
            "Invalid attempts",
            "Fallback rate",
            "Retry success",
            "Provider errors",
            "USD per game",
        ],
        [
            [
                r["model"],
                _pct1(r["invalid_attempt_rate"]),
                _pct1(r["fallback_rate"]),
                _pct1(r["retry_success_rate"]),
                _provider_errors(metrics["models"].get(r["model"], {}).get("all")),
                "n/a" if r["cost_usd_per_game"] is None else f"{r['cost_usd_per_game']:.4f}",
            ]
            for r in board["models"]
        ],
    )
    lines += [
        "Invalid attempts: rejected replies per model call. A retry that works still counts, so "
        "this overstates the damage. Fallback rate: turns that ended in a fallback move. Retry "
        "success: retries whose second attempt was accepted. Provider errors are in none of "
        "the three.",
        "",
    ]
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
        "`detection_brier`, `invalid_output_rate` (rejected replies per model call attempt, so a "
        "retry that works still counts; `invalid_attempt_rate` is the same number), "
        "`fallback_rate` (turns that ended in a fallback move), `retry_success_rate` (retries "
        "whose second attempt was accepted), `cost_usd_per_game` and "
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


def write_deception_charts(dm: dict, present: set[str], out: Path) -> list[str]:
    """One heatmap per label source with lies heard: vote-follow rate, liar x listener."""
    files = []
    for src in LABEL_SOURCES:
        if not _has_matrix(dm, src, present):
            continue
        pairs = dm["pairs"][src]
        liars = list(pairs)
        cols = sorted({m for row in pairs.values() for m in row})
        grid = [
            [pairs[r][c]["vote_follow_rate"] if c in pairs[r] else None for c in cols]
            for r in liars
        ]
        vals = [[math.nan if v is None else v for v in row] for row in grid]
        fig, ax = plt.subplots(figsize=(2 + 1.1 * len(cols), 1.5 + 0.6 * len(liars)))
        im = ax.imshow(vals, vmin=0, vmax=1, cmap="viridis", aspect="auto")
        for i, r in enumerate(liars):
            for j, c in enumerate(cols):
                if c in pairs[r]:
                    cell = pairs[r][c]
                    ax.text(
                        j,
                        i,
                        f"{_pct(cell['vote_follow_rate'])}\nn={cell['voters']}",
                        ha="center",
                        va="center",
                        fontsize=8,
                        color="black"
                        if (rate := cell["vote_follow_rate"]) is None or rate > 0.6
                        else "white",
                    )
        ax.set_xticks(range(len(cols)), cols, rotation=30, ha="right")
        ax.set_yticks(range(len(liars)), liars)
        ax.set_xlabel("listener")
        ax.set_ylabel("liar")
        ax.set_title(f"Who fools whom ({src}): voted as the lie implied")
        fig.colorbar(im, ax=ax)
        fig.tight_layout()
        name = f"deception_{src}.png"
        fig.savefig(out / name, dpi=120)
        plt.close(fig)
        files.append(name)
    return files


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
    write_deception_charts(metrics["deception_matrix"], present, out)

    def dump(name: str, obj) -> None:
        (out / name).write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")

    summary, speed = build_summary(metrics, data), build_speed(metrics, data)
    profiles = build_profiles(metrics, board, speed)
    dump("metrics.json", {**metrics, "summary": summary, "speed": speed, "profiles": profiles})
    dump("leaderboard.json", board)
    dump("index.json", build_index(games))
    (out / "report.md").write_text(
        render_markdown(metrics, board, present, summary, speed), encoding="utf-8"
    )
    return out
