import ast
import importlib.util
import json
import re
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
    bm = rows.pop("batch_metrics")
    assert {t: len(r) for t, r in rows.items()} == {"games": 1, "events": 118, "labels": 8}
    assert bm and all(list(r) == list(ingest.SCHEMAS["batch_metrics"]) for r in bm)
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
    assert names == [
        "batch_metrics.jsonl",
        "events.jsonl",
        "games.jsonl",
        "labels.jsonl",
        "metrics.json",
    ]
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
    assert seen["files"] == [
        "games.jsonl",
        "events.jsonl",
        "labels.jsonl",
        "batch_metrics.jsonl",
        "metrics.json",
    ]
    assert seen["target"] == ("workspace", "ss", "raw", "wh1")


def test_runs_dir_finds_game_logs(tmp_path):
    batch = tmp_path / "b1"
    batch.mkdir()
    shutil.copy(FIXTURE, batch / "g.jsonl")
    shutil.copy(FIXTURE.with_name("sample_game.labels.jsonl"), batch / "g.labels.jsonl")
    (batch / "empty.jsonl").touch()
    # a game still being played: no game_end yet, last line half-written
    running = FIXTURE.read_text().splitlines()[:50]
    (batch / "running.jsonl").write_text("\n".join(running) + "\n" + running[-1][:40])
    assert ingest.find_logs(tmp_path) == [batch / "g.jsonl"]
    assert ingest.main(["--runs", str(tmp_path), "--dry-run", "--out", str(tmp_path / "o")]) == 0
    game = json.loads((tmp_path / "o" / "games.jsonl").read_text())
    assert game["batch_id"] == "b1"
    assert len((tmp_path / "o" / "labels.jsonl").read_text().splitlines()) == 8


def test_scripted_games_are_skipped_unless_included(tmp_path, capsys):
    batch = tmp_path / "b1"
    batch.mkdir()
    for name, tag in [("scripted", "scripted"), ("llm", "llm"), ("untagged", None)]:
        shutil.copy(FIXTURE, batch / f"{name}.jsonl")
        if tag:
            (batch / f"{name}.run_meta.json").write_text(json.dumps({"agents": tag}))
    (batch / "untagged.run_meta.json").write_text(json.dumps({"game_id": "x"}))
    names = lambda logs: sorted(p.stem for p in logs)  # noqa: E731
    assert names(ingest.find_logs(tmp_path)) == ["llm", "untagged"]
    assert "skipped 1 scripted" in capsys.readouterr().out
    assert names(ingest.find_logs(tmp_path, include_scripted=True)) == [
        "llm",
        "scripted",
        "untagged",
    ]


def test_all_scripted_runs_dir_exits_one(tmp_path, capsys):
    shutil.copy(FIXTURE, tmp_path / "g.jsonl")
    (tmp_path / "g.run_meta.json").write_text(json.dumps({"agents": "scripted"}))
    args = ["--runs", str(tmp_path), "--dry-run", "--out", str(tmp_path / "o")]
    assert ingest.main(args) == 1
    assert "scripted" in capsys.readouterr().err
    assert ingest.main([*args, "--include-scripted"]) == 0


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
    tables = ("games", "events", "labels", "batch_metrics")
    for table, stmt in zip(tables, statements[2:], strict=True):
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
    assert sum(r.url.path.endswith("/s1") for r in log) == 6
    first_table = next(i for i, r in enumerate(log) if b"CREATE OR REPLACE" in r.content)
    assert max(i for i, r in enumerate(log) if r.method == "PUT") < first_table


def test_failed_statement_raises(tmp_path):
    files = ingest.write_local([(load_game(FIXTURE), "fixtures")], tmp_path)
    client, _ = mock_databricks(fail="CREATE VOLUME")
    with pytest.raises(RuntimeError, match="CREATE VOLUME"):
        ingest.upload(client, files, "workspace", "societysim", "raw", warehouse_id="wh", poll=0)


