"""Neon columns in docs/DATA_CONTRACT.md match neon/schema.sql (static), and the live database
(`-m live`, needs NEON_DATABASE_URL and SOCIETYSIM_LIVE=1)."""

import asyncio
import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
DOC = (ROOT / "docs/DATA_CONTRACT.md").read_text(encoding="utf-8")
SQL = (ROOT / "neon/schema.sql").read_text(encoding="utf-8")
TABLES = ("games", "events", "labels")
VIEWS = ("leaderboard", "leaderboard_by_batch", "lies_by_model", "events_public")
ANON = {"games", "labels", *VIEWS}  # anonymous reads events through events_public only (#323)
SQL_TYPES = {"timestamptz": "timestamp with time zone"}


def doc_columns(name: str) -> list[tuple[str, str]]:
    """(column, type) rows of the table under `<!-- neon:<name> -->`."""
    rows = []
    for line in DOC.split(f"<!-- neon:{name} -->\n", 1)[1].splitlines():
        if not line.startswith("|"):
            break
        cells = [c.strip() for c in line.strip("|").split("|")]
        if m := re.fullmatch(r"`(\w+)`", cells[0]):
            rows.append((m[1], cells[1]))
    return rows


def sql_table_columns(name: str) -> list[tuple[str, str]]:
    body = re.search(rf"create table if not exists {name} \((.*?)\n\);", SQL, re.S)[1]
    cols = [
        (m[1], SQL_TYPES.get(m[2], m[2]))
        for line in body.splitlines()
        if (m := re.match(r"\s+(\w+)\s+(\w+)", line)) and m[1] != "primary"
    ]
    added = re.findall(rf"alter table {name} add column if not exists (\w+) (\w+)", SQL)
    return cols + [(c, SQL_TYPES.get(t, t)) for c, t in added]


def sql_view_columns(name: str) -> list[str]:
    """Output column names of the view's top-level (unindented) select."""
    body = (
        "\n"
        + re.search(rf"create or replace view {name}(?: with \(\w+\))? as\n(.*?);\n", SQL, re.S)[1]
    )
    select = body[body.rindex("\nselect ") + 8 :].split("\nfrom ", 1)[0]
    items, depth, cur = [], 0, ""
    for ch in select:
        depth += (ch == "(") - (ch == ")")
        if ch == "," and depth == 0:
            items.append(cur)
            cur = ""
        else:
            cur += ch
    return [re.search(r"(\w+)\s*$", item.strip())[1] for item in [*items, cur]]


@pytest.mark.parametrize("table", TABLES)
def test_doc_tables_match_schema_sql(table):
    assert doc_columns(table) == sql_table_columns(table)


@pytest.mark.parametrize("view", VIEWS)
def test_doc_views_match_schema_sql(view):
    assert [c for c, _ in doc_columns(view)] == sql_view_columns(view)


def test_doc_grants_match_schema_sql():
    grants = re.findall(r"grant select on ([\w, ]+) to anonymous", SQL)
    granted = {t.strip() for g in grants for t in g.split(",")}
    assert granted == ANON
    assert "grant insert" not in SQL and "grant all" not in SQL
    assert "revoke all on events from anonymous" in SQL


def test_events_public_hides_running_games():
    """Static check of #323; tests/integration/test_neon_anon_privacy.py runs it on Postgres."""
    view = re.search(r"create or replace view events_public (.*?);\n", SQL, re.S)[1]
    assert view.startswith("with (security_barrier) as")
    assert "where g.status = 'finished' or e.event ->> 'visibility' = 'public'" in view
    assert "jsonb_build_object('private_reasoning', null, 'suspicion', null)" in view
    assert sql_view_columns("events_public") == [c for c, _ in sql_table_columns("events")]


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("NEON_DATABASE_URL"), reason="NEON_DATABASE_URL not set")
def test_live_columns_match_doc():
    from societysim.logging.sinks.neon import connect_neon

    async def run():
        conn = await connect_neon()
        try:
            return await conn.fetch(
                "select table_name, column_name, data_type from information_schema.columns"
                " where table_schema = 'public' and table_name = any($1)"
                " order by table_name, ordinal_position",
                [*TABLES, *VIEWS],
            )
        finally:
            await conn.close()

    live: dict[str, list[tuple[str, str]]] = {}
    for r in asyncio.run(run()):
        live.setdefault(r["table_name"], []).append((r["column_name"], r["data_type"]))
    for name in (*TABLES, *VIEWS):
        assert live.get(name) == doc_columns(name), name


def test_doc_describes_anonymous_token_flow():
    assert "/token/anonymous" in DOC
    assert "VITE_NEON_AUTH_URL" in DOC
    assert "expires_at" in DOC
