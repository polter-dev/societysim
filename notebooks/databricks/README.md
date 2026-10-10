# Databricks: Delta tables, metrics notebook, AI/BI dashboard, MLflow

`scripts/databricks_ingest.py` turns game logs into four Delta tables in `workspace.societysim`: `games`, `events`, `labels`, and `batch_metrics` (the headline T3.3 numbers per batch, model and group, one row per metric).

- `metrics.py` is a Databricks notebook that recomputes the T3.3 metrics (win rates, lies and lie success, detection, persuasion) in Spark SQL and checks every number against `metrics.json`, which the ingest computes with the same Python code as `uv run societysim report`. Once the check passes it saves `metrics_win_rates`, `metrics_lies`, `metrics_detection` and `metrics_persuasion`.
- `societysim.lvdash.json` is an AI/BI (Lakeview) dashboard over those tables and `games`: win rate by model and role, lie success, detection, persuasion, games over time.
- `mlflow_batches.py` logs one MLflow run per batch: params (batch id, models, seeds, game count), metrics from `batch_metrics`, and the batch's metrics table as an artifact.

## 1. Try it locally (no account)

```bash
uv run python scripts/databricks_ingest.py --fixture --dry-run
uv run python scripts/databricks_ingest.py --dashboard --dry-run
```

The first writes `games.jsonl`, `events.jsonl`, `labels.jsonl`, `batch_metrics.jsonl` and `metrics.json` to `.cache/databricks/`. Use `--runs runs/<batch>` instead of `--fixture` for a real batch (label it first with `uv run societysim label runs/<batch>`). Logs without a `game_end` event are skipped, so a batch that is still running only contributes its finished games. The second only checks that the dashboard file loads. Without credentials in the environment, every run is a dry run.

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
uv run python scripts/databricks_ingest.py --runs runs/          # or --fixture
```

It creates the schema `workspace.societysim` and the volume `workspace.societysim.raw` if they don't exist, uploads the five files to `/Volumes/workspace/societysim/raw/` and replaces the four tables with `CREATE OR REPLACE TABLE ... AS SELECT ... FROM read_files(...)`. The first statement can take a minute while the serverless warehouse starts. `--catalog`, `--schema` and `--volume` change the target; pass the same values to the notebook widgets and to `--dashboard`.

Each run replaces the tables, so ingest everything you want in one call (`--runs runs/` picks up every batch under it). The batch id is the name of the directory a log sits in.

## 4. Import both notebooks

Through the API, or in the UI (Workspace > Import > File):

```bash
for nb in metrics mlflow_batches; do
  curl -sf -X POST "$DATABRICKS_HOST/api/2.0/workspace/import" \
    -H "Authorization: Bearer $DATABRICKS_TOKEN" \
    -d "{\"path\": \"/Users/<your-email>/societysim_$nb\", \"format\": \"SOURCE\", \"language\": \"PYTHON\", \"overwrite\": true, \"content\": \"$(base64 < notebooks/databricks/$nb.py | tr -d '\n')\"}"
done
```

## 5. Run the metrics notebook

Open `societysim_metrics`, attach it to Serverless compute and click Run all. The check cell prints `N checks, 0 mismatches` and fails if any metric differs from `metrics.json`. The last cell then saves the four `metrics_*` tables. Run it again after every ingest.

## 6. Import the dashboard

```bash
uv run python scripts/databricks_ingest.py --dashboard
```

It looks for a dashboard named `societysim`, updates it if it exists and creates it in your home folder if not, then publishes it and prints the URL. Run it again after editing `societysim.lvdash.json`. The data refreshes on its own: the datasets query the tables live, so a new ingest plus a metrics notebook run is enough.

If the API call fails, import the file by hand: Workspace > Import > File, pick `notebooks/databricks/societysim.lvdash.json`, then open it and click Publish.

## 7. Log the batches to MLflow

Open `societysim_mlflow_batches`, attach it to Serverless compute and click Run all. The first cell installs `mlflow-skinny` into the notebook. Runs go to the experiment `/Users/<you>/societysim` (the `experiment` widget changes it), one per batch, named after the batch id. Running it again replaces each batch's run, so params like the game count stay current.

Metric keys are `metric/model/group`, for example `win_rate/qwen3-235b/wolf` or `detection_auc/all_models/village`. Groups follow T3.3: `all`, `wolf`, `village` and each village role. Lie metrics carry their label source (`lie_rate_jev`, `lie_share_followed_self_tag`). Lie success and persuasion are exploratory.