def test_batch_metrics_per_batch():
    g = load_game(FIXTURE)
    rows = ingest.batch_metric_rows([(g, "b1"), (g, "b2"), (g, "b2")])
    by = {(r["batch_id"], r["model"], r["grp"], r["metric"]): r["value"] for r in rows}
    one, two = compute_metrics([g]), compute_metrics([g, g])
    assert by["b1", "gemma-4-26b", "wolf", "games"] == 1
    assert by["b2", "gemma-4-26b", "wolf", "games"] == 2
    village = two["by_group"]["village"]["detection"]["auc"]
    assert by["b2", "*", "village", "detection_auc"] == village
    assert by["b1", "deepseek-v3.1", "all", "persuasion_share"] == 0.4375
    wolf = one["models"]["gemma-4-26b"]["wolf"]
    assert by["b1", "gemma-4-26b", "wolf", "win_rate"] == wolf["win_rate"] == 1.0
    assert by["b1", "gemma-4-26b", "wolf", "lie_rate_jev"] == wolf["lies"]["jev"]["lie_rate"]
    assert (
        by["b1", "gemma-4-26b", "wolf", "lie_share_followed_self_tag"]
        == (wolf["lies"]["self_tag"]["lie_success"]["share_followed"])
    )
    n_summaries = sum(len(gs) for gs in one["models"].values()) + len(one["by_group"])
    assert sum(r["batch_id"] == "b1" for r in rows) == n_summaries * 15


# --- dashboard -----------------------------------------------------------------------------

SQL_WORDS = {
    "select", "from", "where", "and", "or", "not", "as", "group", "by", "all", "order",
    "count", "date_trunc", "to_timestamp",
}  # fmt: skip
SCHEMA_PREFIX = "workspace.societysim."


def known_columns() -> dict[str, list[str]]:
    """#127's tables plus the metric tables metrics.py saves (its METRIC_COLUMNS literal)."""
    tree = ast.parse((ROOT / "notebooks" / "databricks" / "metrics.py").read_text())
    metric = next(
        ast.literal_eval(n.value)
        for n in tree.body
        if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", None) == "METRIC_COLUMNS"
    )
    return {**{t: list(c) for t, c in ingest.SCHEMAS.items()}, **metric}


def test_dashboard_queries_use_known_tables_and_columns():
    dash = json.loads(ingest.DASHBOARD.read_text())
    tables = known_columns()
    outputs = {}
    for ds in dash["datasets"]:
        sql = "".join(ds["queryLines"])
        refs = re.findall(r"\b(?:FROM|JOIN)\s+([\w.]+)", sql, re.I)
        assert refs, ds["name"]
        cols = set()
        for ref in refs:
            table = ref.removeprefix(SCHEMA_PREFIX)
            assert ref.startswith(SCHEMA_PREFIX) and table in tables, (ds["name"], ref)
            cols |= set(tables[table])
        body = re.sub(r"'[^']*'", "''", re.sub(r"\bworkspace\.societysim\.\w+", "", sql))
        aliases = set(re.findall(r"\bAS\s+(\w+)", body, re.I))
        words = set(re.findall(r"[A-Za-z_]\w*", body))
        unknown = {w for w in words if w.lower() not in SQL_WORDS} - cols - aliases
        assert not unknown, (ds["name"], unknown)
        outputs[ds["name"]] = cols | aliases

    widgets = [w["widget"] for p in dash["pages"] for w in p["layout"]]
    assert len({w["name"] for w in widgets}) == len(widgets)
    charted = set()
    for w in widgets:
        for q in w.get("queries", []):
            ds = q["query"]["datasetName"]
            charted.add(ds)
            names = {f["name"] for f in q["query"]["fields"]}
            for f in q["query"]["fields"]:
                assert f["expression"].strip("`") in outputs[ds], (w["name"], f)
            enc = w["spec"]["encodings"]
            used = [e["fieldName"] for e in enc.get("columns", [])]
            used += [e["fieldName"] for k, e in enc.items() if k != "columns"]
            assert set(used) <= names, w["name"]
    assert charted == set(outputs)
    for p in dash["pages"]:
        for item in p["layout"]:
            pos = item["position"]
            assert pos["x"] >= 0 and pos["x"] + pos["width"] <= 6, item["widget"]["name"]


