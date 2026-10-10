"""Load game logs into Databricks Delta tables: games, events, labels, batch_metrics.

    uv run python scripts/databricks_ingest.py --fixture            # fixtures/sample_game.jsonl
    uv run python scripts/databricks_ingest.py --runs runs/<batch>  # finished games under a dir
    uv run python scripts/databricks_ingest.py --dashboard          # import/update the dashboard

Always writes games.jsonl, events.jsonl, labels.jsonl, batch_metrics.jsonl (the headline T3.3
metrics of each batch, long format, for the MLflow notebook) and metrics.json (the T3.3 metrics
on all the games, as `societysim report` computes them) to --out. A log without a game_end event
(a batch still running) is skipped. With DATABRICKS_HOST and
DATABRICKS_TOKEN set (and no --dry-run) it also creates the schema and volume, uploads those
files through the Files REST API and creates one Delta table per file with read_files through
the SQL Statement API. Free Edition is serverless only, so a SQL warehouse runs the statements:
DATABRICKS_WAREHOUSE_ID, or the workspace's first warehouse.

--dashboard creates or updates notebooks/databricks/societysim.lvdash.json (matched by display
name) through the Lakeview REST API and publishes it. Its datasets read the tables above and the
metric tables notebooks/databricks/metrics.py saves, so run that notebook first.

Rows are newline-delimited JSON, not Parquet: pyarrow is not a dependency here.
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import httpx

from societysim.analysis.metrics import GameData, compute_metrics, load_game
from societysim.logging.events import EventType

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "fixtures" / "sample_game.jsonl"
DASHBOARD = ROOT / "notebooks" / "databricks" / "societysim.lvdash.json"
DASHBOARD_NAME = "societysim"
GAME_END = re.compile(r'"type":\s*"game_end"')

# Column -> Spark type. Explicit, so a column that happens to be all null still gets its type.
SCHEMAS: dict[str, dict[str, str]] = {
    "games": {
        "game_id": "STRING",
        "batch_id": "STRING",
        "schema_version": "INT",
        "seed": "BIGINT",
        "winner": "STRING",
        "end_reason": "STRING",
        "days": "INT",
        "n_events": "INT",
        "started_at": "STRING",
        "ended_at": "STRING",
        "seats": "ARRAY<STRUCT<seat: STRING, name: STRING, model: STRING, role: STRING>>",
    },
    "events": {
        "game_id": "STRING",
        "seq": "INT",
        "t": "INT",
        "day": "INT",
        "phase": "STRING",
        "type": "STRING",
        "visibility": "STRING",
        "actor": "STRING",
        "target": "STRING",
        "public_text": "STRING",
        "private_reasoning": "STRING",
        "suspicion": "ARRAY<STRUCT<player: STRING, p: DOUBLE>>",
        "statement_seq": "INT",
        "result": "STRING",
        "claimed_role": "STRING",
        "model": "STRING",
        "input_tokens": "BIGINT",
        "output_tokens": "BIGINT",
        "cost_usd": "DOUBLE",
        "latency_ms": "BIGINT",
        "attempts": "INT",
        "meta": "STRING",  # the whole meta object as JSON, for keys not split out
    },
    "labels": {
        "game_id": "STRING",
        "idx": "INT",
        "label": "STRING",
        "actor": "STRING",
        "source_seq": "INT",
        "source": "STRING",
        "confidence": "DOUBLE",
        "detail": "STRING",
    },
    "batch_metrics": {
        "batch_id": "STRING",
        "model": "STRING",  # "*" is every model together (T3.3's by_group)
        "grp": "STRING",
        "metric": "STRING",
        "value": "DOUBLE",
    },
}


def ddl(table: str) -> str:
    return ", ".join(f"{c} {t}" for c, t in SCHEMAS[table].items())


def find_logs(runs: Path) -> list[Path]:
    """Finished game logs under `runs`; one without game_end yet (still running) is skipped."""
    logs = sorted(p for p in runs.rglob("*.jsonl") if not p.name.endswith(".labels.jsonl"))
    return [p for p in logs if GAME_END.search(p.read_text(encoding="utf-8"))]


def headline(summary: dict) -> dict[str, float | None]:
    """The leaderboard numbers of one T3.3 summary (one model, one group), flat."""
    out = {
        "games": summary["games"],
        "win_rate": summary["win_rate"],
        "detection_auc": summary["detection"]["auc"],
        "detection_brier": summary["detection"]["brier"],
        "persuasion_share": summary["persuasion"]["share"],
        "invalid_output_rate": summary["invalid_output_rate"],
        "cost_usd_per_game": summary["cost_usd_per_game"],
    }
    for src, lies in summary["lies"].items():
        ls = lies["lie_success"]
        out |= {
            f"lie_rate_{src}": lies["lie_rate"],
            f"lie_share_moved_{src}": ls["share_moved"],
            f"lie_share_followed_{src}": ls["share_followed"],
            f"lie_base_rate_{src}": ls["base_rate"],
        }
    return out


def batch_metric_rows(games: list[tuple[GameData, str]]) -> list[dict]:
    """compute_metrics on each batch -> (batch_id, model, grp, metric, value) rows."""
    batches: dict[str, list[GameData]] = {}
    for g, batch_id in games:
        batches.setdefault(batch_id, []).append(g)
    rows = []
    for batch_id, gs in batches.items():
        m = compute_metrics(gs)
        for model, groups in [*m["models"].items(), ("*", m["by_group"])]:
            for grp, summary in groups.items():
                rows += [
                    {"batch_id": batch_id, "model": model, "grp": grp, "metric": k, "value": v}
                    for k, v in headline(summary).items()
                ]
    return rows


def build_rows(games: list[tuple[GameData, str]]) -> dict[str, list[dict]]:
    """games: (game, batch_id) pairs -> rows for each table, keys in SCHEMAS column order."""
    out: dict[str, list[dict]] = {t: [] for t in SCHEMAS}
    for g, batch_id in games:
        first, last = g.events[0], g.events[-1]
        end = next((e for e in reversed(g.events) if e.type is EventType.GAME_END), None)
        out["games"].append(
            {
                "game_id": g.game_id,
                "batch_id": batch_id,
                "schema_version": first.schema_version,
                "seed": first.meta.seed,
                "winner": g.winner,
                "end_reason": end.meta.reason if end else None,
                "days": last.day,
                "n_events": len(g.events),
                "started_at": first.meta.ts,
                "ended_at": last.meta.ts,
                "seats": [
                    {"seat": s, "name": x.name, "model": x.model, "role": x.role}
                    for s, x in (first.meta.seats or {}).items()
                ],
            }
        )
        for e in g.events:
            u = e.meta.usage
            out["events"].append(
                {
                    "game_id": e.game_id,
                    "seq": e.seq,
                    "t": e.t,
                    "day": e.day,
                    "phase": e.phase,
                    "type": e.type,
                    "visibility": e.visibility,
                    "actor": e.actor,
                    "target": e.target,
                    "public_text": e.public_text,
                    "private_reasoning": e.private_reasoning,
                    "suspicion": [s.model_dump() for s in e.suspicion]
                    if e.suspicion is not None
                    else None,
                    "statement_seq": e.meta.statement_seq,
                    "result": e.meta.result,
                    "claimed_role": e.meta.role,
                    "model": u.model if u else None,
                    "input_tokens": u.input_tokens if u else None,
                    "output_tokens": u.output_tokens if u else None,
                    "cost_usd": u.cost_usd if u else None,
                    "latency_ms": u.latency_ms if u else None,
                    "attempts": u.attempts if u else None,
                    "meta": e.meta.model_dump_json(),
                }
            )
        for i, lb in enumerate(g.labels):
            out["labels"].append(
                {
                    "game_id": lb.game_id,
                    "idx": i,
                    "label": lb.label,
                    "actor": lb.actor,
                    "source_seq": lb.source_seq,
                    "source": lb.source,
                    "confidence": lb.confidence,
                    "detail": lb.detail,
                }
            )
    out["batch_metrics"] = batch_metric_rows(games)
    return out


def write_local(games: list[tuple[GameData, str]], out: Path) -> list[Path]:
    """Write <table>.jsonl for each table plus metrics.json to `out`; return the paths."""
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for table, rows in build_rows(games).items():
        p = out / f"{table}.jsonl"
        p.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        paths.append(p)
    p = out / "metrics.json"
    metrics = compute_metrics([g for g, _ in games])
    p.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    return [*paths, p]


# --- Databricks REST ----------------------------------------------------------------------


def run_sql(client: httpx.Client, warehouse_id: str, statement: str, poll: float = 2) -> dict:
    """Run one statement on a SQL warehouse and wait for it; raise if it fails."""
    r = client.post(
        "/api/2.0/sql/statements/",
        json={
            "statement": statement,
            "warehouse_id": warehouse_id,
            "wait_timeout": "50s",
            "on_wait_timeout": "CONTINUE",
        },
    )
    r.raise_for_status()
    body = r.json()
    while body["status"]["state"] in ("PENDING", "RUNNING"):  # serverless cold start
        time.sleep(poll)
        r = client.get(f"/api/2.0/sql/statements/{body['statement_id']}")
        r.raise_for_status()
        body = r.json()
    if body["status"]["state"] != "SUCCEEDED":
        raise RuntimeError(f"{statement[:80]!r}: {body['status']}")
    return body


def first_warehouse(client: httpx.Client) -> str:
    r = client.get("/api/2.0/sql/warehouses")
    r.raise_for_status()
    warehouses = r.json().get("warehouses") or []
    if not warehouses:
        raise RuntimeError("no SQL warehouse in this workspace; set DATABRICKS_WAREHOUSE_ID")
    return warehouses[0]["id"]


def upload(
    client: httpx.Client,
    files: list[Path],
    catalog: str,
    schema: str,
    volume: str,
    warehouse_id: str | None = None,
    poll: float = 2,
) -> list[str]:
    """Create schema + volume, upload `files` into it, (re)create one Delta table per .jsonl."""
    wid = warehouse_id or first_warehouse(client)
    vol = f"/Volumes/{catalog}/{schema}/{volume}"
    statements = [
        f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}",
        f"CREATE VOLUME IF NOT EXISTS {catalog}.{schema}.{volume}",
    ]
    for s in statements:
        run_sql(client, wid, s, poll)
    for f in files:
        r = client.put(
            f"/api/2.0/fs/files{vol}/{f.name}",
            params={"overwrite": "true"},
            content=f.read_bytes(),
            headers={"Content-Type": "application/octet-stream"},
        )
        r.raise_for_status()
    for f in files:
        if f.suffix != ".jsonl":
            continue
        table = f.stem
        s = (
            f"CREATE OR REPLACE TABLE {catalog}.{schema}.{table} AS"
            f" SELECT {', '.join(SCHEMAS[table])} FROM read_files('{vol}/{f.name}',"
            f" format => 'json', schema => '{ddl(table)}')"
        )
        run_sql(client, wid, s, poll)
        statements.append(s)
    return statements


def dashboard_json(catalog: str, schema: str) -> str:
    """The checked-in dashboard, pointed at `catalog`.`schema`."""
    text = DASHBOARD.read_text(encoding="utf-8")
    json.loads(text)  # fail here, not in the workspace, on a broken file
    return text.replace("workspace.societysim.", f"{catalog}.{schema}.")


def find_dashboard(client: httpx.Client, name: str) -> str | None:
    params: dict = {"page_size": 100}
    while True:
        r = client.get("/api/2.0/lakeview/dashboards", params=params)
        r.raise_for_status()
        body = r.json()
        for d in body.get("dashboards") or []:
            if d.get("display_name") == name:
                return d["dashboard_id"]
        if not body.get("next_page_token"):
            return None
        params["page_token"] = body["next_page_token"]


def deploy_dashboard(
    client: httpx.Client, serialized: str, warehouse_id: str | None = None, name=DASHBOARD_NAME
) -> str:
    """Update the dashboard called `name` (or create it in the caller's home), publish it."""
    wid = warehouse_id or first_warehouse(client)
    payload = {"display_name": name, "warehouse_id": wid, "serialized_dashboard": serialized}
    if did := find_dashboard(client, name):
        r = client.patch(f"/api/2.0/lakeview/dashboards/{did}", json=payload)
    else:
        me = client.get("/api/2.0/preview/scim/v2/Me")
        me.raise_for_status()
        payload["parent_path"] = f"/Users/{me.json()['userName']}"
        r = client.post("/api/2.0/lakeview/dashboards", json=payload)
    r.raise_for_status()
    did = r.json()["dashboard_id"]
    r = client.post(
        f"/api/2.0/lakeview/dashboards/{did}/published",
        json={"warehouse_id": wid, "embed_credentials": True},
    )
    r.raise_for_status()
    return did


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--fixture", action="store_true", help="ingest fixtures/sample_game.jsonl")
    src.add_argument("--runs", type=Path, help="directory of game logs (searched recursively)")
    src.add_argument("--dashboard", action="store_true", help="import/update the AI/BI dashboard")
    ap.add_argument("--out", type=Path, default=ROOT / ".cache" / "databricks")
    ap.add_argument("--dry-run", action="store_true", help="write local files only")
    ap.add_argument("--catalog", default="workspace")
    ap.add_argument("--schema", default="societysim")
    ap.add_argument("--volume", default="raw")
    a = ap.parse_args(argv)

    if a.dashboard:
        serialized = dashboard_json(a.catalog, a.schema)
        n = len(json.loads(serialized)["datasets"])
        print(f"dashboard {DASHBOARD.name}: {n} datasets on {a.catalog}.{a.schema}")
    else:
        logs = [FIXTURE] if a.fixture else find_logs(a.runs)
        if not logs:
            print(f"no finished game logs under {a.runs}", file=sys.stderr)
            return 1
        games = [(load_game(p), p.parent.name) for p in logs]
        files = write_local(games, a.out)
        n = {f.stem: len(f.read_text().splitlines()) for f in files if f.suffix == ".jsonl"}
        print(f"wrote {n} rows to {a.out}")

    host, token = os.environ.get("DATABRICKS_HOST"), os.environ.get("DATABRICKS_TOKEN")
    if a.dry_run or not (host and token):
        why = "--dry-run" if a.dry_run else "DATABRICKS_HOST / DATABRICKS_TOKEN not set"
        print(f"dry run ({why}): nothing uploaded")
        return 0
    base = (host if host.startswith("http") else f"https://{host}").rstrip("/")
    headers = {"Authorization": f"Bearer {token}"}
    wid = os.environ.get("DATABRICKS_WAREHOUSE_ID")
    with httpx.Client(base_url=base, headers=headers, timeout=120) as client:
        if a.dashboard:
            did = deploy_dashboard(client, serialized, wid)
            print(f"published {base}/dashboardsv3/{did}/published")
            return 0
        upload(client, files, a.catalog, a.schema, a.volume, wid)
    print(f"created {', '.join(f'{a.catalog}.{a.schema}.{t}' for t in SCHEMAS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
