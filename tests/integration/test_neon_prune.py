"""sync-neon --prune: dry run by default, transactional delete of only the matched games.
Fake connection only; nothing here touches a real database."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from societysim.cli import app
from societysim.logging.sinks.neon import PruneBlocked, find_prunable, prune_games

NOW = datetime.now(UTC)  # stands in for the database clock; the CLI uses db_now from the query


def _game(gid, batch="real", has_end=True, age_h=48, status="finished", events=10, labels=2):
    return {
        "game_id": gid,
        "batch_id": batch,
        "status": status,
        "db_now": NOW,
        "last_activity": None if age_h is None else NOW - timedelta(hours=age_h),
        "has_end": has_end,
        "events": events,
        "labels": labels,
    }


GAMES = [
    _game("ok-1"),
    _game("ok-2", has_end=False, age_h=1, status="running"),  # still running: keep
    _game("part-1", has_end=False, status="failed"),  # partial and old: prune
    _game("nostart", has_end=False, age_h=None, status="failed"),  # unknown age: keep
    _game("t-1", batch="smoke"),  # test batch: prune
    _game("t-2", batch="smoke", has_end=False, age_h=1),  # test batch beats age
    _game("fixture-0001", batch="x"),  # fixture: prune
]


class PgFake:
    def __init__(self, games=GAMES, fail_on=None, has_inserted_at=True):
        self.has_inserted_at = has_inserted_at
        self.games = [dict(g) for g in games]
        self.statements: list[tuple[str, tuple]] = []
        self.committed: list[tuple[str, tuple]] = []
        self.in_tx = False
        self.fail_on = fail_on
        self.closed = False

    async def fetchval(self, sql, *args):
        assert sql.lstrip().startswith("select"), "the column check must be read-only"
        return self.has_inserted_at

    async def fetch(self, sql, *args):
        assert sql.lstrip().startswith("select"), "find_prunable must be read-only"
        rows = []
        for g in self.games:
            g = dict(g)
            if "events_log" in g:  # emulate: (select max(e.inserted_at) ...); meta.ts is ignored
                times = [e["inserted_at"] for e in g.pop("events_log")]
                g["last_activity"] = max(times, default=None)
            rows.append(g)
        return rows

    async def execute(self, sql, *args):
        self.statements.append((sql, args))
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("boom")

    def transaction(self):
        return _Tx(self)

    async def close(self):
        self.closed = True


class _Tx:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        self.conn.in_tx = True
        self.start = len(self.conn.statements)

    async def __aexit__(self, exc_type, *_):
        self.conn.in_tx = False
        if exc_type is None:
            self.conn.committed += self.conn.statements[self.start :]


async def test_find_prunable_reasons():
    found = await find_prunable(PgFake(), older_than_hours=6, batch_ids=["smoke"])
    reasons = {g["game_id"]: g["reason"] for g in found}
    assert list(reasons) == ["part-1", "t-1", "t-2", "fixture-0001"]
    assert reasons["part-1"].startswith("partial: no game_end")
    assert reasons["t-1"] == "test batch smoke" == reasons["t-2"]
    assert reasons["fixture-0001"] == "fixture"


async def test_find_prunable_without_batches_leaves_test_batch_alone():
    found = await find_prunable(PgFake())
    assert [g["game_id"] for g in found] == ["part-1", "fixture-0001"]


async def test_find_prunable_age_cutoff():
    games = [
        _game("a", has_end=False, age_h=5, status="running"),
        _game("b", has_end=False, age_h=7, status="running"),
    ]
    found = await find_prunable(PgFake(games), older_than_hours=6)
    assert [g["game_id"] for g in found] == ["b"]


async def test_running_game_is_judged_by_database_time_not_local_time():
    # A running game active 1h ago by the database clock. The machine running the CLI is
    # years ahead, but that must not make the game look stale.
    db_now = datetime(2020, 1, 1, tzinfo=UTC)
    live = _game("live", has_end=False, age_h=1, status="running") | {"db_now": db_now}
    live["last_activity"] = db_now - timedelta(hours=1)
    assert await find_prunable(PgFake([live])) == []


async def test_long_running_game_with_old_meta_ts_but_recent_inserts_is_kept():
    old_ts = (NOW - timedelta(hours=30)).isoformat()
    game = _game("long", has_end=False, status="running") | {
        "events_log": [
            {"meta_ts": old_ts, "inserted_at": NOW - timedelta(hours=9)},
            {"meta_ts": old_ts, "inserted_at": NOW - timedelta(minutes=2)},
        ]
    }
    assert await find_prunable(PgFake([game])) == []
    game["events_log"] = [{"meta_ts": old_ts, "inserted_at": NOW - timedelta(hours=9)}]
    assert [g["game_id"] for g in await find_prunable(PgFake([game]))] == ["long"]


async def test_game_with_no_events_is_never_stale():
    game = _game("empty", has_end=False, status="running") | {"events_log": []}
    assert await find_prunable(PgFake([game])) == []


async def test_candidate_query_uses_database_time_only():
    from societysim.logging.sinks.neon import _PRUNE_CANDIDATES as sql

    assert "now() as db_now" in sql
    assert "max(e.inserted_at)" in sql
    assert "started_at" not in sql and "'ts'" not in sql


async def test_prune_refuses_when_inserted_at_column_is_missing():
    conn = PgFake(has_inserted_at=False)
    with pytest.raises(PruneBlocked, match="001_events_inserted_at"):
        await find_prunable(conn, batch_ids=["smoke"])
    assert conn.statements == []


def test_prune_cli_refuses_without_migration_even_with_apply(fake_neon):
    fake_neon.has_inserted_at = False
    res = CliRunner().invoke(app, ["sync-neon", "--prune", "--apply", "--batch", "smoke"])
    assert res.exit_code == 1
    assert "refusing to prune" in res.output and "001_events_inserted_at.sql" in res.output
    assert fake_neon.statements == [] and fake_neon.committed == [] and fake_neon.closed


def test_migration_and_schema_add_inserted_at():
    root = Path(__file__).resolve().parents[2]
    ddl = (
        "alter table events add column if not exists inserted_at timestamptz not null default now()"
    )
    assert ddl in (root / "neon/schema.sql").read_text(encoding="utf-8").lower()
    migration = root / "neon/migrations/001_events_inserted_at.sql"
    assert ddl in migration.read_text(encoding="utf-8").lower()


async def test_finished_game_without_game_end_row_is_not_partial():
    lost_end = _game("lost-end", has_end=False, age_h=48, status="finished")
    stale_running = _game("stale", has_end=False, age_h=48, status="running")
    found = await find_prunable(PgFake([lost_end, stale_running]))
    assert [g["game_id"] for g in found] == ["stale"]


async def test_fixture_match_is_exact_not_a_prefix():
    games = [_game("fixture-0001"), _game("fixture-real-1", batch="fixture-run")]
    found = await find_prunable(PgFake(games))
    assert [g["game_id"] for g in found] == ["fixture-0001"]


async def test_prune_games_runs_in_a_transaction_on_only_those_ids():
    conn = PgFake()
    await prune_games(conn, ["a", "b", "a"])
    assert [s for s, _ in conn.committed] == [
        "delete from events where game_id = any($1)",
        "delete from labels where game_id = any($1)",
        "delete from games where game_id = any($1)",
    ]
    assert all(args == (["a", "b"],) for _, args in conn.committed)


async def test_prune_games_failure_commits_nothing():
    conn = PgFake(fail_on="from labels")
    with pytest.raises(RuntimeError):
        await prune_games(conn, ["a"])
    assert conn.committed == []


async def test_prune_games_empty_does_nothing():
    conn = PgFake()
    await prune_games(conn, [])
    assert conn.statements == []


@pytest.fixture
def fake_neon(monkeypatch):
    monkeypatch.setattr("societysim.cli.load_dotenv", lambda: None)
    monkeypatch.setenv("NEON_DATABASE_URL", "postgres://fake")
    conn = PgFake()

    async def connect():
        return conn

    monkeypatch.setattr("societysim.logging.sinks.neon.connect_neon", connect)
    return conn


def test_prune_cli_default_is_dry_run_and_deletes_nothing(fake_neon):
    res = CliRunner().invoke(app, ["sync-neon", "--prune", "--batch", "smoke"])
    assert res.exit_code == 0, res.output
    assert fake_neon.statements == [] and fake_neon.committed == []
    assert "would delete 4 games, 40 events, 8 labels" in res.output
    assert "dry run: nothing was deleted" in res.output
    assert "part-1" in res.output and "(test batch smoke)" in res.output
    assert "(fixture)" in res.output and "ok-1" not in res.output
    assert fake_neon.closed


def test_prune_cli_apply_deletes_only_matched_ids(fake_neon):
    res = CliRunner().invoke(app, ["sync-neon", "--prune", "--apply", "--batch", "smoke"])
    assert res.exit_code == 0, res.output
    assert "deleted 4 games" in res.output and "dry run" not in res.output
    ids = {args[0][i] for _, args in fake_neon.committed for i in range(4)}
    assert ids == {"part-1", "t-1", "t-2", "fixture-0001"}
    assert len(fake_neon.committed) == 3


def test_prune_cli_nothing_to_prune(fake_neon):
    fake_neon.games = [_game("ok-1")]
    res = CliRunner().invoke(app, ["sync-neon", "--prune", "--apply"])
    assert res.exit_code == 0, res.output
    assert "deleted 0 games" in res.output and fake_neon.statements == []


@pytest.mark.parametrize(
    "args",
    [
        ["--apply"],
        ["--batch", "smoke"],
        ["--prune", "--delete", "a"],
        ["--prune", "somedir"],
    ],
)
def test_prune_cli_bad_flag_combinations(fake_neon, args):
    res = CliRunner().invoke(app, ["sync-neon", *args])
    assert res.exit_code == 1
    assert fake_neon.statements == []
