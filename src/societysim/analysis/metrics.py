"""Per-model and per-role metrics from game logs and their labels.

Every metric is built from one row per (game, seat) holding raw counts, then rows are summed
by (model, group). Groups: "all", "wolf", "village" (every non-wolf role), and each role.

Lie metrics read labels from one claim source (`self_tag` by default, or `jev`), because Jev
and self-tag labels can describe the same lie. Engine labels (votes) are never lies here.
Lie success and persuasion are exploratory: they read correlation, not cause.
"""

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from openskill.models import PlackettLuce

from societysim.analysis.labeler import read_log
from societysim.logging.events import Event, EventType, Label, LabelType
from societysim.roles import Role

LIE_TYPES = (
    LabelType.FALSE_ROLE_CLAIM,
    LabelType.FALSE_REPORT,
    LabelType.BUSSING,
    LabelType.HIDDEN_KNOWLEDGE,
)
# hidden_knowledge is an omission: no subject whose suspicion should move, so no success.
SUCCESS_TYPES = (LabelType.FALSE_ROLE_CLAIM, LabelType.FALSE_REPORT, LabelType.BUSSING)
EXPLORATORY = ("lie_success", "persuasion")
SIDES = ("wolf", "village")


@dataclass
class GameData:
    events: list[Event]
    labels: list[Label]

    @property
    def game_id(self) -> str:
        return self.events[0].game_id

    @property
    def roles(self) -> dict[str, Role]:
        return {s: seat.role for s, seat in (self.events[0].meta.seats or {}).items()}

    @property
    def models(self) -> dict[str, str]:
        return {s: seat.model for s, seat in (self.events[0].meta.seats or {}).items()}

    @property
    def winner(self) -> str | None:
        ends = [e for e in self.events if e.type is EventType.GAME_END]
        return ends[-1].meta.winner if ends else None


def load_game(path: Path) -> GameData:
    """A game log plus its .labels.jsonl next to it (no labels if that file is missing)."""
    events = read_log(path)
    lpath = path.with_name(f"{path.stem}.labels.jsonl")
    labels = (
        [Label.model_validate_json(x) for x in lpath.read_text().splitlines() if x]
        if lpath.exists()
        else []
    )
    return GameData(events, labels)


def load_batch(batch_dir: Path) -> list[GameData]:
    logs = sorted(p for p in batch_dir.glob("*.jsonl") if not p.name.endswith(".labels.jsonl"))
    return [load_game(p) for p in logs if p.stat().st_size]


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """Wilson score interval for k successes in n trials."""
    if n == 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (centre - half, centre + half)


def brier(pairs: list[tuple[float, int]]) -> float | None:
    return sum((p - y) ** 2 for p, y in pairs) / len(pairs) if pairs else None


def auc(pairs: list[tuple[float, int]]) -> float | None:
    """P(a random wolf gets a higher suspicion than a random non-wolf); ties count half."""
    npos = sum(y for _, y in pairs)
    nneg = len(pairs) - npos
    if not npos or not nneg:
        return None
    ordered = sorted(pairs)
    rank_sum, i = 0.0, 0
    while i < len(ordered):  # average ranks over ties
        j = i
        while j < len(ordered) and ordered[j][0] == ordered[i][0]:
            j += 1
        avg = (i + 1 + j) / 2
        rank_sum += avg * sum(y for _, y in ordered[i:j])
        i = j
    return (rank_sum - npos * (npos + 1) / 2) / (npos * nneg)


# --- per (game, seat) rows -------------------------------------------------------------------


@dataclass
class Row:
    games: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0
    statements: int = 0
    lies: Counter = field(default_factory=Counter)
    ls_comparisons: int = 0  # (observer, subject) suspicion pairs around a lie
    ls_moved: int = 0  # ... that moved the way the liar wanted
    ls_voters: int = 0
    ls_followed: int = 0  # voters who voted the way the lie implied
    detection: list[tuple[int, float, int]] = field(default_factory=list)  # (day, p, is_wolf)
    accusations: int = 0
    pers_voters: int = 0
    pers_hits: int = 0
    survival_days: int = 0
    night_targeted: int = 0
    voted_out: int = 0
    invalid: int = 0
    attempts: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    def add(self, other: "Row") -> None:
        for name in self.__dataclass_fields__:
            mine = getattr(self, name)
            setattr(self, name, mine + getattr(other, name))


def _last_suspicion(events: list[Event], actor: str, before: int, player: str) -> float | None:
    for e in reversed(events[:before]):
        if e.actor == actor and e.suspicion is not None:
            return next((s.p for s in e.suspicion if s.player == player), None)
    return None