def test_dashboard_dry_run_without_credentials(monkeypatch, capsys):
    monkeypatch.delenv("DATABRICKS_HOST", raising=False)
    monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)
    monkeypatch.setattr(ingest, "deploy_dashboard", lambda *a, **k: pytest.fail("deployed"))
    assert ingest.main(["--dashboard"]) == 0
    assert "nothing uploaded" in capsys.readouterr().out


def test_dashboard_uses_credentials_and_catalog(monkeypatch, capsys):
    monkeypatch.setenv("DATABRICKS_HOST", "https://example.cloud.databricks.com/")
    monkeypatch.setenv("DATABRICKS_TOKEN", "dapi-test")
    monkeypatch.delenv("DATABRICKS_WAREHOUSE_ID", raising=False)
    seen = {}

    def fake_deploy(client, serialized, warehouse_id):
        seen.update(auth=client.headers["Authorization"], dash=serialized, wid=warehouse_id)
        return "d9"

    monkeypatch.setattr(ingest, "deploy_dashboard", fake_deploy)
    assert ingest.main(["--dashboard", "--catalog", "main", "--schema", "ss"]) == 0
    assert seen["auth"] == "Bearer dapi-test" and seen["wid"] is None
    assert "main.ss.metrics_win_rates" in seen["dash"] and SCHEMA_PREFIX not in seen["dash"]
    out = capsys.readouterr().out
    assert "https://example.cloud.databricks.com/dashboardsv3/d9/published" in out


def mock_lakeview(existing: bool):
    log: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        log.append(req)
        path, method = req.url.path, req.method
        if path == "/api/2.0/sql/warehouses":
            return httpx.Response(200, json={"warehouses": [{"id": "wh-first"}]})
        if path == "/api/2.0/lakeview/dashboards" and method == "GET":
            if "page_token" not in req.url.params:  # page 1: someone else's dashboard
                other = {"dashboard_id": "d0", "display_name": "other"}
                return httpx.Response(200, json={"dashboards": [other], "next_page_token": "t2"})
            mine = [{"dashboard_id": "d1", "display_name": "societysim"}] if existing else []
            return httpx.Response(200, json={"dashboards": mine})
        if path == "/api/2.0/lakeview/dashboards/d1" and method == "PATCH":
            return httpx.Response(200, json={"dashboard_id": "d1"})
        if path == "/api/2.0/preview/scim/v2/Me":
            return httpx.Response(200, json={"userName": "me@example.com"})
        if path == "/api/2.0/lakeview/dashboards" and method == "POST":
            return httpx.Response(200, json={"dashboard_id": "d2"})
        if path.endswith("/published") and method == "POST":
            return httpx.Response(200, json={})
        return httpx.Response(404)

    client = httpx.Client(base_url="https://dbx.test", transport=httpx.MockTransport(handler))
    return client, log


def test_deploy_dashboard_updates_existing():
    client, log = mock_lakeview(existing=True)
    assert ingest.deploy_dashboard(client, '{"x": 1}') == "d1"
    calls = [(r.method, r.url.path) for r in log]
    assert ("POST", "/api/2.0/lakeview/dashboards") not in calls
    patch = json.loads(next(r for r in log if r.method == "PATCH").content)
    assert patch == {
        "display_name": "societysim",
        "warehouse_id": "wh-first",
        "serialized_dashboard": '{"x": 1}',
    }
    assert calls[-1] == ("POST", "/api/2.0/lakeview/dashboards/d1/published")
    assert json.loads(log[-1].content) == {"warehouse_id": "wh-first", "embed_credentials": True}


def test_deploy_dashboard_creates_in_home_folder():
    client, log = mock_lakeview(existing=False)
    assert ingest.deploy_dashboard(client, "{}", warehouse_id="wh") == "d2"
    post = next(r for r in log if r.method == "POST" and r.url.path.endswith("/dashboards"))
    body = json.loads(post.content)
    assert body["parent_path"] == "/Users/me@example.com" and body["warehouse_id"] == "wh"
    assert not any(r.method == "PATCH" for r in log)
    assert not any(r.url.path == "/api/2.0/sql/warehouses" for r in log)
    assert log[-1].url.path == "/api/2.0/lakeview/dashboards/d2/published"


# --- --notebooks ---


