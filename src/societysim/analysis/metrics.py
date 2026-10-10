"""Per-model and per-role metrics from game logs and their labels.

Every metric is built from one row per (game, seat) holding raw counts, then rows are summed
by (model, group). Groups: "all", "wolf", "village" (every non-wolf role), and each role.

Lie metrics are computed separately for each claim source (`self_tag` and `jev`) and reported
side by side under `lies[source]`, never summed: Jev and self-tag labels can describe the same
lie. Engine labels (votes) and mis_elimination are never lies here. Lie success and persuasion
are exploratory: they read correlation, not cause.

OpenSkill (PlackettLuce), equal-weight sides with a side-advantage dummy. Each game is one
match between two teams:
- each side plays as one composite player: the mean (not sum) of its seats' ratings, with
  sigma^2 the mean of their sigma^2. So 2 wolves vs 5 villagers, and 2 or 5 distinct village
  models, are the same match;
- the wolf team also holds a "wolf side" dummy (prior mu 0) that learns how much easier or
  harder the wolf side is, so side difficulty does not leak into model ratings;
- the composite's update goes back to each distinct model on that side: mu moves by the
  composite's mu change times sigma_i^2 / sigma_composite^2, and sigma scales by the
  composite's sigma ratio. A model on both sides (self-play) takes the mean of its updates.
Pools: "overall" has one rating per model; "wolf" / "village" have one per (model, side) and
share one pool. Each pool has its own wolf-side dummy, reported under "wolf_side".
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
SOURCES = ("self_tag", "jev")
LS_KEYS = (
    "comparisons",
    "moved",
    "voters",
    "followed",
    "base_voters",
    "base_followed",
    "base_abstained",
)
WOLF_SIDE = "wolf_side"


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
    lies: Counter = field(default_factory=Counter)  # (source, label type)
    lie_success: Counter = field(default_factory=Counter)  # (source, LS_KEYS item)
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


def _dead_before(events: list[Event], seq: int) -> set[str]:
    deaths = (EventType.DEATH_REVEAL, EventType.ELIMINATION)
    return {e.target for e in events[:seq] if e.type in deaths and e.target}


def lie_success(game: GameData, label: Label, seen_base: set | None = None) -> dict:
    """Did other living non-wolves' suspicion move the liar's way between the lie and their vote?

    Subject: the liar (false_role_claim / false_report: success = suspicion dropped) or the
    accused wolf (bussing: success = suspicion rose). Observers are the non-wolf voters of
    that day other than the liar, compared on their last suspicion before the lie and the one
    they gave with their vote. Also counts those who voted the way the lie implied: not the
    liar for a role claim; the reported player for a wolf report (anyone but the reported
    player and the liar for a not_wolf report); the accused wolf for bussing.
    Abstains never follow the lie.

    Base rate: the same vote rule, same voters (non-wolves other than the liar), on the game's
    other vote days that have no label of this type from this actor and source and where
    every player the rule names is still alive. A base vote is counted once per (liar, lie type,
    source, day, voter): pass the same `seen_base` set for every label of a game and later labels
    of the same liar, type and source skip the votes an earlier one already counted. Abstains are
    in the base and never follow; `base_abstained` says how many.
    """
    if label.label not in SUCCESS_TYPES:
        raise ValueError(f"no lie success for {label.label}")
    events, roles, s, liar = game.events, game.roles, label.source_seq, label.actor
    src = events[s]
    wolves = {p for p, r in roles.items() if r is Role.WOLF}
    named = {liar}
    if label.label is LabelType.BUSSING:
        fellows = wolves - {liar} - _dead_before(events, s)
        subjects = {src.target} if src.target in wolves else fellows
        named, rose = subjects, True
    else:
        subjects, rose = {liar}, False
        if label.label is LabelType.FALSE_REPORT and src.type is EventType.SEER_CLAIM:
            named = {liar, src.target}

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

    out = dict.fromkeys(LS_KEYS, 0)
    votes = [v for v in events if v.type is EventType.VOTE]
    for v in votes:
        if v.day != src.day or v.seq < s or v.actor in wolves or v.actor == liar:
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

    lie_days = {
        events[lb.source_seq].day
        for lb in game.labels
        if (lb.label, lb.actor, lb.source) == (label.label, liar, label.source)
    }
    first_vote: dict[int, int] = {}
    for v in votes:
        first_vote.setdefault(v.day, v.seq)
    for day, seq in first_vote.items():
        if day in lie_days or named & _dead_before(events, seq):
            continue
        for v in votes:
            if v.day == day and v.actor not in wolves and v.actor != liar:
                key = (liar, label.label, label.source, day, v.actor)
                if seen_base is not None:
                    if key in seen_base:
                        continue
                    seen_base.add(key)
                out["base_voters"] += 1
                out["base_followed"] += follows(v.target)
                out["base_abstained"] += v.target is None
    return out


def game_rows(game: GameData) -> dict[str, Row]:
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

    seen_base: set = set()
    for lb in game.labels:
        if lb.source not in SOURCES or lb.label not in LIE_TYPES:
            continue
        row = rows[lb.actor]
        row.lies[(lb.source, lb.label.value)] += 1
        if lb.label in SUCCESS_TYPES:
            for k, n in lie_success(game, lb, seen_base).items():
                row.lie_success[(lb.source, k)] += n
    return rows


def _groups(role: Role) -> tuple[str, ...]:
    return ("all", "wolf") if role is Role.WOLF else ("all", "village", role.value)


def _ratio(k: float, n: float) -> float | None:
    return k / n if n else None


def _lies(row: Row, source: str) -> dict:
    by_type = {t: n for (src, t), n in row.lies.items() if src == source}
    ls = {k: row.lie_success[(source, k)] for k in LS_KEYS}
    return {
        "lies": sum(by_type.values()),
        "by_type": by_type,
        "lie_rate": _ratio(sum(by_type.values()), row.statements),
        "lie_success": {
            "exploratory": True,
            **ls,
            "share_moved": _ratio(ls["moved"], ls["comparisons"]),
            "share_followed": _ratio(ls["followed"], ls["voters"]),
            "base_rate": _ratio(ls["base_followed"], ls["base_voters"]),
        },
    }


def summarize(row: Row) -> dict:
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
        "lies": {src: _lies(row, src) for src in SOURCES},
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


def _rate_match(pl: PlackettLuce, pool: dict, teams: dict[str, list], winner: str) -> None:
    """One game in one pool; `teams` maps side -> one pool key per seat. See module docstring."""
    for keys in teams.values():
        for k in keys:
            pool.setdefault(k, pl.rating())
    pool.setdefault(WOLF_SIDE, pl.rating(mu=0.0))
    comp = {}
    for side, keys in teams.items():
        rs = [pool[k] for k in keys]
        comp[side] = pl.rating(
            mu=sum(r.mu for r in rs) / len(rs),
            sigma=math.sqrt(sum(r.sigma**2 for r in rs) / len(rs)),
        )
    ranks = {"wolves": [1, 2], "villagers": [2, 1], "draw": [1, 1]}[winner]
    (new_wolf, new_side), (new_village,) = pl.rate(
        [[comp["wolf"], pool[WOLF_SIDE]], [comp["village"]]], ranks=ranks
    )
    pool[WOLF_SIDE] = new_side
    updates: dict = defaultdict(list)
    for side, new in (("wolf", new_wolf), ("village", new_village)):
        old = comp[side]
        for k in dict.fromkeys(teams[side]):
            r = pool[k]
            mu = r.mu + (new.mu - old.mu) * r.sigma**2 / old.sigma**2
            updates[k].append((mu, r.sigma * new.sigma / old.sigma))
    for k, us in updates.items():
        mu = sum(u[0] for u in us) / len(us)
        sigma = sum(u[1] for u in us) / len(us)
        pool[k] = pl.rating(mu=mu, sigma=sigma)


def openskill_ratings(games: list[GameData]) -> dict[str, dict[str, dict]]:
    """Equal-weight PlackettLuce ratings, games in game_id order; a draw is a tie.

    Games without a winner are skipped. See the module docstring for the method.
    """
    pl = PlackettLuce()
    overall: dict = {}
    sided: dict = {}
    for g in sorted(games, key=lambda g: g.game_id):
        if g.winner is None:
            continue
        seats: dict[str, list[str]] = {side: [] for side in SIDES}
        for seat, role in g.roles.items():
            seats["wolf" if role is Role.WOLF else "village"].append(g.models[seat])
        _rate_match(pl, overall, seats, g.winner)
        by_side = {side: [(m, side) for m in ms] for side, ms in seats.items()}
        _rate_match(pl, sided, by_side, g.winner)

    def out(r) -> dict:
        return {"mu": r.mu, "sigma": r.sigma, "ordinal": r.ordinal()}

    side_ratings = {
        side: {k[0]: out(r) for k, r in sided.items() if k != WOLF_SIDE and k[1] == side}
        for side in SIDES
    }
    return {
        "overall": {m: out(r) for m, r in sorted(overall.items()) if m != WOLF_SIDE},
        **{side: dict(sorted(rs.items())) for side, rs in side_ratings.items()},
        WOLF_SIDE: {
            name: out(pool[WOLF_SIDE])
            for name, pool in (("overall", overall), ("sided", sided))
            if pool
        },
    }


def compute_metrics(games: list[GameData]) -> dict:
    """Every metric, per model and group, plus the same groups across all models."""
    per_model: dict[str, dict[str, Row]] = defaultdict(lambda: defaultdict(Row))
    by_group: dict[str, Row] = defaultdict(Row)
    for g in games:
        roles, models = g.roles, g.models
        for seat, row in game_rows(g).items():
            for grp in _groups(roles[seat]):
                per_model[models[seat]][grp].add(row)
                by_group[grp].add(row)
    return {
        "games": len(games),
        "label_sources": list(SOURCES),
        "exploratory": list(EXPLORATORY),
        "models": {
            m: {grp: summarize(r) for grp, r in sorted(groups.items())}
            for m, groups in sorted(per_model.items())
        },
        "by_group": {grp: summarize(r) for grp, r in sorted(by_group.items())},
        "openskill": openskill_ratings(games),
    }
