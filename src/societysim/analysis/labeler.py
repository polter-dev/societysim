"""Lie labels from a finished game log: <game_id>.labels.jsonl next to <game_id>.jsonl.

Labels come from three sources (docs/LOG_SCHEMA.md, "Label"):
- self_tag: the agents' own structured tags (accusation, role_claim, seer_claim events)
- jev: Jev's classification of each statement, passed in as {statement_seq: JevVerdict}
- engine: game facts alone (votes, eliminations)

The raw log is only ever read.
"""

import json
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from societysim.analysis.jev import JevResult
from societysim.logging.events import Event, EventType, Label, LabelType
from societysim.roles import Role, SeatId, SeerResult


class JevReport(BaseModel):
    target: SeatId
    result: SeerResult


class JevVerdict(BaseModel):
    """What Jev read in one statement. The labeler's only dependency on analysis/jev.py."""

    accuse: list[SeatId] = []
    claim_role: Role | None = None
    seer_report: JevReport | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)


Jev = dict[int, JevVerdict]  # statement seq -> verdict


def jev_verdicts(results: list[JevResult]) -> Jev:
    """Jev's probabilities -> verdicts. A yes needs P > 0.5; claim confidence covers all."""
    return {
        r.seq: JevVerdict(
            accuse=[seat for seat, p in r.accuse.items() if p > 0.5],
            claim_role=r.claim_role,
            seer_report=JevReport(target=r.seer_target, result=r.seer_result)
            if r.seer_report > 0.5
            else None,
            confidence=r.claim_role_confidence,
        )
        for r in results
    }


@dataclass
class _Tag:
    """One claim made in a statement, from either source."""

    kind: EventType  # ACCUSATION, ROLE_CLAIM or SEER_CLAIM
    seq: int  # label source_seq: the self-tag event, or the statement for Jev
    statement_seq: int
    actor: str
    target: str | None = None
    role: Role | None = None
    result: SeerResult | None = None
    confidence: float = 1.0


class _Game:
    def __init__(self, events: list[Event]):
        self.events = events
        self.game_id = events[0].game_id
        seats = events[0].meta.seats or {}
        self.roles = {s: seat.role for s, seat in seats.items()}
        self.names = {s: seat.name for s, seat in seats.items()}
        self.wolves = {s for s, r in self.roles.items() if r is Role.WOLF}
        self.statements = [e for e in events if e.type is EventType.STATEMENT]

    def who(self, seat: str | None) -> str:
        return f"{self.names.get(seat, seat)} ({seat})"

    def checks_before(self, seq: int) -> dict[str, SeerResult]:
        return {
            e.target: e.meta.result
            for e in self.events
            if e.type is EventType.SEER_CHECK and e.seq < seq and e.target and e.meta.result
        }

    def dead_before(self, seq: int) -> set[str]:
        deaths = (EventType.DEATH_REVEAL, EventType.ELIMINATION)
        return {e.target for e in self.events if e.type in deaths and e.seq < seq and e.target}


def _self_tags(game: _Game) -> list[_Tag]:
    kinds = (EventType.ACCUSATION, EventType.ROLE_CLAIM, EventType.SEER_CLAIM)
    return [
        _Tag(e.type, e.seq, e.meta.statement_seq, e.actor, e.target, e.meta.role, e.meta.result)
        for e in game.events
        if e.type in kinds
    ]


def _jev_tags(game: _Game, jev: Jev) -> list[_Tag]:
    tags = []
    for s in game.statements:
        v = jev.get(s.seq)
        if v is None:
            continue
        base = {"seq": s.seq, "statement_seq": s.seq, "actor": s.actor, "confidence": v.confidence}
        tags += [_Tag(EventType.ACCUSATION, target=t, **base) for t in v.accuse]
        if v.claim_role:
            tags.append(_Tag(EventType.ROLE_CLAIM, role=v.claim_role, **base))
        if v.seer_report:
            r = v.seer_report
            tags.append(_Tag(EventType.SEER_CLAIM, target=r.target, result=r.result, **base))
    return tags


def _label(
    game: _Game, kind: LabelType, source: str, actor: str, seq: int, conf: float, detail: str
) -> Label:
    return Label(
        game_id=game.game_id,
        label=kind,
        actor=actor,
        source_seq=seq,
        source=source,
        confidence=conf,
        detail=detail,
    )


def _claim_labels(
    game: _Game, tags: list[_Tag], source: str, statements: list[tuple[Event, float]]
) -> list[Label]:
    """Labels from claims; `statements` are the (statement, confidence) pairs this source read."""
    out = []
    for t in tags:
        true_role = game.roles[t.actor]
        detail = None
        if t.kind is EventType.ROLE_CLAIM and t.role != true_role:
            kind = LabelType.FALSE_ROLE_CLAIM
            detail = f"{game.who(t.actor)} claimed {t.role}; they are a {true_role}."
        elif t.kind is EventType.SEER_CLAIM:
            kind = LabelType.FALSE_REPORT
            said = f"{game.who(t.actor)} reported {game.who(t.target)} as {t.result}"
            actual = game.checks_before(t.seq).get(t.target)
            if true_role is not Role.SEER:
                detail = f"{said} but is not the Seer."
            elif actual != t.result:
                detail = f"{said}; their check said {actual or 'nothing'}."
        elif t.kind is EventType.ACCUSATION and t.actor in game.wolves and t.target in game.wolves:
            kind = LabelType.BUSSING
            detail = f"{game.who(t.actor)}, a wolf, accused fellow wolf {game.who(t.target)}."
        if detail:
            out.append(_label(game, kind, source, t.actor, t.seq, t.confidence, detail))

    # hidden_knowledge: the Seer speaks while holding an unreported wolf result on a living
    # player. One label per day, on the first such statement.
    labelled_days = set()
    for s, conf in statements:
        if game.roles.get(s.actor) is not Role.SEER or s.day in labelled_days:
            continue
        held = {p for p, r in game.checks_before(s.seq).items() if r == "wolf"}
        reported = {
            t.target
            for t in tags
            if t.kind is EventType.SEER_CLAIM and t.actor == s.actor and t.statement_seq <= s.seq
        }
        hidden = sorted(held - reported - game.dead_before(s.seq))
        if hidden:
            labelled_days.add(s.day)
            who = ", ".join(game.who(p) for p in hidden)
            detail = f"{game.who(s.actor)} knew {who} was a wolf and spoke without reporting it."
            kind = LabelType.HIDDEN_KNOWLEDGE
            out.append(_label(game, kind, source, s.actor, s.seq, conf, detail))
    return out