def lie_success(game: GameData, label: Label) -> dict:
    """Did other living non-wolves' suspicion move the liar's way between the lie and their vote?

    Subject: the liar (false_role_claim / false_report: success = suspicion dropped) or the
    accused wolf (bussing: success = suspicion rose). Observers are the non-wolf voters of
    that day other than the liar, compared on their last suspicion before the lie and the one
    they gave with their vote. Also counts those who voted the way the lie implied: not the
    liar for a role claim; the reported player for a wolf report (anyone but the reported
    player and the liar for a not_wolf report); the accused wolf for bussing.
    Abstains never follow the lie.
    """
    if label.label not in SUCCESS_TYPES:
        raise ValueError(f"no lie success for {label.label}")
    events, roles, s, liar = game.events, game.roles, label.source_seq, label.actor
    src = events[s]
    wolves = {p for p, r in roles.items() if r is Role.WOLF}
    dead = {
        e.target
        for e in events[:s]
        if e.type in (EventType.DEATH_REVEAL, EventType.ELIMINATION) and e.target
    }
    if label.label is LabelType.BUSSING:
        subjects = {src.target} if src.target in wolves else wolves - {liar} - dead
        rose = True
    else:
        subjects, rose = {liar}, False

    def follows(vote: str | None) -> bool:
        if vote is None:
            return False
        if label.label is LabelType.BUSSING:
            return vote in subjects
        if label.label is LabelType.FALSE_REPORT and src.type is EventType.SEER_CLAIM:
            if src.meta.result == "wolf":
                return vote == src.target
            return vote not in (src.target, liar)
        return vote != liar

    out = {"comparisons": 0, "moved": 0, "voters": 0, "followed": 0}
    for v in events:
        if v.type is not EventType.VOTE or v.day != src.day or v.seq < s:
            continue
        if v.actor in wolves or v.actor == liar:
            continue
        out["voters"] += 1
        out["followed"] += follows(v.target)
        after = {x.player: x.p for x in v.suspicion or []}
        for subj in subjects:
            before = _last_suspicion(events, v.actor, s, subj)
            if before is None or subj not in after:
                continue
            out["comparisons"] += 1
            out["moved"] += after[subj] > before if rose else after[subj] < before
    return out


def game_rows(game: GameData, label_source: str = "self_tag") -> dict[str, Row]:
    """One Row per seat for one game."""
    events, roles = game.events, game.roles
    rows = {seat: Row(games=1) for seat in roles}
    final_day = events[-1].day
    winner = game.winner

    for seat, row in rows.items():
        team = "wolves" if roles[seat] is Role.WOLF else "villagers"
        row.wins, row.draws = int(winner == team), int(winner == "draw")
        row.losses = int(winner is not None and not row.wins and not row.draws)
        row.survival_days = final_day

    accused: dict[tuple[str, int], set[str]] = defaultdict(set)
    last_susp: dict[tuple[str, int], Event] = {}
    for e in events:
        row = rows.get(e.actor) if e.actor else None
        if e.type in (EventType.DEATH_REVEAL, EventType.ELIMINATION) and e.target:
            rows[e.target].survival_days = e.day
        if e.type is EventType.ELIMINATION and e.target:
            rows[e.target].voted_out += 1
        if e.type is EventType.NIGHT_KILL and e.target:
            rows[e.target].night_targeted += 1
        if row is None:
            continue
        if e.type is EventType.STATEMENT:
            row.statements += 1
        if e.type is EventType.INVALID_OUTPUT:
            row.invalid += 1
        if e.type is EventType.ACCUSATION and e.target:
            accused[(e.actor, e.meta.statement_seq or e.seq)].add(e.target)
        if e.suspicion is not None and roles[e.actor] is not Role.WOLF:
            last_susp[(e.actor, e.day)] = e
        if u := e.meta.usage:
            row.attempts += u.attempts
            row.input_tokens += u.input_tokens
            row.output_tokens += u.output_tokens
            row.cost_usd += u.cost_usd

    for (actor, _day), e in last_susp.items():
        rows[actor].detection += [
            (e.day, s.p, int(roles[s.player] is Role.WOLF)) for s in e.suspicion or []
        ]

    votes = [e for e in events if e.type is EventType.VOTE]
    for (actor, stmt), targets in accused.items():
        day = events[stmt].day
        others = [v for v in votes if v.day == day and v.actor != actor]
        rows[actor].accusations += 1
        rows[actor].pers_voters += len(others)
        rows[actor].pers_hits += sum(v.target in targets for v in others)

    for lb in game.labels:
        if lb.source != label_source or lb.label not in LIE_TYPES:
            continue
        row = rows[lb.actor]
        row.lies[lb.label.value] += 1
        if lb.label in SUCCESS_TYPES:
            r = lie_success(game, lb)
            row.ls_comparisons += r["comparisons"]
            row.ls_moved += r["moved"]
            row.ls_voters += r["voters"]
            row.ls_followed += r["followed"]
    return rows


def _groups(role: Role) -> tuple[str, ...]:
    return ("all", "wolf") if role is Role.WOLF else ("all", "village", role.value)


def _ratio(k: float, n: float) -> float | None:
    return k / n if n else None


