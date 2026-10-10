#!/usr/bin/env bash
# Deploy the viewer plus one batch of games to a Hugging Face static Space, then ask
# GitHub to refresh the Pages mirror (.github/workflows/pages.yml).
#
#   scripts/deploy-space.sh [--dry-run] <batch_dir> <space_id>
#   scripts/deploy-space.sh runs/batch-01 polter-dev/societysim
#
# The batch needs `societysim report <batch_dir>` first (it writes report/index.json).
# The batch dir is copied to data/ next to the viewer, which is where the viewer looks
# for data/report/index.json. --dry-run builds and stages everything and lists it, but
# uploads nothing and needs no token.
#
# HF_TOKEN must be set in the environment (write access to the Space). It is read by
# huggingface_hub directly and never printed or passed on a command line.
set -euo pipefail

dry_run=false
if [[ "${1:-}" == "--dry-run" ]]; then
  dry_run=true
  shift
fi
if [[ $# -ne 2 ]]; then
  sed -n '5,6p' "$0" >&2
  exit 2
fi
batch=$1
space=$2
root=$(cd "$(dirname "$0")/.." && pwd)

if [[ ! -f "$batch/report/index.json" ]]; then
  echo "error: $batch/report/index.json is missing; run: uv run societysim report $batch" >&2
  exit 1
fi
if [[ ! "$space" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
  echo "error: space id must look like owner/name, got: $space" >&2
  exit 1
fi
if [[ "$dry_run" == false && -z "${HF_TOKEN:-}" ]]; then
  echo "error: HF_TOKEN is not set" >&2
  exit 1
fi

(cd "$root/viewer" && npm ci --silent && npm run build --silent)

stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
cp -R "$root/viewer/dist/." "$stage/"
mkdir "$stage/data"
# .cache holds raw judge responses; the PNGs are for report.md, not the viewer.
rsync -a --exclude '.cache' --exclude '*.png' "$batch/" "$stage/data/"
cat >"$stage/README.md" <<'EOF'
---
title: SocietySim
colorFrom: indigo
colorTo: gray
sdk: static
app_file: index.html
pinned: false
---

Replays and a leaderboard for LLM agents playing Werewolf. Source: https://github.com/polter-dev/societysim
EOF

echo "space: $space"
(cd "$stage" && find . -type f | sort)

if [[ "$dry_run" == true ]]; then
  echo "dry run: nothing uploaded"
  exit 0
fi

uv run --project "$root" python - "$space" "$stage" <<'EOF'
import sys

from huggingface_hub import HfApi

space, folder = sys.argv[1], sys.argv[2]
api = HfApi()  # token comes from HF_TOKEN
api.create_repo(space, repo_type="space", space_sdk="static", exist_ok=True)
api.upload_folder(
    repo_id=space,
    repo_type="space",
    folder_path=folder,
    delete_patterns="*",  # the Space holds exactly this batch
    commit_message="Deploy viewer and games",
)
print(f"deployed: https://huggingface.co/spaces/{space}")
EOF

if command -v gh >/dev/null; then
  gh workflow run pages.yml -f space="$space" -R polter-dev/societysim ||
    echo "warning: could not start the Pages mirror; run the 'Pages' workflow by hand" >&2
fi
