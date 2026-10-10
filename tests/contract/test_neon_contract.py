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
VIEWS = ("leaderboard", "leaderboard_by_batch", "lies_by_model")
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
    body = "\n" + re.search(rf"create or replace view {name} as\n(.*?);\n", SQL, re.S)[1]
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
    assert granted == {*TABLES, *VIEWS}
    assert "grant insert" not in SQL and "grant all" not in SQL


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
