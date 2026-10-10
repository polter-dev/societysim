#!/usr/bin/env bash
# Run societysim games inside the AMD pod, against the server serve.sh started on this box, then
# pack runs/<batch> into a tarball to download. AUP pods block Cloudflare tunnels, so this is
# the supported AMD path.
#
#   bash infra/amd/run_on_box.sh <config.yaml> [n_games]
#
# Game config (configs/selfplay_amd.yaml): plays n_games games (default 1) with seeds SEED,
#   SEED+1, ... into runs/<config name>.
# Batch config (has batch_id:, e.g. configs/mixed_v1.yaml): runs `societysim batch`, which
#   takes the game count from the config and resumes a half-done batch. n_games is ignored.
# Before any game, `societysim doctor` checks every model in the config; a failure stops here.
#
# Keys: AMD_API_KEY and any provider keys (OPENAI_API_KEY, GEMINI_API_KEY, HF_TOKEN) come from
# the environment or the repo's .env; an exported value wins over .env. Never printed here.
#
# Settings: AMD_VLLM_BASE_URL (default http://127.0.0.1:8000/v1), SEED (default 0),
#   PUBLISH=1 to also run `societysim publish runs/<batch>` after packing. publish takes the
#   dataset repo from SOCIETYSIM_HF_DATASET (env or .env) and needs HF_TOKEN with write access.

set -Eeuo pipefail
die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

[ -f "${1:-}" ] || die "usage: bash $0 <config.yaml> [n_games]"
n=${2:-1}
seed=${SEED:-0}
[[ $n =~ ^[1-9][0-9]*$ ]] || die "n_games must be a positive integer"
[[ $seed =~ ^[0-9]+$ ]] || die "SEED must be a non-negative integer"
config=$(cd "$(dirname "$1")" && pwd)/$(basename "$1")
cd "$(dirname "${BASH_SOURCE[0]}")/../.." # repo root: batch and doctor read configs/models.yaml

export AMD_VLLM_BASE_URL="${AMD_VLLM_BASE_URL:-http://127.0.0.1:8000/v1}"
echo "AMD_VLLM_BASE_URL=$AMD_VLLM_BASE_URL"

uv sync --frozen
# "<play|batch> <batch id> <model> <model>..."
read -r kind batch models < <(uv run --frozen python - "$config" <<'EOF'
import sys
from pathlib import Path

import yaml

path = Path(sys.argv[1])
cfg = yaml.safe_load(path.read_text())
kind, batch = ("batch", cfg["batch_id"]) if "batch_id" in cfg else ("play", path.stem)
print(kind, batch, *dict.fromkeys(cfg["models"]))
EOF
)
[ -n "${models:-}" ] || die "could not read models from $config"

for m in $models; do
  uv run --frozen societysim doctor --model "$m"
done

rc=0
if [ "$kind" = batch ]; then
  [ -z "${2:-}" ] || echo "batch config: the game count comes from $config, ignoring n_games=$n"
  uv run --frozen societysim batch --config "$config" --agents llm || rc=$?
else
  for ((s = seed; s < seed + n; s++)); do
    uv run --frozen societysim play --config "$config" --agents llm --seed "$s" \
      --batch-id "$batch" || { rc=$?; break; }
  done
fi

dir="runs/$batch"
[ -d "$dir" ] || die "no $dir to pack (exit $rc)"
tar -czf "$dir.tar.gz" -C runs "$batch"
cat <<EOF
----------------------------------------------------------------------------
Packed $PWD/$dir.tar.gz ($(du -h "$dir.tar.gz" | cut -f1))
Download: Jupyter file browser -> $PWD/runs -> right-click $batch.tar.gz -> Download.
On your laptop: tar -xzf $batch.tar.gz -C runs/
----------------------------------------------------------------------------
EOF

if [ "${PUBLISH:-0}" = 1 ]; then
  if uv run --frozen societysim publish --help >/dev/null 2>&1; then
    uv run --frozen societysim publish "$dir" || rc=$?
  else
    echo "societysim publish is not in this checkout yet (T3.5, #21); skipped"
  fi
fi
[ "$rc" = 0 ] || echo "a game or batch step failed (exit $rc); the tarball has what finished"
exit "$rc"
