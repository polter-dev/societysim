# Databricks: Delta tables and the metrics notebook

`scripts/databricks_ingest.py` turns game logs into three Delta tables, `games`, `events` and `labels`, in `workspace.societysim`. `metrics.py` is a Databricks notebook that recomputes the T3.3 metrics (win rates, lies and lie success, detection, persuasion) in Spark SQL and checks every number against `metrics.json`, which the ingest computes with the same Python code as `uv run societysim report`.

## 1. Try it locally (no account)

```bash
uv run python scripts/databricks_ingest.py --fixture --dry-run
```

This writes `games.jsonl`, `events.jsonl`, `labels.jsonl` and `metrics.json` to `.cache/databricks/`. Use `--runs runs/<batch>` instead of `--fixture` for a real batch (label it first with `uv run societysim label runs/<batch>`). Without credentials in the environment, every run is a dry run.

## 2. Credentials (Free Edition)

1. Sign in at <https://www.databricks.com/learn/free-edition>. The workspace URL looks like `https://dbc-xxxxxxxx-xxxx.cloud.databricks.com`.
2. Avatar (top right) > Settings > Developer > Access tokens > Generate new token. Copy it.
3. Export both in your shell. Don't put them in git.

```bash
export DATABRICKS_HOST=https://dbc-xxxxxxxx-xxxx.cloud.databricks.com
export DATABRICKS_TOKEN=dapi...
# optional: otherwise the first SQL warehouse is used (Free Edition has one, "Serverless Starter Warehouse")
export DATABRICKS_WAREHOUSE_ID=...
```

## 3. Push the data

```bash
uv run python scripts/databricks_ingest.py --fixture              # or --runs runs/<batch>
```

It creates the schema `workspace.societysim` and the volume `workspace.societysim.raw` if they don't exist, uploads the four files to `/Volumes/workspace/societysim/raw/` and replaces the three tables with `CREATE OR REPLACE TABLE ... AS SELECT ... FROM read_files(...)`. The first statement can take a minute while the serverless warehouse starts. `--catalog`, `--schema` and `--volume` change the target; pass the same values to the notebook widgets.

Each run replaces the tables, so ingest everything you want in one call (`--runs runs/` picks up every batch under it).

## 4. Push and run the notebook

Import it through the API (or in the UI: Workspace > Import > File, pick `notebooks/databricks/metrics.py`):

```bash
curl -sf -X POST "$DATABRICKS_HOST/api/2.0/workspace/import" \
  -H "Authorization: Bearer $DATABRICKS_TOKEN" \
  -d "{\"path\": \"/Users/<your-email>/societysim_metrics\", \"format\": \"SOURCE\", \"language\": \"PYTHON\", \"overwrite\": true, \"content\": \"$(base64 < notebooks/databricks/metrics.py | tr -d '\n')\"}"
```

Open it, attach it to Serverless compute and click Run all. The last cell prints `N checks, 0 mismatches` and fails if any metric differs from `metrics.json`.
