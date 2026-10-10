"""Demo moments in a batch: lies that moved votes, wolves that lied and survived, role actions
that changed the outcome.

Moment types and scores:
- lie_moved_vote: a self_tag/jev lie (false_role_claim, false_report, bussing) where village
  voters voted the way the lie implied. Uses `metrics.lie_success`; score = followed + moved
  - base_rate * voters (the followers you'd expect anyway). Kept only if followed > 0 and
  score > 0. A lie labeled by both sources is one moment.
- wolf_survived_vote: a wolf lied on day d and the day's vote did not eliminate them. Score =
  1 + votes cast against them + 2 if a non-wolf was eliminated instead.
- doctor_save: the doctor saved the night-kill target. Score 3.
- seer_led_elimination: the seer found a wolf and a later vote eliminated that wolf. Score =
  2 + votes cast against the wolf on that day.

Ordering: score (high first), then game id, then first seq. Links are viewer query strings
(`?game=<id>&seq=<n>&mode=narrator`); prefix them with the viewer's URL.
"""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from societysim.analysis.metrics import SUCCESS_TYPES, GameData, lie_success, load_batch
from societysim.logging.events import Event, EventType
from societysim.roles import Role, SeatId

RESOLUTIONS = (EventType.ELIMINATION, EventType.NO_ELIMINATION)


class Highlight(BaseModel):
    """One entry of highlights.json (the contract, docs/DATA_CONTRACT.md). Only describes it:
    `_moment` builds the dicts. `seq` is [first, last]; names and models are null when the
    seat is unknown."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["lie_moved_vote", "wolf_survived_vote", "doctor_save", "seer_led_elimination"]
    score: float
    game_id: str
    day: int
    seq: tuple[int, int]
    actor: SeatId | None
    actor_name: str | None
    actor_model: str | None
    target: SeatId | None
    target_name: str | None
    target_model: str | None
    description: str
    link: str


Highlights = list[Highlight]


def _moment(g: GameData, kind: str, day: int, seqs: tuple[int, int], actor, target, score, desc):
    seats = g.events[0].meta.seats or {}

    def who(s):
        return (seats[s].name, seats[s].model) if s in seats else (None, None)

    (an, am), (tn, tm) = who(actor), who(target)
    return {
        "type": kind,
        "score": round(float(score), 2),
        "game_id": g.game_id,
        "day": day,
        "seq": list(seqs),
        "actor": actor,
        "actor_name": an,
        "actor_model": am,
        "target": target,
        "target_name": tn,
        "target_model": tm,
        "description": desc,
        "link": f"?game={g.game_id}&seq={seqs[0]}&mode=narrator",
    }


def _day_end(g: GameData, day: int) -> Event | None:
    """The day's elimination / no_elimination event, else its last vote."""
    votes = [e for e in g.events if e.day == day and e.type in (*RESOLUTIONS, EventType.VOTE)]
    return next((e for e in votes if e.type in RESOLUTIONS), votes[-1] if votes else None)


def _name(g: GameData, seat: str | None, role: str = "") -> str:
    seats = g.events[0].meta.seats or {}
    tag = f"{seat}, {role}" if role else seat
    return f"{seats[seat].name} ({tag})" if seat in seats else str(seat)


def lie_moments(g: GameData) -> list[dict]:
    out: dict[tuple, dict] = {}
    by_seq = g.by_seq
    for lb in g.labels:
        if lb.source not in ("self_tag", "jev") or lb.label not in SUCCESS_TYPES:
            continue
        key = (lb.label, lb.actor, lb.source_seq)
        if key in out:
            continue
        r = lie_success(g, lb)
        expected = r["base_followed"] / r["base_voters"] * r["voters"] if r["base_voters"] else 0
        score = r["followed"] + r["moved"] - expected
        src = by_seq[lb.source_seq]
        end = _day_end(g, src.day)
        if not r["followed"] or score <= 0 or end is None:
            continue
        base = f" (base rate predicts {expected:.1f})" if r["base_voters"] else ""
        desc = (
            f"{_name(g, lb.actor)} {lb.label.value.replace('_', ' ')}: {lb.detail} "
            f"{r['followed']}/{r['voters']} village voters followed{base}, "
            f"{r['moved']}/{r['comparisons']} suspicions moved."
        )
        out[key] = _moment(
            g,
            "lie_moved_vote",
            src.day,
            (lb.source_seq, end.seq),
            lb.actor,
            src.target,
            score,
            desc,
        )
    return list(out.values())


