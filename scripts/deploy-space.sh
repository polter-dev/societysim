#!/usr/bin/env bash
# Deploy the viewer plus one batch of games to a Hugging Face static Space, then ask
# GitHub to refresh the Pages mirror (.github/workflows/pages.yml).
#
#   scripts/deploy-space.sh [--dry-run] <batch_dir> <space_id>
#   scripts/deploy-space.sh runs/batch-01 polter-dev/societysim
#   scripts/deploy-space.sh --out <dir> [--force] <batch_dir>
#
# The batch needs `societysim report <batch_dir>` first (it writes report/index.json).
# The batch dir is copied to data/ next to the viewer, which is where the viewer looks
# for data/report/index.json. --dry-run builds and stages everything and lists it, but
# uploads nothing and needs no token.
#
# --out writes the staged site (viewer build plus data/) to <dir> and stops: no upload, no
# HF_TOKEN, no space id. <dir> must be empty or missing unless --force. Preview it with
# `python -m http.server -d <dir> 8000`. The build reads VITE_NEON_DATA_API_URL and
# VITE_NEON_ANON_KEY from the environment; the output says which data source it uses.
#
# HF_TOKEN must be set in the environment (write access to the Space). It is read by
# huggingface_hub directly and never printed or passed on a command line.
#
# The site also gets devui/ (a plain test page, see devui/README.md) at /devui/. If both Neon
# vars are set, devui/config.json carries the URL and anon key so that page can auto-fill them.
#
# Test hooks: SOCIETYSIM_SKIP_VIEWER_BUILD=1 skips npm and copies SOCIETYSIM_VIEWER_DIST
# (default viewer/dist) as is.
set -euo pipefail

usage() {
  echo "usage: $0 [--dry-run] <batch_dir> <space_id>" >&2
  echo "       $0 --out <dir> [--force] <batch_dir>" >&2
  exit 2
}

dry_run=false
force=false
out=""
args=()
while [[ $# -gt 0 ]]; do
  case $1 in
    --dry-run) dry_run=true ;;
    --force) force=true ;;
    --out)
      [[ $# -ge 2 ]] || usage
      out=$2
      shift
      ;;
    *) args+=("$1") ;;
  esac
  shift
done
space=""
if [[ -n "$out" ]]; then
  [[ ${#args[@]} -eq 1 ]] || usage
else
  [[ ${#args[@]} -eq 2 ]] || usage
  space=${args[1]}
fi
batch=${args[0]}
root=$(cd "$(dirname "$0")/.." && pwd)

if [[ ! -f "$batch/report/index.json" ]]; then
  echo "error: $batch/report/index.json is missing; run: uv run societysim report $batch" >&2
  exit 1
fi
if [[ -n "$space" && ! "$space" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
  echo "error: space id must look like owner/name, got: $space" >&2
  exit 1
fi
if [[ -z "$out" && "$dry_run" == false && -z "${HF_TOKEN:-}" ]]; then
  echo "error: HF_TOKEN is not set" >&2
  exit 1
fi
if [[ -n "$out" && "$force" == false && -d "$out" && -n "$(ls -A "$out")" ]]; then
  echo "error: $out is not empty; pass --force to write into it" >&2
  exit 1
fi

dist="$root/viewer/dist"
if [[ "${SOCIETYSIM_SKIP_VIEWER_BUILD:-}" == 1 ]]; then
  dist=${SOCIETYSIM_VIEWER_DIST:-$dist}
else
  (cd "$root/viewer" && npm ci --silent && npm run build --silent)
fi
if [[ -n "${VITE_NEON_DATA_API_URL:-}" ]]; then
  echo "data source: Neon (VITE_NEON_DATA_API_URL is set)"
else
  echo "data source: static data/report/index.json"
fi
if [[ -z "${VITE_NEON_DATA_API_URL:-}" || -z "${VITE_NEON_ANON_KEY:-}" ]]; then
  echo "::notice::VITE_NEON_DATA_API_URL and/or VITE_NEON_ANON_KEY unset: no devui/config.json; the viewer uses static data unless the URL is set."
fi

if [[ -n "$out" ]]; then
  stage=$out
  mkdir -p "$stage"
else
  stage=$(mktemp -d)
  trap 'rm -rf "$stage"' EXIT
fi
cp -R "$dist/." "$stage/"
mkdir -p "$stage/data"
# .cache holds raw judge responses; the PNGs are for report.md, not the viewer.
rsync -a --exclude '.cache' --exclude '*.png' "$batch/" "$stage/data/"
# Dev test UI at /devui/, reading the same data/; it fetches the JSON Schemas at runtime.
mkdir -p "$stage/devui/schema"
cp "$root/devui/index.html" "$stage/devui/"
cp "$root"/schema/*.schema.json "$stage/devui/schema/"
# With both Neon build vars set, the dev page pre-fills its Neon fields from devui/config.json.
# Only the Data API URL and the public read-only anon key go in; never the database URL.
if [[ -n "${VITE_NEON_DATA_API_URL:-}" && -n "${VITE_NEON_ANON_KEY:-}" ]]; then
  python3 -c 'import json,os; print(json.dumps({"neon_url": os.environ["VITE_NEON_DATA_API_URL"], "neon_anon_key": os.environ["VITE_NEON_ANON_KEY"]}))' >"$stage/devui/config.json"
fi
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

if [[ -n "$out" ]]; then
  echo "staged site: $out"
  echo "serve it:    python -m http.server -d $out 8000"
  echo "index:       http://localhost:8000/"
  echo "leaderboard: http://localhost:8000/?page=leaderboard"
  echo "dev UI:      http://localhost:8000/devui/"
  if [[ -f "$batch/highlights.json" ]]; then
    link=$(python3 -c 'import json,sys; h=json.load(open(sys.argv[1])); print(h[0].get("link","") if h else "")' "$batch/highlights.json")
    [[ -z "$link" ]] || echo "top moment:  http://localhost:8000/$link"
  fi
  exit 0
fi

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
