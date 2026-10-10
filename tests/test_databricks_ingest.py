import importlib.util
import json
import shutil
from pathlib import Path

import httpx
import pytest

from societysim.analysis.metrics import compute_metrics, load_game

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "fixtures" / "sample_game.jsonl"

spec = importlib.util.spec_from_file_location(
    "databricks_ingest", ROOT / "scripts" / "databricks_ingest.py"
)
ingest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ingest)


def test_rows_from_fixture():
    rows = ingest.build_rows([(load_game(FIXTURE), "fixtures")])
    assert {t: len(r) for t, r in rows.items()} == {"games": 1, "events": 118, "labels": 8}
    for table, rs in rows.items():
        for r in rs:
            assert list(r) == list(ingest.SCHEMAS[table]), table

    game = rows["games"][0]
    assert game["game_id"] == "fixture-0001"
    assert (game["winner"], game["seed"], game["days"], game["n_events"]) == ("wolves", 42, 3, 118)
    assert game["seats"][2] == {
        "seat": "p2",
        "name": "Cleo",
        "model": "gemma-4-26b",
        "role": "wolf",
    }

    ev = {e["seq"]: e for e in rows["events"]}
    assert ev[24]["type"] == "accusation" and ev[24]["statement_seq"] == 23
    assert ev[31]["result"] == "wolf" and ev[31]["target"] == "p2"
    assert ev[2]["model"] == "gemma-4-26b" and ev[2]["input_tokens"] == 2430
    assert ev[2]["suspicion"][0] == {"player": "p0", "p": 0.05}
    assert ev[0]["suspicion"] is None and ev[0]["model"] is None
    assert json.loads(ev[0]["meta"])["seed"] == 42

    assert [lb["idx"] for lb in rows["labels"]] == list(range(8))
    assert rows["labels"][3]["label"] == "bussing"


