#!/usr/bin/env bash
# Label, report and highlight a batch fetched from the HF Dataset (deploy.yml). No paid calls.
#
#   bash scripts/label_fetched_batch.sh BATCH_DIR [JEV_CACHE_ZIP]
#
# The dataset has logs and labels but not the Jev cache, and report takes the Jev lie rate's
# denominator (the statements Jev read) from BATCH_DIR/.cache/jev (#629). Without it the rate
# is null and there is no agreement.json (#645).
#
# With JEV_CACHE_ZIP (the jev_cache.zip asset of the jev-cache-<batch_id> release, holding
# .cache/jev/<game_id>/<seq>.json), restore the cache and relabel every game from it with
# --no-jev: keyless, zero calls. That rebuilds the published labels, writes agreement.json and
# gives report its denominator, so the site matches the local postbatch.
# Without it, label only the games that have no labels file and warn that Jev numbers are null.
set -euo pipefail

batch=${1:?usage: label_fetched_batch.sh BATCH_DIR [JEV_CACHE_ZIP]}
zip=${2:-}
root=$(cd "$(dirname "$0")/.." && pwd)
run() { uv run --frozen --project "$root" "$@"; }

if [[ -n "$zip" ]]; then
  # zipfile drops ".." and absolute paths on extract; python, since unzip may be missing.
  run python -m zipfile -e "$zip" "$batch"
  run societysim label "$batch" --no-jev
else
  echo "::warning::no Jev cache for $(basename "$batch"): the Jev lie rate stays null and there is no agreement.json"
  tmp=$(mktemp -d)
  trap 'rm -rf "$tmp"' EXIT
  for log in "$batch"/*.jsonl; do
    case "$log" in *.labels.jsonl) continue ;; esac
    [[ -f "${log%.jsonl}.labels.jsonl" ]] || cp "$log" "$tmp/"
  done
  if ls "$tmp"/*.jsonl >/dev/null 2>&1; then
    run societysim label "$tmp" --no-jev
    cp "$tmp"/*.labels.jsonl "$batch/"
  fi
fi
run societysim report "$batch"
run societysim highlights "$batch"
