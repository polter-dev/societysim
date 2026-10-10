"""sync-neon --prune: dry run by default, transactional delete of only the matched games.
Fake connection only; nothing here touches a real database."""

from datetime import UTC, datetime, timedelta

import pytest
from typer.testing import CliRunner

from societysim.cli import app
from societysim.logging.sinks.neon import find_prunable, prune_games

NOW = datetime.now(UTC)  # the CLI uses the real clock, so build ages relative to it


def _game(gid, batch="real", has_end=True, age_h=48, status="finished", events=10, labels=2):
    return {
        "game_id": gid,
        "batch_id": batch,
        "status": status,
        "started_at": None if age_h is None else NOW - timedelta(hours=age_h),
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
    def __init__(self, games=GAMES, fail_on=None):
        self.games = [dict(g) for g in games]
        self.statements: list[tuple[str, tuple]] = []
        self.committed: list[tuple[str, tuple]] = []
        self.in_tx = False
        self.fail_on = fail_on
        self.closed = False

    async def fetch(self, sql, *args):
        assert sql.lstrip().startswith("select"), "find_prunable must be read-only"
        return self.games

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
    found = await find_prunable(PgFake(), older_than_hours=6, batch_ids=["smoke"], now=NOW)
    reasons = {g["game_id"]: g["reason"] for g in found}
    assert list(reasons) == ["part-1", "t-1", "t-2", "fixture-0001"]
    assert reasons["part-1"].startswith("partial: no game_end")
    assert reasons["t-1"] == "test batch smoke" == reasons["t-2"]
    assert reasons["fixture-0001"] == "fixture"


async def test_find_prunable_without_batches_leaves_test_batch_alone():
    found = await find_prunable(PgFake(), now=NOW)
    assert [g["game_id"] for g in found] == ["part-1", "fixture-0001"]


async def test_find_prunable_age_cutoff():
    games = [_game("a", has_end=False, age_h=5), _game("b", has_end=False, age_h=7)]
    found = await find_prunable(PgFake(games), older_than_hours=6, now=NOW)
    assert [g["game_id"] for g in found] == ["b"]


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