def test_dry_run_without_credentials(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("DATABRICKS_HOST", raising=False)
    monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)
    monkeypatch.setattr(ingest, "upload", lambda *a, **k: pytest.fail("uploaded"))
    assert ingest.main(["--fixture", "--out", str(tmp_path)]) == 0
    assert "nothing uploaded" in capsys.readouterr().out

    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["events.jsonl", "games.jsonl", "labels.jsonl", "metrics.json"]
    events = [json.loads(x) for x in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert len(events) == 118 and events[5]["seq"] == 5
    want = json.loads(json.dumps(compute_metrics([load_game(FIXTURE)])))
    assert json.loads((tmp_path / "metrics.json").read_text()) == want


def test_dry_run_flag_wins_over_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABRICKS_HOST", "example.cloud.databricks.com")
    monkeypatch.setenv("DATABRICKS_TOKEN", "dapi-test")
    monkeypatch.setattr(ingest, "upload", lambda *a, **k: pytest.fail("uploaded"))
    assert ingest.main(["--fixture", "--dry-run", "--out", str(tmp_path)]) == 0


def test_credentials_trigger_upload(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABRICKS_HOST", "example.cloud.databricks.com")
    monkeypatch.setenv("DATABRICKS_TOKEN", "dapi-test")
    monkeypatch.setenv("DATABRICKS_WAREHOUSE_ID", "wh1")
    seen = {}

    def fake_upload(client, files, catalog, schema, volume, warehouse_id):
        seen.update(
            base=str(client.base_url),
            auth=client.headers["Authorization"],
            files=[f.name for f in files],
            target=(catalog, schema, volume, warehouse_id),
        )

    monkeypatch.setattr(ingest, "upload", fake_upload)
    assert ingest.main(["--fixture", "--out", str(tmp_path), "--schema", "ss"]) == 0
    assert seen["base"] == "https://example.cloud.databricks.com"
    assert seen["auth"] == "Bearer dapi-test"
    assert seen["files"] == ["games.jsonl", "events.jsonl", "labels.jsonl", "metrics.json"]
    assert seen["target"] == ("workspace", "ss", "raw", "wh1")


def test_runs_dir_finds_game_logs(tmp_path):
    batch = tmp_path / "b1"
    batch.mkdir()
    shutil.copy(FIXTURE, batch / "g.jsonl")
    shutil.copy(FIXTURE.with_name("sample_game.labels.jsonl"), batch / "g.labels.jsonl")
    (batch / "empty.jsonl").touch()
    assert ingest.find_logs(tmp_path) == [batch / "g.jsonl"]
    assert ingest.main(["--runs", str(tmp_path), "--dry-run", "--out", str(tmp_path / "o")]) == 0
    game = json.loads((tmp_path / "o" / "games.jsonl").read_text())
    assert game["batch_id"] == "b1"
    assert len((tmp_path / "o" / "labels.jsonl").read_text().splitlines()) == 8


def mock_databricks(fail: str | None = None):
    """A fake workspace: one warehouse; statements go PENDING once, then SUCCEEDED."""
    log: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        log.append(req)
        path = req.url.path
        if path == "/api/2.0/sql/warehouses":
            return httpx.Response(200, json={"warehouses": [{"id": "wh-first"}]})
        if path == "/api/2.0/sql/statements/":
            stmt = json.loads(req.content)["statement"]
            state = "FAILED" if fail and fail in stmt else "PENDING"
            return httpx.Response(200, json={"statement_id": "s1", "status": {"state": state}})
        if path == "/api/2.0/sql/statements/s1":
            return httpx.Response(
                200, json={"statement_id": "s1", "status": {"state": "SUCCEEDED"}}
            )
        if path.startswith("/api/2.0/fs/files/Volumes/") and req.method == "PUT":
            return httpx.Response(204)
        return httpx.Response(404)

    client = httpx.Client(base_url="https://dbx.test", transport=httpx.MockTransport(handler))
    return client, log


def test_upload_creates_volume_uploads_and_builds_tables(tmp_path):
    files = ingest.write_local([(load_game(FIXTURE), "fixtures")], tmp_path)
    client, log = mock_databricks()
    statements = ingest.upload(client, files, "workspace", "societysim", "raw", poll=0)

    posted = [json.loads(r.content) for r in log if r.url.path == "/api/2.0/sql/statements/"]
    assert {p["warehouse_id"] for p in posted} == {"wh-first"}
    assert [p["statement"] for p in posted] == statements
    assert statements[:2] == [
        "CREATE SCHEMA IF NOT EXISTS workspace.societysim",
        "CREATE VOLUME IF NOT EXISTS workspace.societysim.raw",
    ]
    for table, stmt in zip(("games", "events", "labels"), statements[2:], strict=True):
        assert stmt.startswith(f"CREATE OR REPLACE TABLE workspace.societysim.{table} AS")
        assert f"read_files('/Volumes/workspace/societysim/raw/{table}.jsonl'" in stmt
        assert ingest.ddl(table) in stmt

    puts = [r for r in log if r.method == "PUT"]
    assert [r.url.path for r in puts] == [
        f"/api/2.0/fs/files/Volumes/workspace/societysim/raw/{f.name}" for f in files
    ]
    assert all(r.url.params["overwrite"] == "true" for r in puts)
    assert puts[1].content == (tmp_path / "events.jsonl").read_bytes()
    # every statement polled once after PENDING; uploads happen before the tables are built
    assert sum(r.url.path.endswith("/s1") for r in log) == 5
    first_table = next(i for i, r in enumerate(log) if b"CREATE OR REPLACE" in r.content)
    assert max(i for i, r in enumerate(log) if r.method == "PUT") < first_table


def test_failed_statement_raises(tmp_path):
    files = ingest.write_local([(load_game(FIXTURE), "fixtures")], tmp_path)
    client, _ = mock_databricks(fail="CREATE VOLUME")
    with pytest.raises(RuntimeError, match="CREATE VOLUME"):
        ingest.upload(client, files, "workspace", "societysim", "raw", warehouse_id="wh", poll=0)