def _engine_labels(game: _Game) -> list[Label]:
    out = []
    votes = [e for e in game.events if e.type is EventType.VOTE and e.target]
    for v in votes:
        if v.actor in game.wolves and v.target in game.wolves:
            detail = f"{game.who(v.actor)}, a wolf, voted for fellow wolf {game.who(v.target)}."
            out.append(_label(game, LabelType.BUSSING, "engine", v.actor, v.seq, 1.0, detail))
    for e in game.events:
        if e.type is not EventType.ELIMINATION or e.target in game.wolves:
            continue
        for v in votes:
            if v.day == e.day and v.seq < e.seq and v.target == e.target:
                detail = (
                    f"{game.who(v.actor)} voted to eliminate {game.who(e.target)}, "
                    f"a {game.roles[e.target]}."
                )
                kind = LabelType.MIS_ELIMINATION
                out.append(_label(game, kind, "engine", v.actor, v.seq, 1.0, detail))
    return out


def label_game(events: list[Event], jev: Jev | None = None) -> list[Label]:
    """All labels for one game, sorted by source_seq. Without Jev: self-tags and engine only."""
    game = _Game(events)
    statements = [(s, 1.0) for s in game.statements]
    labels = _claim_labels(game, _self_tags(game), "self_tag", statements)
    labels += _engine_labels(game)
    if jev is not None:
        covered = [(s, jev[s.seq].confidence) for s in game.statements if s.seq in jev]
        labels += _claim_labels(game, _jev_tags(game, jev), "jev", covered)
    return sorted(labels, key=lambda lb: (lb.source_seq, lb.label, lb.source, lb.actor))


def agreement(events: list[Event], labels: list[Label], jev: Jev) -> dict[str, Counter]:
    """Per label type: both / self_tag_only / jev_only, over the statements Jev classified.

    A self-tag label and a Jev label agree when they have the same type, actor and statement.
    """
    stmt_of = {e.seq: e.meta.statement_seq or e.seq for e in events}
    keys = {
        src: {
            (lb.label.value, lb.actor, stmt_of[lb.source_seq])
            for lb in labels
            if lb.source == src and stmt_of[lb.source_seq] in jev
        }
        for src in ("self_tag", "jev")
    }
    out: dict[str, Counter] = {}
    for key in keys["self_tag"] | keys["jev"]:
        in_self, in_jev = key in keys["self_tag"], key in keys["jev"]
        col = "both" if in_self and in_jev else "self_tag_only" if in_self else "jev_only"
        out.setdefault(key[0], Counter())[col] += 1
    return out


def _table(counts: dict[str, Counter]) -> dict[str, dict]:
    table = {}
    for name in LabelType:
        c = counts.get(name.value, Counter())
        total = c["both"] + c["self_tag_only"] + c["jev_only"]
        table[name.value] = {
            "both": c["both"],
            "self_tag_only": c["self_tag_only"],
            "jev_only": c["jev_only"],
            "agreement": round(c["both"] / total, 3) if total else None,
        }
    return table


def format_table(table: dict[str, dict]) -> str:
    rows = [f"{'label':<18} {'both':>5} {'self':>5} {'jev':>5} {'agree':>6}"]
    for name, r in table.items():
        agree = "-" if r["agreement"] is None else f"{r['agreement']:.2f}"
        rows.append(
            f"{name:<18} {r['both']:>5} {r['self_tag_only']:>5} {r['jev_only']:>5} {agree:>6}"
        )
    return "\n".join(rows)


def read_log(path: Path) -> list[Event]:
    return [Event.model_validate_json(line) for line in path.read_text().splitlines() if line]


def label_batch(
    batch_dir: Path, jev_for: Callable[[list[Event]], Jev] | None = None
) -> dict[str, dict] | None:
    """Write <game_id>.labels.jsonl next to every game log in batch_dir.

    With jev_for, also print the self-tag vs Jev agreement table and write it to
    <batch_dir>/agreement.json. Returns that table, or None without Jev. Empty logs are
    skipped with a warning.
    """
    totals: dict[str, Counter] = {}
    logs = sorted(p for p in batch_dir.glob("*.jsonl") if not p.name.endswith(".labels.jsonl"))
    games = 0
    for path in logs:
        events = read_log(path)
        if not events:
            print(f"warning: {path.name} is empty, skipped")
            continue
        games += 1
        jev = jev_for(events) if jev_for else None
        labels = label_game(events, jev)
        out = path.with_name(f"{events[0].game_id}.labels.jsonl")
        out.write_text("".join(lb.model_dump_json() + "\n" for lb in labels))
        for name, c in (agreement(events, labels, jev) if jev is not None else {}).items():
            totals.setdefault(name, Counter()).update(c)
    print(f"labelled {games} games in {batch_dir}")
    if jev_for is None:
        return None
    table = _table(totals)
    (batch_dir / "agreement.json").write_text(json.dumps(table, indent=2) + "\n")
    print(format_table(table))
    return table
