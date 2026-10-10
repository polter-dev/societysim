# Databricks: Delta tables, metrics notebook, AI/BI dashboard, MLflow

`scripts/databricks_ingest.py` turns game logs into five Delta tables in `workspace.societysim`: `games`, `events`, `labels`, `batch_metrics` (the headline T3.3 numbers per batch, model and group, one row per metric) and `deception_pairs` (who fools whom, one row per batch, label source, liar model and listener model).

- `metrics.py` is a Databricks notebook that recomputes the T3.3 metrics (win rates, lies and lie success, detection, persuasion) in Spark SQL and checks every number against `metrics.json`, which the ingest computes with the same Python code as `uv run societysim report`. Once the check passes it saves `metrics_win_rates`, `metrics_lies`, `metrics_detection` and `metrics_persuasion`. A "Who fools whom" section then adds up `deception_pairs` over batches and saves it as `metrics_deception`.
- `societysim.lvdash.json` is an AI/BI (Lakeview) dashboard over those tables and `games`: win rate by model and role, lie success, detection, persuasion, games over time, and the two deception tiles below.

The deception tiles answer the research question: which model's lies work on which other model. The heatmap has the liar model down the side and the listener model across the top. A darker cell means that listener voted the way that liar's lies pointed more often, and hovering shows how many votes the cell rests on. "Top fooling pairs" lists the pairs with the highest rate, but only those with at least 5 votes, since a single vote would otherwise put a pair at 100%. The truthful baseline column is how often the same listener followed that liar's kind of claim when it was true. Both are exploratory and use the model's own lie tags (`self_tag`); the table also holds the `jev` source.
- `mlflow_batches.py` logs one MLflow run per batch: params (batch id, models, seeds, game count), metrics from `batch_metrics`, and the batch's metrics table as an artifact.

## 1. Try it locally (no account)

```bash
uv run python scripts/databricks_ingest.py --fixture --dry-run
uv run python scripts/databricks_ingest.py --dashboard --dry-run
```

The first writes `games.jsonl`, `events.jsonl`, `labels.jsonl`, `batch_metrics.jsonl`, `deception_pairs.jsonl` and `metrics.json` to `.cache/databricks/`. Use `--runs runs/<batch>` instead of `--fixture` for a real batch (label it first with `uv run societysim label runs/<batch>`; run `societysim report` too if you want the deception rows read from `report/metrics.json`, otherwise they are computed from the games). Logs without a `game_end` event are skipped, so a batch that is still running only contributes its finished games. The second only checks that the dashboard file loads. Without credentials in the environment, every run is a dry run.

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

It creates the schema `workspace.societysim` and the volume `workspace.societysim.raw` if they don't exist, uploads the six files to `/Volumes/workspace/societysim/raw/` and replaces the five tables with `CREATE OR REPLACE TABLE ... AS SELECT ... FROM read_files(...)`. The first statement can take a minute while the serverless warehouse starts. `--catalog`, `--schema` and `--volume` change the target; pass the same values to the notebook widgets and to `--dashboard`.

Each run replaces the tables, so ingest everything you want in one call (`--runs runs/` picks up every batch under it). The batch id is the name of the directory a log sits in.

## 4. Import and run both notebooks

One command ingests the games, imports `metrics.py` and `mlflow_batches.py` to `/Users/<you>/societysim_<name>` (overwriting), runs each as a serverless one-time run in that order, and prints each run's state and page URL:

```bash
uv run python scripts/databricks_ingest.py --runs runs/ --notebooks
```

`DATABRICKS_HOST` can be the bare host; a `?o=` suffix is dropped. A failed notebook prints its error message and the command exits 1. `--dry-run` prints the API calls without sending them. The check cell in `metrics` prints `N checks, 0 mismatches` and fails if any metric differs from `metrics.json`; its last cell saves the five `metrics_*` tables. `mlflow_batches` logs one run per batch to the experiment `/Users/<you>/societysim`.

To do it by hand instead, import the files in the UI (Workspace > Import > File), attach each to Serverless compute and click Run all.

## 5. Re-run later

Run the same command after every ingest. The notebooks are overwritten and run again, and each batch's MLflow run is replaced so params like the game count stay current.

## 6. Import the dashboard

```bash
uv run python scripts/databricks_ingest.py --dashboard
```

It looks for a dashboard named `societysim`, updates it if it exists and creates it in your home folder if not, then publishes it and prints the URL. Run it again after editing `societysim.lvdash.json`. The data refreshes on its own: the datasets query the tables live, so a new ingest plus a metrics notebook run is enough.

If the API call fails, import the file by hand: Workspace > Import > File, pick `notebooks/databricks/societysim.lvdash.json`, then open it and click Publish.

## 7. About the MLflow notebook

It uses the MLflow that comes with serverless; nothing is installed. Runs go to the experiment `/Users/<you>/societysim` (the `experiment` widget changes it), one per batch, named after the batch id.

Metric keys are `metric/model/group`, for example `win_rate/qwen3-235b/wolf` or `detection_auc/all_models/village`. Groups follow T3.3: `all`, `wolf`, `village` and each village role. Lie metrics carry their label source (`lie_rate_jev`, `lie_share_followed_self_tag`). Lie success and persuasion are exploratory.

Serverless gotcha: serverless compute runs on Spark Connect, which refuses to read `spark.mlflow.modelRegistryUri` (`CONFIG_NOT_AVAILABLE`). MLflow reads that conf the first time it builds a client, unless a registry URI is already set. So the notebook calls `mlflow.set_tracking_uri("databricks")` and `mlflow.set_registry_uri("databricks-uc")` before anything else, even though it never uses the model registry. Keep those two lines first if you add MLflow calls, and don't `%pip install` a newer MLflow over the preinstalled one.
