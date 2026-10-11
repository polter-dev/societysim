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
  composite's sigma ratio;
- a model on both sides of one game (self-play) keeps its overall rating unchanged: the game
  pits it against itself, so it says nothing about how it compares with other models. The
  match is still rated so the wolf-side dummy learns from it. A model whose every game was
  self-play has no overall rating (reported as n/a, never ranked by the prior). Self-play
  games still count toward every other metric, and the sided pools rate them as usual, since
  (model, "wolf") and (model, "village") are different players.
Pools: "overall" has one rating per model; "wolf" / "village" have one per (model, side) and
share one pool. Each pool has its own wolf-side dummy, reported under "wolf_side".
"""

import logging
import math
import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from openskill.models import PlackettLuce

from societysim.analysis.jev import cache_dir_for, cached_verdict
from societysim.analysis.labeler import _SAYS_VILLAGER, read_log
from societysim.logging.events import Event, EventType, Label, LabelType
from societysim.logging.textio import read_text_lenient
from societysim.models.client import is_provider_failure
from societysim.roles import Role

LIE_TYPES = (
    LabelType.FALSE_ROLE_CLAIM,
    LabelType.FALSE_REPORT,
    LabelType.BUSSING,
    LabelType.HIDDEN_KNOWLEDGE,
)
log = logging.getLogger(__name__)

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
HUMAN = "human"  # model name of a --human-seat; never ranked


@dataclass
class GameData:
    events: list[Event]
    labels: list[Label]
    # seqs of the statements Jev actually read: the ones with a usable entry in the batch's Jev
    # cache (#619). Empty means Jev read nothing, whatever the labels say.
    jev_read: frozenset[int] = frozenset()

    @property
    def game_id(self) -> str:
        return self.events[0].game_id

    @property
    def by_seq(self) -> dict[int, Event]:
        """Events keyed by seq. seq is an id, not a list position: look events up here."""
        return {e.seq: e for e in self.events}

    @property
    def roles(self) -> dict[str, Role]:
        return {s: seat.role for s, seat in (self.events[0].meta.seats or {}).items()}

    @property
    def models(self) -> dict[str, str]:
        return {s: seat.model for s, seat in (self.events[0].meta.seats or {}).items()}

    @property
    def labeled_sources(self) -> set[str]:
        """Sources with a label in this game. Says nothing about what Jev read: a statement Jev
        read and found clean has no label. That is `jev_read` (#619)."""
        return {lb.source for lb in self.labels}

    @property
    def winner(self) -> str | None:
        ends = [e for e in self.events if e.type is EventType.GAME_END]
        return ends[-1].meta.winner if ends else None


def load_game(path: Path) -> GameData:
    """A game log plus its .labels.jsonl next to it (no labels if that file is missing)."""
    events = read_log(path)
    lpath = path.with_name(f"{path.stem}.labels.jsonl")
    labels = (
        [Label.model_validate_json(x) for x in read_text_lenient(lpath).splitlines() if x]
        if lpath.exists()
        else []
    )
    return GameData(events, labels, jev_read_seqs(events, cache_dir_for(path.parent)))


def jev_read_seqs(events: list[Event], cache_dir: Path) -> frozenset[int]:
    """Seqs of the statements with a usable Jev cache entry. The labels file can't say: a
    statement Jev read and found clean has no jev label, and `label --no-jev` labels only the
    statements that are cached (#619)."""
    if not (cache_dir / events[0].game_id).is_dir():
        return frozenset()
    read = set()
    for e in events:
        if e.type is not EventType.STATEMENT:
            continue
        try:
            hit = cached_verdict(cache_dir, e.game_id, e.seq, e.actor or "", e.public_text or "")
        except ValueError:  # a cache file that isn't JSON: not a read
            continue
        if hit is not None:
            read.add(e.seq)
    return frozenset(read)


def load_finished(batch_dir: Path) -> tuple[list[tuple[Path, GameData]], int]:
    """Finished games in a batch dir, plus how many unfinished logs (no game_end) were skipped."""
    logs = sorted(p for p in batch_dir.glob("*.jsonl") if not p.name.endswith(".labels.jsonl"))
    games, skipped = [], 0
    for p in logs:
        if not p.stat().st_size:
            continue
        g = load_game(p)
        if not any(e.type is EventType.GAME_END for e in g.events):
            print(f"warning: {p.name} has no game_end, skipped")
            skipped += 1
            continue
        games.append((p, g))
    return games, skipped


def load_batch(batch_dir: Path) -> list[GameData]:
    return [g for _, g in load_finished(batch_dir)[0]]


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
    jev_statements: int = 0  # statements Jev read: the jev lie rate's denominator
    lies: Counter = field(default_factory=Counter)  # (source, label type)
    lie_success: Counter = field(default_factory=Counter)  # (source, LS_KEYS item)
    detection: list[tuple[int, float, int]] = field(default_factory=list)  # (day, p, is_wolf)
    accusations: int = 0
    pers_voters: int = 0
    pers_hits: int = 0
    survival_days: int = 0
    night_targeted: int = 0
    voted_out: int = 0
    invalid: int = 0  # invalid_output events caused by the model's reply
    provider_errors: int = (
        0  # invalid_output events with reason "provider": the model never answered
    )
    # model replies, counted from events: each rejected (non-provider) reply plus each accepted
    # one. Not usage.attempts, which also counts the client's transient HTTP retries (#443).
    attempts: int = 0
    # turns = one model call (up to two attempts) that ended in a reply or a fallback move; a
    # turn lost to a provider error is in none of these four
    turns: int = 0
    fallbacks: int = 0  # turns that ended in a fallback move after two rejected attempts
    retries: int = 0  # turns whose first attempt the model got wrong, so a second was made
    retry_wins: int = 0  # retries whose second attempt was accepted
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    def add(self, other: "Row") -> None:
        for name in self.__dataclass_fields__:
            mine = getattr(self, name)
            setattr(self, name, mine + getattr(other, name))


def _last_suspicion(events: list[Event], actor: str, before: int, player: str) -> float | None:
    for e in reversed(events):
        if e.seq < before and e.actor == actor and e.suspicion is not None:
            return next((s.p for s in e.suspicion if s.player == player), None)
    return None


def _dead_before(events: list[Event], seq: int) -> set[str]:
    deaths = (EventType.DEATH_REVEAL, EventType.ELIMINATION)
    return {e.target for e in events if e.seq < seq and e.type in deaths and e.target}


def _tagged(game: GameData, label: Label, kind: EventType) -> list[Event]:
    """Self-tag events of `kind` by the label's actor in the statement the label points at."""
    s, liar = label.source_seq, label.actor
    return [
        e for e in game.events if e.type is kind and e.actor == liar and e.meta.statement_seq == s
    ]


def _claim_target(game: GameData, label: Label) -> tuple[str | None, str | None]:
    """(reported or accused player, reported result) of a false_report or bussing label.

    A self-tag label points at its own seer_claim / accusation event. A Jev label points at the
    statement (the Label has no target field), so the target is read from the label's detail,
    which the labeler writes as "reported Name (pN) as <result>" / "accused fellow wolf Name
    (pN)", else from the actor's only self-tag of that kind in the statement. (None, None) when
    neither says.
    """
    src = game.by_seq[label.source_seq]
    if src.type in (EventType.SEER_CLAIM, EventType.ACCUSATION, EventType.VOTE):
        return src.target, src.meta.result
    detail = label.detail or ""
    if label.label is LabelType.FALSE_REPORT:
        if m := re.search(r"reported .*?\((p\d+)\) as (\w+)", detail):
            return m[1], m[2]
        tags = _tagged(game, label, EventType.SEER_CLAIM)
        return (tags[0].target, tags[0].meta.result) if len(tags) == 1 else (None, None)
    if m := re.search(r"fellow wolf .*?\((p\d+)\)", detail):
        return m[1], None
    wolves = {p for p, r in game.roles.items() if r is Role.WOLF}
    tags = [e for e in _tagged(game, label, EventType.ACCUSATION) if e.target in wolves]
    return (tags[0].target, None) if len(tags) == 1 else (None, None)


def _lie_rule(
    game: GameData, label: Label
) -> tuple[set[str], bool, Callable[[str | None], bool], set[str]] | None:
    """What a lie wants: (subjects, rose, follows, named), or None when the label can't be scored.

    Subjects are the players whose suspicion the lie should move, up if `rose` else down.
    `follows(vote)` says whether a vote went the way the lie implied (abstains never do).
    `named` are the players the vote rule names, used to pick base days.

    A false_report is a seer report: the reported player and result come from `_claim_target`,
    so a Jev label (which points at the statement) is scored like the equivalent self-tag.
    A false_report or bussing label whose target can't be found is None, not a role claim.
    """
    if label.label not in SUCCESS_TYPES:
        raise ValueError(f"no lie success for {label.label}")
    s, liar = label.source_seq, label.actor
    wolves = {p for p, r in game.roles.items() if r is Role.WOLF}
    named = {liar}
    target, result = _claim_target(game, label)
    if label.label is LabelType.BUSSING:
        fellows = wolves - {liar} - _dead_before(game.events, s)
        if target not in wolves and len(fellows) > 1:
            return None  # a Jev bussing label with no readable target: which fellow?
        subjects = {target} if target in wolves else fellows
        named, rose = subjects, True
    else:
        subjects, rose = {liar}, False
        if label.label is LabelType.FALSE_REPORT:
            if target is None and game.by_seq[s].type is not EventType.SEER_CLAIM:
                return None
            named = {liar, target}

    def follows(vote: str | None) -> bool:
        if vote is None:
            return False
        if label.label is LabelType.BUSSING:
            return vote in subjects
        if label.label is LabelType.FALSE_REPORT:
            if result == "wolf":
                return vote == target
            return vote not in (target, liar)
        return vote != liar

    return subjects, rose, follows, named


def lies_left_out(games: list[GameData]) -> dict[str, dict[str, int]]:
    """{source: {label type: count}} of false_report / bussing labels that lie_success and the
    deception matrix skip because the reported or accused player can't be found."""
    out: dict[str, Counter] = {src: Counter() for src in SOURCES}
    for g in games:
        for lb in g.labels:
            if lb.source in SOURCES and lb.label in SUCCESS_TYPES and _lie_rule(g, lb) is None:
                out[lb.source][lb.label.value] += 1
    return {src: dict(c) for src, c in out.items()}


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
    events, roles, s, liar = game.events, game.roles, label.source_seq, label.actor
    by_seq = game.by_seq
    src = by_seq[s]
    wolves = {p for p, r in roles.items() if r is Role.WOLF}
    out = dict.fromkeys(LS_KEYS, 0)
    rule = _lie_rule(game, label)
    if rule is None:
        return out  # left out, counted by lies_left_out
    subjects, rose, follows, named = rule
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
        by_seq[lb.source_seq].day
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


def _count_turn(row: Row, e: Event, failures: list[bool]) -> bool:
    """Close one turn. `e` carries the turn's usage (the reply's event, or the fallback_action);
    `failures` flags each rejected attempt before it as provider-caused or not. A fallback is
    the provider's if it says so or (older logs) its last rejection reads that way. Returns
    True for such a turn, whose usage is the failed calls and not a model attempt."""
    fallback = e.type is EventType.FALLBACK_ACTION
    if fallback and (e.meta.reason == "provider" or (failures and failures[-1])):
        return True  # the model never answered: none of turns, fallbacks, retries
    row.turns += 1
    row.fallbacks += fallback
    if failures and not failures[0]:  # a model error on attempt 1 means attempt 2 was made
        row.retries += 1
        row.retry_wins += not fallback
    return False


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

    failed: dict[str, list[bool]] = defaultdict(list)  # seat -> provider flag per rejected attempt
    accused: dict[tuple[str, int], set[str]] = defaultdict(set)
    accused_day: dict[tuple[str, int], int] = {}
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
            row.jev_statements += e.seq in game.jev_read
        if e.type is EventType.INVALID_OUTPUT:
            provider = is_provider_failure(e.meta.reason, e.meta.error)  # also pre-reason logs
            failed[e.actor].append(provider)
            if provider:
                row.provider_errors += 1
            else:
                row.invalid += 1
                row.attempts += 1
        if e.type is EventType.ACCUSATION and e.target:
            key = (e.actor, e.meta.statement_seq or e.seq)
            accused[key].add(e.target)
            accused_day[key] = e.day  # the statement's day; the statement itself may be missing
        if e.suspicion is not None and roles[e.actor] is not Role.WOLF:
            last_susp[(e.actor, e.day)] = e
        if u := e.meta.usage:
            provider_turn = _count_turn(row, e, failed.pop(e.actor, []))
            if not provider_turn and e.type is not EventType.FALLBACK_ACTION:
                row.attempts += 1  # the accepted reply; its rejected ones were counted above
            row.input_tokens += u.input_tokens
            row.output_tokens += u.output_tokens
            row.cost_usd += u.cost_usd

    for (actor, _day), e in last_susp.items():
        rows[actor].detection += [
            (e.day, s.p, int(roles[s.player] is Role.WOLF)) for s in e.suspicion or []
        ]

    votes = [e for e in events if e.type is EventType.VOTE]
    for (actor, stmt), targets in accused.items():
        day = accused_day[(actor, stmt)]
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


# --- deception matrix: liar model x listener model -------------------------------------------

DM_KEYS = ("heard", "comparisons", "moved", "voters", "followed")
BASE_KEYS = ("heard", "voters", "followed")
# What a lie claimed, and so which truthful claims its baseline is read from.
CLAIM_KINDS = ("role_claim", "seer_wolf", "seer_clear", "accusation")


def _listeners(game: GameData, seq: int, named: set[str]) -> list[str]:
    """Non-wolf seats alive at `seq` that the claim does not name.

    Wolves are left out as in lie_success: they know who the wolves are.
    """
    dead = _dead_before(game.events, seq)
    return [
        p for p, r in game.roles.items() if r is not Role.WOLF and p not in named and p not in dead
    ]


def _next_vote(votes: list[Event], seat: str, seq: int, day: int) -> Event | None:
    """The seat's first vote after `seq` on the same day (None if it never voted that day)."""
    return next((v for v in votes if v.actor == seat and v.seq > seq and v.day == day), None)


def _belief_moved(
    events: list[Event], seat: str, seq: int, subjects: set[str], rose: bool
) -> bool | None:
    """Did the seat's first suspicion update after `seq` move the subjects the lie's way?

    Compares that update with the seat's last suspicion before `seq`, summed over the subjects
    with a value in both. None when there is nothing to compare.
    """
    after = next(
        (e for e in events if e.seq > seq and e.actor == seat and e.suspicion is not None), None
    )
    if after is None:
        return None
    now = {x.player: x.p for x in after.suspicion or []}
    deltas = []
    for subj in subjects:
        before = _last_suspicion(events, seat, seq, subj)
        if before is not None and subj in now:
            deltas.append(now[subj] - before)
    if not deltas:
        return None
    return sum(deltas) > 0 if rose else sum(deltas) < 0


def _truthful_claims(
    game: GameData,
) -> Iterator[tuple[str, Event, set[str], Callable[[str | None], bool]]]:
    """(kind, event, named, follows) for every claim in the log that told the truth.

    role_claim: the claimed role is the speaker's role; follows = did not vote the speaker. A
    villager self-tag counts only when the statement says it (the labeler's rule, #596): models
    tag `villager` on statements that claim nothing, and those are no truthful claim.
    seer_wolf / seer_clear: the real seer reported a player's true side; follows as for a
    false_report (voted the reported wolf; voted neither the cleared player nor the seer).
    accusation: a non-wolf accused only real wolves (all targets of one statement together);
    follows = voted one of them.
    """
    roles = game.roles
    wolves = {p for p, r in roles.items() if r is Role.WOLF}
    text = {e.seq: e.public_text or "" for e in game.events if e.type is EventType.STATEMENT}
    accused: dict[tuple[str, int], set[str]] = defaultdict(set)
    first: dict[tuple[str, int], Event] = {}
    for e in game.events:
        a = e.actor
        if not a:
            continue
        if e.type is EventType.ROLE_CLAIM and e.meta.role is roles.get(a):
            if e.meta.role is Role.VILLAGER and not _SAYS_VILLAGER.search(
                text.get(e.meta.statement_seq, "")
            ):
                continue
            yield "role_claim", e, {a}, lambda v, a=a: v is not None and v != a
        elif (
            e.type is EventType.SEER_CLAIM
            and roles.get(a) is Role.SEER
            and e.target
            and (e.meta.result == "wolf") == (e.target in wolves)
        ):
            t = e.target
            if e.meta.result == "wolf":
                yield "seer_wolf", e, {a, t}, lambda v, t=t: v == t
            else:
                yield "seer_clear", e, {a, t}, lambda v, a=a, t=t: v not in (None, a, t)
        elif e.type is EventType.ACCUSATION and e.target and a not in wolves:
            k = (a, e.meta.statement_seq or e.seq)
            accused[k].add(e.target)
            first.setdefault(k, e)
    for (a, stmt), targets in accused.items():
        if targets <= wolves:
            yield "accusation", first[(a, stmt)], {a} | targets, lambda v, ts=targets: v in ts


def game_deception(game: GameData) -> tuple[Counter, Counter]:
    """Raw deception counts for one game: (pairs, baseline).

    pairs[(source, liar_model, listener_model, kind, key)] over DM_KEYS, one `heard` per (lie,
    listener): every non-wolf seat alive at the lie, except the liar and the players the lie's
    vote rule names. `followed`: the listener's next vote that day went the way the lie implied
    (lie_success's rule; abstains never follow). `moved`: the listener's next suspicion update
    moved the lie's subject the lie's way: down on the liar for a role claim, toward the claimed
    side of the reported player for a seer report, up on the accused wolf for bussing.
    `caught`: that next vote went to the liar (only the humans summary reads it).
    `kind` is what the lie claimed (CLAIM_KINDS), which picks its baseline.

    baseline[(listener_model, kind, key)] over BASE_KEYS: the same listeners, next vote and vote
    direction for every truthful claim of that kind (_truthful_claims). It reads the log, not
    the labels, so it is one baseline for both sources.
    """
    events, models = game.events, game.models
    votes = [e for e in events if e.type is EventType.VOTE]
    pairs: Counter = Counter()
    for lb in game.labels:
        if lb.source not in SOURCES or lb.label not in SUCCESS_TYPES:
            continue
        rule = _lie_rule(game, lb)
        if rule is None:
            continue
        subjects, rose, follows, named = rule
        s = lb.source_seq
        src = game.by_seq[s]
        kind = "accusation" if lb.label is LabelType.BUSSING else "role_claim"
        if lb.label is LabelType.FALSE_REPORT:
            target, result = _claim_target(game, lb)
            if target:
                kind = "seer_wolf" if result == "wolf" else "seer_clear"
                subjects, rose = {target}, kind == "seer_wolf"
        for seat in _listeners(game, s, named | {lb.actor}):
            key = (lb.source, models[lb.actor], models[seat], kind)
            pairs[(*key, "heard")] += 1
            moved = _belief_moved(events, seat, s, subjects, rose)
            if moved is not None:
                pairs[(*key, "comparisons")] += 1
                pairs[(*key, "moved")] += moved
            if v := _next_vote(votes, seat, s, src.day):
                pairs[(*key, "voters")] += 1
                pairs[(*key, "followed")] += follows(v.target)
                pairs[(*key, "caught")] += v.target == lb.actor

    base: Counter = Counter()
    for kind, e, named, follows in _truthful_claims(game):
        for seat in _listeners(game, e.seq, named):
            key = (models[seat], kind)
            base[(*key, "heard")] += 1
            if v := _next_vote(votes, seat, e.seq, e.day):
                base[(*key, "voters")] += 1
                base[(*key, "followed")] += follows(v.target)
    return pairs, base


def _follow_stats(c: dict) -> dict:
    out = {**c, "vote_follow_rate": _ratio(c["followed"], c["voters"])}
    out["vote_follow_ci"] = wilson(c["followed"], c["voters"])
    if "comparisons" in c:
        out["belief_shift_rate"] = _ratio(c["moved"], c["comparisons"])
        out["belief_shift_ci"] = wilson(c["moved"], c["comparisons"])
    return out


def deception_counts(games: list[GameData]) -> tuple[Counter, Counter]:
    """game_deception summed over games: (pairs, baseline)."""
    pairs: Counter = Counter()
    base: Counter = Counter()
    for g in games:
        p, b = game_deception(g)
        pairs.update(p)
        base.update(b)
    return pairs, base


def deception_matrix(games: list[GameData], counts: tuple[Counter, Counter] | None = None) -> dict:
    """Who fools whom: per label source, liar model -> listener model -> counts and rates, and
    one no-lie baseline per claim kind and listener model. Exploratory, like lie_success.

    Each cell carries `baseline_rate`: the vote-follow rate the listener would have had if it
    followed this cell's lies at its truthful-claim rate for each kind, weighted by the cell's
    votes of that kind; `baseline_voters` counts the votes that had a baseline to compare with.

    Rows and columns are model names, so a human seat is its own "human" row and column and
    never mixes into a model. Self-play games only fill the diagonal. A source with no labels
    has no pairs. Pass `counts` (deception_counts) to skip recounting.
    """
    pairs, base = counts or deception_counts(games)
    baseline: dict[str, dict[str, dict]] = {}
    for listener, kind in sorted({k[:2] for k in base}):
        stats = _follow_stats({k: base[(listener, kind, k)] for k in BASE_KEYS})
        baseline.setdefault(kind, {})[listener] = stats
    out: dict = {"exploratory": True, "pairs": {}, "baseline": baseline}
    for src in SOURCES:
        cells: dict[str, dict[str, dict]] = {}
        for liar, listener in sorted({(k[1], k[2]) for k in pairs if k[0] == src}):
            c = dict.fromkeys(DM_KEYS, 0)
            n = expected = 0.0
            for kind in CLAIM_KINDS:
                for k in DM_KEYS:
                    c[k] += pairs[(src, liar, listener, kind, k)]
                rate = baseline.get(kind, {}).get(listener, {}).get("vote_follow_rate")
                voters = pairs[(src, liar, listener, kind, "voters")]
                if rate is not None and voters:
                    n += voters
                    expected += voters * rate
            cell = _follow_stats(c)
            cell["baseline_rate"] = _ratio(expected, n)
            cell["baseline_voters"] = int(n)
            cells.setdefault(liar, {})[listener] = cell
        out["pairs"][src] = cells
    return out


# --- humans vs models: visitor games (#305) ---------------------------------------------------

FLOWS = ("human_to_model", "model_to_human", "model_to_model")
FLOW_KEYS = ("heard", "voters", "followed", "caught", "comparisons", "moved")


def _flow(liar: str, listener: str) -> str | None:
    """Which way a lie went; None for a human lying to a human."""
    if liar == HUMAN:
        return None if listener == HUMAN else "human_to_model"
    return "model_to_human" if listener == HUMAN else "model_to_model"


def _seat_record(row: Row) -> dict:
    return {
        "seats": row.games,
        "wins": row.wins,
        "draws": row.draws,
        "losses": row.losses,
        "win_rate": _ratio(row.wins, row.games),
        "win_ci": wilson(row.wins, row.games),
    }


def humans_vs_models(games: list[GameData], pairs: Counter | None = None) -> dict:
    """Do humans fool models, or get fooled, more than models fool each other? Exploratory.

    `by_role`: human seats' results per group; `models_by_role`: the AI seats in the same games.
    `lies[source][flow]` sums deception-matrix counts by direction: human liar and model
    listener, model liar and human listener, and model to model (every game in the batch, the
    comparison). `followed` is the matrix's vote rule; `caught` is a next vote for the liar.
    Only counts and rates, never a seat name or game id: visitor games stay anonymous.
    """
    if pairs is None:
        pairs = deception_counts(games)[0]
    by_role: dict[str, Row] = defaultdict(Row)
    models_by_role: dict[str, Row] = defaultdict(Row)
    played = [g for g in games if HUMAN in g.models.values()]
    for g in played:
        for seat, row in game_rows(g).items():
            side = by_role if g.models[seat] == HUMAN else models_by_role
            for grp in _groups(g.roles[seat]):
                side[grp].add(row)
    flows = {src: {f: dict.fromkeys(FLOW_KEYS, 0) for f in FLOWS} for src in SOURCES}
    for (src, liar, listener, _kind, key), n in pairs.items():
        if key in FLOW_KEYS and (f := _flow(liar, listener)):
            flows[src][f][key] += n
    lies = {
        src: {
            f: {
                **_follow_stats(c),
                "catch_rate": _ratio(c["caught"], c["voters"]),
                "catch_ci": wilson(c["caught"], c["voters"]),
            }
            for f, c in by_flow.items()
        }
        for src, by_flow in flows.items()
    }
    return {
        "exploratory": True,
        "games": len(played),
        "seats": by_role["all"].games if by_role else 0,
        "by_role": {grp: _seat_record(r) for grp, r in sorted(by_role.items())},
        "models_by_role": {grp: _seat_record(r) for grp, r in sorted(models_by_role.items())},
        "lies": lies,
    }


# --- role skill: doctor saves and seer checks against chance ---------------------------------

SKILL_KEYS = ("n", "hits", "self_protects", "chance_sum", "fallbacks")


def game_role_skill(game: GameData) -> Counter:
    """Raw doctor / seer counts for one game: [(role, model, key)] over SKILL_KEYS.

    Doctor, one `n` per doctor_save event: `hits` when the night's night_kill (same day) hit
    the protected player, so nobody died of the protect; `self_protects` when the doctor
    protected itself; `chance_sum` adds 1 / living players (the doctor included) at that
    point of the night. Seer, one `n` per seer_check: `hits` when the checked player is a
    wolf; `chance_sum` adds living wolves / living others. Living = every seat minus the
    death_reveal / elimination events before the check, which is what the players could see.

    A decision with meta.fallback is the engine's random pick, not the model's: it counts
    under `fallbacks` and in nothing else. Human seats are skipped.
    """
    events, roles, models = game.events, game.roles, game.models
    kills = {e.day: e.target for e in events if e.type is EventType.NIGHT_KILL}
    out: Counter = Counter()
    for e in events:
        if e.type not in (EventType.DOCTOR_SAVE, EventType.SEER_CHECK) or not e.actor:
            continue
        model = models.get(e.actor)
        if model is None or model == HUMAN or not e.target:
            continue
        doctor = e.type is EventType.DOCTOR_SAVE
        key = ("doctor" if doctor else "seer", model)
        if e.meta.fallback:
            out[(*key, "fallbacks")] += 1
            continue
        living = [s for s in roles if s not in _dead_before(events, e.seq)]
        out[(*key, "n")] += 1
        if doctor:
            out[(*key, "hits")] += kills.get(e.day) == e.target
            out[(*key, "self_protects")] += e.target == e.actor
            out[(*key, "chance_sum")] += 1 / len(living)
        else:
            others = [s for s in living if s != e.actor]
            out[(*key, "hits")] += roles.get(e.target) is Role.WOLF
            out[(*key, "chance_sum")] += (
                sum(roles[s] is Role.WOLF for s in others) / len(others) if others else 0.0
            )
    return out


def _skill_stats(c: Counter, role: str, model: str | None) -> dict:
    """Stats for one (role, model), or for the role over every model when `model` is None."""
    tot = {
        k: sum(v for (r, m, kk), v in c.items() if r == role and kk == k and model in (None, m))
        for k in SKILL_KEYS
    }
    n, hits = tot["n"], tot["hits"]
    rate = _ratio(hits, n)
    ci, chance = wilson(hits, n), _ratio(tot["chance_sum"], n)
    if role == "doctor":
        return {
            "nights": n,
            "saves": hits,
            "self_protects": tot["self_protects"],
            "fallbacks": tot["fallbacks"],
            "save_rate": rate,
            "save_ci": ci,
            "chance_rate": chance,
        }
    return {
        "checks": n,
        "wolves_found": hits,
        "fallbacks": tot["fallbacks"],
        "hit_rate": rate,
        "hit_ci": ci,
        "chance_rate": chance,
    }


def role_skill(games: list[GameData]) -> dict:
    """Doctor and seer skill per model, with the chance rate to read it against. Exploratory:
    a chance baseline ignores what the player knew (the doctor's no-repeat rule, a seer who
    already found a wolf), so it is a yardstick, not a test."""
    counts: Counter = Counter()
    for g in games:
        counts.update(game_role_skill(g))
    out: dict = {}
    for role in ("doctor", "seer"):
        names = sorted({m for (r, m, _k) in counts if r == role})
        out[role] = {
            "models": {m: _skill_stats(counts, role, m) for m in names},
            "all": _skill_stats(counts, role, None),
        }
    return out


def _groups(role: Role) -> tuple[str, ...]:
    return ("all", "wolf") if role is Role.WOLF else ("all", "village", role.value)


def _ratio(k: float, n: float) -> float | None:
    return k / n if n else None


def _lies(row: Row, source: str) -> dict:
    by_type = {t: n for (src, t), n in row.lies.items() if src == source}
    ls = {k: row.lie_success[(source, k)] for k in LS_KEYS}
    # Jev may not have read every statement: its rate is over the ones it read.
    statements = row.jev_statements if source == "jev" else row.statements
    return {
        "lies": sum(by_type.values()),
        "by_type": by_type,
        "lie_rate": _ratio(sum(by_type.values()), statements),
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
        # invalid_output_rate and invalid_attempt_rate: rejected model replies per model call
        # attempt. A retry that works still counts here, so it overstates the turns lost; see
        # fallback_rate. None of the rates below count provider errors.
        "invalid_output_rate": _ratio(row.invalid, row.attempts),
        "invalid_attempt_rate": _ratio(row.invalid, row.attempts),
        "fallback_rate": _ratio(row.fallbacks, row.turns),  # turns that ended in a fallback move
        "retry_success_rate": _ratio(row.retry_wins, row.retries),  # retries that were accepted
        "provider_errors": row.provider_errors,
        "provider_error_rate": _ratio(row.provider_errors, row.attempts + row.provider_errors),
        "tokens_per_game": _ratio(row.input_tokens + row.output_tokens, row.games),
        "cost_usd_per_game": _ratio(row.cost_usd, row.games),
    }


def _rate_match(pl: PlackettLuce, pool: dict, teams: dict[str, list], winner: str) -> set:
    """One game in one pool; `teams` maps side -> one pool key per seat. See module docstring.

    Returns the keys whose rating was updated: a key on both sides is left unchanged.
    """
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
    both = set(teams["wolf"]) & set(teams["village"])
    updated = set()
    for side, new in (("wolf", new_wolf), ("village", new_village)):
        old = comp[side]
        for k in dict.fromkeys(teams[side]):
            if k in both:
                continue
            r = pool[k]
            mu = r.mu + (new.mu - old.mu) * r.sigma**2 / old.sigma**2
            pool[k] = pl.rating(mu=mu, sigma=r.sigma * new.sigma / old.sigma)
            updated.add(k)
    return updated


def openskill_ratings(games: list[GameData]) -> dict[str, dict[str, dict]]:
    """Equal-weight PlackettLuce ratings, games in game_id order; a draw is a tie.

    Games without a winner are skipped. See the module docstring for the method.
    """
    pl = PlackettLuce()
    overall: dict = {}
    sided: dict = {}
    rated: set = set()  # models with at least one overall update (a game not against itself)
    for g in sorted(games, key=lambda g: g.game_id):
        if g.winner is None:
            continue
        seats: dict[str, list[str]] = {side: [] for side in SIDES}
        for seat, role in g.roles.items():
            seats["wolf" if role is Role.WOLF else "village"].append(g.models[seat])
        rated |= _rate_match(pl, overall, seats, g.winner)
        by_side = {side: [(m, side) for m in ms] for side, ms in seats.items()}
        _rate_match(pl, sided, by_side, g.winner)

    def out(r) -> dict:
        return {"mu": r.mu, "sigma": r.sigma, "ordinal": r.ordinal()}

    side_ratings = {
        side: {k[0]: out(r) for k, r in sided.items() if k != WOLF_SIDE and k[1] == side}
        for side in SIDES
    }
    return {
        "overall": {m: out(r) for m, r in sorted(overall.items()) if m in rated},
        **{side: dict(sorted(rs.items())) for side, rs in side_ratings.items()},
        WOLF_SIDE: {
            name: out(pool[WOLF_SIDE])
            for name, pool in (("overall", overall), ("sided", sided))
            if pool
        },
    }


def _without_human(ratings: dict) -> dict:
    """The human seat still counts toward its team's strength while rating, but gets no row."""
    return {
        k: ({m: r for m, r in v.items() if m != HUMAN} if k != WOLF_SIDE else v)
        for k, v in ratings.items()
    }


def compute_metrics(games: list[GameData], unfinished: int = 0) -> dict:
    """Every metric, per model and group, plus the same groups across all models."""
    per_model: dict[str, dict[str, Row]] = defaultdict(lambda: defaultdict(Row))
    by_group: dict[str, Row] = defaultdict(Row)
    counts = deception_counts(games)
    # metrics.json is a published contract (schema/metrics.schema.json): no room for a count
    # key, so the skipped labels are logged instead.
    for src, left in lies_left_out(games).items():
        if left:
            log.warning("%s labels left out of lie success and the deception matrix: %s", src, left)
    for g in games:
        roles, models = g.roles, g.models
        for seat, row in game_rows(g).items():
            for grp in _groups(roles[seat]):
                if models[seat] != HUMAN:
                    per_model[models[seat]][grp].add(row)
                by_group[grp].add(row)
    return {
        "games": len(games),
        "unfinished_games": unfinished,
        "label_sources": list(SOURCES),
        "exploratory": list(EXPLORATORY),
        "models": {
            m: {grp: summarize(r) for grp, r in sorted(groups.items())}
            for m, groups in sorted(per_model.items())
        },
        "by_group": {grp: summarize(r) for grp, r in sorted(by_group.items())},
        "openskill": _without_human(openskill_ratings(games)),
        "deception_matrix": deception_matrix(games, counts),
        "humans": humans_vs_models(games, counts[0]),
        "role_skill": role_skill(games),
    }