def wolf_survival_moments(g: GameData) -> list[dict]:
    roles, out, by_seq = g.roles, [], g.by_seq
    first_lie: dict[tuple[str, int], int] = {}
    for lb in g.labels:
        if lb.source in ("self_tag", "jev") and roles.get(lb.actor) is Role.WOLF:
            k = (lb.actor, by_seq[lb.source_seq].day)
            first_lie[k] = min(first_lie.get(k, lb.source_seq), lb.source_seq)
    for (wolf, day), seq in sorted(first_lie.items()):
        end = _day_end(g, day)
        if end is None or end.type is EventType.VOTE or end.target == wolf:
            continue  # no vote resolved that day, or the wolf was voted out
        against = sum(
            e.type is EventType.VOTE and e.day == day and e.target == wolf for e in g.events
        )
        swap = end.type is EventType.ELIMINATION and roles.get(end.target) is not Role.WOLF
        outcome = f"{_name(g, end.target)} was eliminated instead" if swap else "nobody went out"
        desc = (
            f"{_name(g, wolf, 'wolf')} lied on day {day} and survived the vote "
            f"({against} vote{'' if against == 1 else 's'} against); {outcome}."
        )
        score = 1 + against + 2 * swap
        out.append(
            _moment(g, "wolf_survived_vote", day, (seq, end.seq), wolf, end.target, score, desc)
        )
    return out


def role_action_moments(g: GameData) -> list[dict]:
    out = []
    for e in g.events:
        if e.type is EventType.DOCTOR_SAVE and e.meta.saved:
            desc = f"{_name(g, e.actor, 'doctor')} saved {_name(g, e.target)} from the night kill."
            out.append(_moment(g, "doctor_save", e.day, (e.seq, e.seq), e.actor, e.target, 3, desc))
        if e.type is EventType.SEER_CHECK and e.meta.result == "wolf":
            elim = next(
                (
                    x
                    for x in g.events
                    if x.seq > e.seq and x.type is EventType.ELIMINATION and x.target == e.target
                ),
                None,
            )
            if elim is None:
                continue
            votes = sum(
                x.type is EventType.VOTE and x.day == elim.day and x.target == e.target
                for x in g.events
            )
            desc = (
                f"{_name(g, e.actor, 'seer')} found {_name(g, e.target)} on night {e.day}; "
                f"the village voted them out on day {elim.day} "
                f"({votes} vote{'' if votes == 1 else 's'})."
            )
            seqs = (e.seq, elim.seq)
            out.append(
                _moment(g, "seer_led_elimination", e.day, seqs, e.actor, e.target, 2 + votes, desc)
            )
    return out


def find_highlights(games: list[GameData]) -> list[dict]:
    moments = [
        m
        for g in games
        for m in (*lie_moments(g), *wolf_survival_moments(g), *role_action_moments(g))
    ]
    return sorted(moments, key=lambda m: (-m["score"], m["game_id"], m["seq"][0], m["type"]))


def write_highlights(batch_dir: Path, top: int = 10, out: Path | None = None) -> list[dict]:
    """Top moments of a batch, also written to `out` (default <batch_dir>/highlights.json)."""
    moments = find_highlights(load_batch(batch_dir))[:top]
    (out or batch_dir / "highlights.json").write_text(json.dumps(moments, indent=2) + "\n")
    return moments