def summarize(row: Row) -> dict:
    lies = sum(row.lies.values())
    by_day: dict[int, list[tuple[float, int]]] = defaultdict(list)
    for d, p, y in row.detection:
        by_day[d].append((p, y))
    pairs = [(p, y) for _, p, y in row.detection]
    return {
        "games": row.games,
        "wins": row.wins,
        "draws": row.draws,
        "losses": row.losses,
        "win_rate": _ratio(row.wins, row.games),
        "win_ci": wilson(row.wins, row.games),
        "statements": row.statements,
        "lies": lies,
        "lies_by_type": dict(row.lies),
        "lie_rate": _ratio(lies, row.statements),
        "lie_success": {
            "exploratory": True,
            "comparisons": row.ls_comparisons,
            "moved": row.ls_moved,
            "share_moved": _ratio(row.ls_moved, row.ls_comparisons),
            "voters": row.ls_voters,
            "followed": row.ls_followed,
            "share_followed": _ratio(row.ls_followed, row.ls_voters),
        },
        "detection": {
            "n": len(pairs),
            "brier": brier(pairs),
            "auc": auc(pairs),
            "by_day": {
                d: {"n": len(ps), "brier": brier(ps), "auc": auc(ps)}
                for d, ps in sorted(by_day.items())
            },
        },
        "persuasion": {
            "exploratory": True,
            "accusations": row.accusations,
            "voters": row.pers_voters,
            "hits": row.pers_hits,
            "share": _ratio(row.pers_hits, row.pers_voters),
        },
        "survival_days": _ratio(row.survival_days, row.games),
        "night_targeted": _ratio(row.night_targeted, row.games),
        "voted_out": _ratio(row.voted_out, row.games),
        "invalid_output_rate": _ratio(row.invalid, row.attempts),
        "tokens_per_game": _ratio(row.input_tokens + row.output_tokens, row.games),
        "cost_usd_per_game": _ratio(row.cost_usd, row.games),
    }


def openskill_ratings(games: list[GameData]) -> dict[str, dict[str, dict]]:
    """PlackettLuce ratings, games in game_id order: wolves vs villagers, a draw is a tie.

    "overall" keeps one rating per model; "wolf" and "village" keep one per (model, side), and
    both sides of a game are rated in one shared pool. A model appears once per team however
    many seats it has; a model on both teams (self-play) takes the mean of its two updates.
    Games without a winner are skipped.
    """
    pl = PlackettLuce()
    overall: dict[str, object] = {}
    sided: dict[tuple[str, str], object] = {}
    for g in sorted(games, key=lambda g: g.game_id):
        if g.winner is None:
            continue
        teams = {side: [] for side in SIDES}
        for seat, role in g.roles.items():
            side = "wolf" if role is Role.WOLF else "village"
            if g.models[seat] not in teams[side]:
                teams[side].append(g.models[seat])
        ranks = {"wolves": [1, 2], "villagers": [2, 1], "draw": [1, 1]}[g.winner]

        def rate(pool: dict, key, teams=teams, ranks=ranks) -> list[list]:
            team_ratings = [
                [pool.setdefault(key(m, side), pl.rating(name=str(key(m, side)))) for m in ms]
                for side, ms in teams.items()
            ]
            return pl.rate(team_ratings, ranks=ranks)

        new = rate(sided, lambda m, side: (m, side))
        for side, ms, rs in zip(SIDES, teams.values(), new, strict=True):
            for m, r in zip(ms, rs, strict=True):
                sided[(m, side)] = r

        new = rate(overall, lambda m, side: m)
        updates: dict[str, list] = defaultdict(list)
        for ms, rs in zip(teams.values(), new, strict=True):
            for m, r in zip(ms, rs, strict=True):
                updates[m].append(r)
        for m, rs in updates.items():
            mu = sum(r.mu for r in rs) / len(rs)
            sigma = sum(r.sigma for r in rs) / len(rs)
            overall[m] = pl.rating(mu=mu, sigma=sigma, name=m)

    def out(r) -> dict:
        return {"mu": r.mu, "sigma": r.sigma, "ordinal": r.ordinal()}

    return {
        "overall": {m: out(r) for m, r in sorted(overall.items())},
        **{side: {m: out(r) for (m, s), r in sorted(sided.items()) if s == side} for side in SIDES},
    }


def compute_metrics(games: list[GameData], label_source: str = "self_tag") -> dict:
    """Every metric, per model and group, plus the same groups across all models."""
    per_model: dict[str, dict[str, Row]] = defaultdict(lambda: defaultdict(Row))
    by_group: dict[str, Row] = defaultdict(Row)
    for g in games:
        roles, models = g.roles, g.models
        for seat, row in game_rows(g, label_source).items():
            for grp in _groups(roles[seat]):
                per_model[models[seat]][grp].add(row)
                by_group[grp].add(row)
    return {
        "games": len(games),
        "label_source": label_source,
        "exploratory": list(EXPLORATORY),
        "models": {
            m: {grp: summarize(r) for grp, r in sorted(groups.items())}
            for m, groups in sorted(per_model.items())
        },
        "by_group": {grp: summarize(r) for grp, r in sorted(by_group.items())},
        "openskill": openskill_ratings(games),
    }
