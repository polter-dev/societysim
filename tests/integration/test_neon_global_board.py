"""#344: global_detectors and global_liars on a throwaway local Postgres, checked against
metrics.py on the fixture game. Skips when no Postgres binaries are installed."""

import asyncio
import json
from statistics import mean

import pytest
from test_neon_anon_privacy import BINDIR, FIXTURES, _as_anon, _connect, pg_port  # noqa: F401

from societysim.analysis.metrics import game_rows, load_game
from societysim.logging.events import EventType
from societysim.logging.sinks.neon import apply_schema, event_row, label_row
from societysim.roles import Role

pytestmark = pytest.mark.skipif(BINDIR is None, reason="no local Postgres (initdb) installed")

FIX = load_game(FIXTURES / "sample_game.jsonl")
# Every game also gets a jev copy of each self_tag label, so the jev columns have data too.
FIX.labels += [
    lb.model_copy(update={"source": "jev"}) for lb in FIX.labels if lb.source == "self_tag"
]
DIES = ("g3", "p2")  # in g3 the wolf p2 is also eliminated, so its model survives 4 of 5
SWAPPED = "llama-4-maverick"  # p1, a villager; a human in the g-human game
# Four ordinary finished games and one with p1 played by a human: every model has 5 seat-games
# but SWAPPED, which has 4 (below the threshold). The rest must not count.
GAMES = {
    **{f"g{i}": ("finished", None, False) for i in range(4)},
    "g-human": ("finished", "llm", True),
    "g-run": ("running", None, False),
    "g-fail": ("failed", None, False),
    "g-scripted": ("finished", "scripted", False),
}


def _lines(name: str, gid: str, human: bool) -> list[dict]:
    text = (FIXTURES / name).read_text(encoding="utf-8").replace("fixture-0001", gid)
    if human:
        text = text.replace(f'"{SWAPPED}"', '"human"')
    return [json.loads(x) for x in text.splitlines() if x.strip()]


def _events(gid: str, human: bool) -> list[dict]:
    events = _lines("sample_game.jsonl", gid, human)
    if gid == DIES[0]:
        last = events[-1]
        events.append({**last, "seq": last["seq"] + 1, "type": "elimination", "target": DIES[1]})
    return events


def _labels(gid: str, human: bool) -> list[dict]:
    labels = _lines("sample_game.labels.jsonl", gid, human)
    return labels + [{**lb, "source": "jev"} for lb in labels if lb["source"] == "self_tag"]


@pytest.fixture(scope="module")
def db(pg_port):  # noqa: F811
    asyncio.run(_setup(pg_port))
    return pg_port


async def _setup(port: int) -> None:
    su = await _connect(port, "postgres", "postgres")
    try:
        await su.execute("create role anonymous nologin")
        await su.execute("create role writer login")
        await su.execute("create database sim owner writer")
    finally:
        await su.close()
    w = await _connect(port, "writer")
    try:
        await apply_schema(w)
        for gid, (status, agents, human) in GAMES.items():
            await w.execute(
                "insert into games (game_id, batch_id, status, agents) values ($1, 'b', $2, $3)",
                gid,
                status,
                agents,
            )
            await w.executemany(
                "insert into events (game_id, seq, day, phase, type, actor, schema_version, event)"
                " values ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)",
                [event_row(e) for e in _events(gid, human)],
            )
            labels = _labels(gid, human)
            await w.executemany(
                "insert into labels (game_id, idx, label_type, actor, source_seq, source, label)"
                " values ($1, $2, $3, $4, $5, $6, $7::jsonb)",
                [label_row(i, lb) for i, lb in enumerate(labels)],
            )
    finally:
        await w.close()


def _ratio(k: float, n: float) -> float | None:
    return k / n if n else None


def _expected() -> tuple[dict, dict]:
    """Per-model values for one fixture game, from metrics.py where it has them."""
    roles, models, events = FIX.roles, FIX.models, FIX.events
    wolves = {s for s, r in roles.items() if r is Role.WOLF}
    rows = game_rows(FIX)
    last = {
        (e.actor, e.day): e for e in events if e.suspicion is not None and e.actor not in wolves
    }
    votes = [e for e in events if e.type is EventType.VOTE and e.actor not in wolves]
    dead = {e.target for e in events if e.type in (EventType.ELIMINATION, EventType.DEATH_REVEAL)}
    detectors, liars = {}, {}
    for seat in set(roles) - wolves:
        det = rows[seat].detection
        cast = [v for v in votes if v.actor == seat and v.target]
        detectors[models[seat]] = {
            "suspicion_gap": mean(p for _, p, y in det if y) - mean(p for _, p, y in det if not y),
            "votes": len(cast),
            "wolf_vote_rate": _ratio(sum(v.target in wolves for v in cast), len(cast)),
        }
    for seat in wolves:
        ls = rows[seat].lie_success
        liars[models[seat]] = {
            "suspicion_received": mean(
                s.p for e in last.values() for s in e.suspicion if s.player == seat
            ),
            "survival_rate": float(seat not in dead) - (seat == DIES[1]) / 5,
            "votes_received_per_game": sum(v.target == seat for v in votes),
            **{f"{src}_lie_voters": ls[(src, "voters")] for src in ("self_tag", "jev")},
            **{
                f"{src}_lie_success": _ratio(ls[(src, "followed")], ls[(src, "voters")])
                for src in ("self_tag", "jev")
            },
        }
    return detectors, liars


def _check(row, want: dict) -> None:
    for key, value in want.items():
        got = row[key]
        if value is None:
            assert got is None, key
        elif key.endswith("_voters") or key == "votes":
            assert got == value * (4 if row["model"] == SWAPPED else 5), key
        else:
            assert float(got) == pytest.approx(value, abs=6e-4), key


async def test_fixture_exercises_every_metric():
    detectors, liars = _expected()
    assert len(liars) == 2 and len(detectors) == 5
    assert all(d["votes"] for d in detectors.values())
    assert {lb.label.value for lb in FIX.labels} >= {"false_role_claim", "false_report", "bussing"}
    for w in liars.values():  # both wolves lied and were heard, and survived the fixture game
        assert w["self_tag_lie_voters"] and w["jev_lie_voters"] and w["survival_rate"] > 0.5


async def test_global_detectors(db):
    want, _ = _expected()
    rows = {r["model"]: r for r in await _as_anon(db, "select * from global_detectors")}
    assert set(rows) == set(want)  # no human row
    for model, row in rows.items():
        assert row["village_games"] == (4 if model == SWAPPED else 5)
        assert row["ranked"] is (model != SWAPPED)
        _check(row, want[model])


async def test_global_liars(db):
    _, want = _expected()
    rows = {r["model"]: r for r in await _as_anon(db, "select * from global_liars")}
    assert set(rows) == set(want)
    for model, row in rows.items():
        assert row["wolf_games"] == 5 and row["ranked"] is True
        _check(row, want[model])


@pytest.mark.parametrize("view", ["finished_seats", "finished_suspicion"])
async def test_internal_views_are_not_anonymous(db, view):
    import asyncpg

    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await _as_anon(db, f"select * from {view} limit 1")


@pytest.mark.parametrize("view", ["global_detectors", "global_liars"])
async def test_column_types_match_the_doc(db, view):
    from contract.test_neon_contract import doc_columns

    rows = await _as_anon(
        db,
        "select column_name, data_type from information_schema.columns"
        " where table_name = $1 order by ordinal_position",
        view,
    )
    assert [(r["column_name"], r["data_type"]) for r in rows] == doc_columns(view)