def _jobs_client(states, calls, output=None):
    """Mock workspace: runs/get walks `states` (one list per run, in submit order)."""
    polls = {"n": 0, "run": 0}

    def handler(req):
        path = req.url.path
        calls.append((req.method, path, json.loads(req.content) if req.content else None))
        if path.endswith("/Me"):
            return httpx.Response(200, json={"userName": "me@x.com"})
        if path.endswith("/workspace/import"):
            return httpx.Response(200, json={})
        if path.endswith("/runs/submit"):
            polls["run"] += 1
            polls["n"] = 0
            return httpx.Response(200, json={"run_id": 100 + polls["run"]})
        if path.endswith("/runs/get-output"):
            return httpx.Response(200, json=output or {})
        seq = states[polls["run"] - 1]
        st = seq[min(polls["n"], len(seq) - 1)]
        polls["n"] += 1
        return httpx.Response(
            200,
            json={"state": st, "run_page_url": "https://dbx.test/run/1", "tasks": [{"run_id": 7}]},
        )

    return httpx.Client(base_url="https://dbx.test", transport=httpx.MockTransport(handler))


OK = [
    {"life_cycle_state": "PENDING"},
    {"life_cycle_state": "RUNNING"},
    {"life_cycle_state": "TERMINATED", "result_state": "SUCCESS"},
]


def test_run_notebooks_imports_submits_in_order_and_polls(monkeypatch, capsys):
    monkeypatch.setattr(ingest.time, "sleep", lambda s: None)
    calls = []
    assert ingest.run_notebooks(_jobs_client([OK, OK], calls), poll=0)
    imports = [c[2] for c in calls if c[1].endswith("/workspace/import")]
    assert [i["path"] for i in imports] == [
        "/Users/me@x.com/societysim_metrics",
        "/Users/me@x.com/societysim_mlflow_batches",
    ]
    assert all(i["overwrite"] and i["format"] == "SOURCE" for i in imports)
    submits = [c[2] for c in calls if c[1].endswith("/runs/submit")]
    assert submits[0] == {
        "run_name": "societysim_metrics",
        "tasks": [
            {
                "task_key": "metrics",
                "notebook_task": {"notebook_path": "/Users/me@x.com/societysim_metrics"},
            }
        ],
    }
    assert "cluster" not in json.dumps(submits)
    assert len([c for c in calls if c[1].endswith("/runs/get")]) == 6  # 3 polls each
    out = capsys.readouterr().out
    assert "metrics: SUCCESS https://dbx.test/run/1" in out
    assert "mlflow_batches: SUCCESS" in out


def test_failed_notebook_prints_error_and_stops(monkeypatch, capsys):
    monkeypatch.setattr(ingest.time, "sleep", lambda s: None)
    calls = []
    bad = [{"life_cycle_state": "TERMINATED", "result_state": "FAILED"}]
    client = _jobs_client([bad, OK], calls, output={"error": "AssertionError: 3 mismatches"})
    assert not ingest.run_notebooks(client, poll=0)
    assert "metrics failed: AssertionError: 3 mismatches" in capsys.readouterr().out
    assert len([c for c in calls if c[1].endswith("/runs/submit")]) == 1


def test_main_exits_one_when_notebook_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABRICKS_HOST", "https://dbx.test/?o=123")
    monkeypatch.setenv("DATABRICKS_TOKEN", "dapi-test")
    monkeypatch.setattr(ingest, "upload", lambda *a, **k: None)
    monkeypatch.setattr(ingest, "run_notebooks", lambda client: False)
    assert ingest.main(["--fixture", "--notebooks", "--out", str(tmp_path)]) == 1


def test_notebooks_dry_run_prints_calls(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ingest, "run_notebooks", lambda *a, **k: pytest.fail("sent"))
    assert ingest.main(["--fixture", "--notebooks", "--dry-run", "--out", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "POST /api/2.1/jobs/runs/submit" in out
    assert "societysim_mlflow_batches" in out


def test_normalize_host():
    for h in ("dbc-1.cloud.databricks.com", "https://dbc-1.cloud.databricks.com/?o=99"):
        assert ingest.normalize_host(h) == "https://dbc-1.cloud.databricks.com"
