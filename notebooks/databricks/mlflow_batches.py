# Databricks notebook source
# MAGIC %md
# MAGIC # societysim batches in MLflow
# MAGIC
# MAGIC One MLflow run per batch in `games`:
# MAGIC - params: batch id, models, seeds, game count
# MAGIC - metrics: win rates and the T3.3 headline metrics (detection, lie rate, lie success,
# MAGIC   persuasion, invalid output, cost) per model and group, keyed `metric/model/group`.
# MAGIC   `all_models` is every model together. They come from `batch_metrics`, which
# MAGIC   `scripts/databricks_ingest.py` computes per batch with the T3.3 Python code
# MAGIC - artifact: the batch's metrics table, one row per model and group
# MAGIC
# MAGIC Re-running replaces each batch's run, so a batch that grew gets fresh params.

# COMMAND ----------

# ruff: noqa: F821  (spark and dbutils are notebook globals)
from collections import defaultdict

import mlflow
from pyspark.sql import functions as F

# Set both URIs before any MLflow call. The first MlflowClient() (inside search_runs below)
# resolves the registry URI, and with none set MLflow falls back to
# spark.conf.get("spark.mlflow.modelRegistryUri"), which serverless Spark Connect refuses
# to read (CONFIG_NOT_AVAILABLE). An explicit registry URI short-circuits that lookup.
# We never touch the registry; this only keeps MLflow from looking for one.
mlflow.set_tracking_uri("databricks")
mlflow.set_registry_uri("databricks-uc")

dbutils.widgets.text("catalog", "workspace")
dbutils.widgets.text("schema", "societysim")
dbutils.widgets.text("experiment", "")
CATALOG = dbutils.widgets.get("catalog")
SCHEMA = dbutils.widgets.get("schema")
user = spark.sql("SELECT current_user()").first()[0]
EXPERIMENT = dbutils.widgets.get("experiment") or f"/Users/{user}/societysim"
experiment_id = mlflow.set_experiment(EXPERIMENT).experiment_id

# COMMAND ----------

batches = spark.sql(f"""
SELECT g.batch_id, count(DISTINCT g.game_id) AS games,
       array_join(array_sort(collect_set(s.model)), ',') AS models,
       array_join(transform(array_sort(collect_set(g.seed)), x -> CAST(x AS STRING)), ',') AS seeds
FROM {CATALOG}.{SCHEMA}.games g LATERAL VIEW explode(g.seats) t AS s
GROUP BY g.batch_id ORDER BY g.batch_id
""").collect()

bm = spark.table(f"{CATALOG}.{SCHEMA}.batch_metrics")
metrics = defaultdict(dict)
for r in bm.where("value IS NOT NULL").collect():
    model = "all_models" if r.model == "*" else r.model
    metrics[r.batch_id][f"{r.metric}/{model}/{r.grp}"] = r.value

# COMMAND ----------

for b in batches:
    old = mlflow.search_runs(
        [experiment_id], filter_string=f"tags.batch_id = '{b.batch_id}'", output_format="list"
    )
    for run in old:
        mlflow.delete_run(run.info.run_id)
    with mlflow.start_run(run_name=b.batch_id):
        mlflow.set_tag("batch_id", b.batch_id)
        mlflow.log_params(
            # ponytail: seeds cut at 500 chars (MLflow's old param limit); the games table has all
            {"batch_id": b.batch_id, "models": b.models, "seeds": b.seeds[:500], "games": b.games}
        )
        mlflow.log_metrics(metrics[b.batch_id])
        table = (
            bm.where(F.col("batch_id") == b.batch_id)
            .groupBy("model", "grp")
            .pivot("metric")
            .agg(F.first("value"))
            .orderBy("model", "grp")
        )
        mlflow.log_table(table.toPandas(), "batch_metrics.json")
    print(f"{b.batch_id}: {b.games} games, {len(metrics[b.batch_id])} metrics")
print(f"{len(batches)} runs in {EXPERIMENT}")
