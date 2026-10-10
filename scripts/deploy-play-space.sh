#!/usr/bin/env bash
# Deploy the visitor-play game server (src/societysim/server) to a Docker Hugging Face Space.
#
#   scripts/deploy-play-space.sh [--dry-run] [space_id]     (default polter-dev/societysim-play)
#
# The caller's environment supplies everything. Values are never printed or put on a command
# line; the output lists names only.
#   HF_TOKEN              write access to the Space (the deploy itself; not the server's key)
#   Secrets, set on the Space under the name the server reads (source env name -> Space name):
#     PLAY_OPENAI_API_KEY  -> OPENAI_API_KEY      dedicated, limited keys for visitor play
#     PLAY_HF_TOKEN        -> HF_TOKEN
#     PLAY_GEMINI_API_KEY  -> GEMINI_API_KEY
#     NEON_DATABASE_URL    -> NEON_DATABASE_URL   required: spend ledger and game stream
#     TURNSTILE_SECRET_KEY -> TURNSTILE_SECRET_KEY  required: the server will not start without
#   Variables (not secret): PLAY_TRUSTED_PROXIES and PLAY_ALLOWED_MODELS get the defaults below
#   unless set in the environment; PLAY_RATE_PER_MIN, PLAY_MAX_ACTIVE_PER_IP,
#   PLAY_MAX_GAMES_PER_IP_DAY, PLAY_MAX_CONCURRENT, PLAY_MAX_GAME_MINUTES,
#   PLAY_DAILY_BUDGET_USD, PLAY_MAX_TOKENS, PLAY_ENABLED are passed on when set.
#
# PLAY_TRUSTED_PROXIES: HF Spaces puts a load balancer in front of the container. The
# container sees the balancer's private address as the peer and the visitor in
# X-Forwarded-For. The server only believes forwarding headers from trusted peers, and
# refuses to start on a Space without this variable (every visitor would share one IP and
# one rate limit). The private ranges are the value the server's own error message names.
#
# PLAY_ALLOWED_MODELS: only models with a known price, so the daily budget can be enforced.
# An unpriced call never fits the ledger. All seven below price through configs/models.yaml
# or LiteLLM's table (tests/test_deploy_play.py checks).
#
# --dry-run prints the plan and uploads nothing; it needs no token.
set -euo pipefail

cd "$(dirname "$0")/.."

dry_run=false
stage_only=""
space="polter-dev/societysim-play"
while [ $# -gt 0 ]; do
  a=$1; shift
  case "$a" in
    --dry-run) dry_run=true ;;
    --stage-only) stage_only=${1:?--stage-only needs a directory}; shift ;;
    -*) echo "usage: $0 [--dry-run] [--stage-only dir] [space_id]" >&2; exit 2 ;;
    *) space="$a" ;;
  esac
done

DEFAULT_PROXIES="10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
DEFAULT_MODELS="qwen3-235b,llama4-maverick,gemma4-26b,gpt-oss-120b,deepseek-v3.1,gemini-flash-lite,gpt-5-nano"

# "source_env:space_name:required"
SECRETS=(
  "PLAY_OPENAI_API_KEY:OPENAI_API_KEY:no"
  "PLAY_HF_TOKEN:HF_TOKEN:no"
  "PLAY_GEMINI_API_KEY:GEMINI_API_KEY:no"
  "NEON_DATABASE_URL:NEON_DATABASE_URL:yes"
  "TURNSTILE_SECRET_KEY:TURNSTILE_SECRET_KEY:yes"
  "PLAY_ALERT_WEBHOOK_URL:PLAY_ALERT_WEBHOOK_URL:no"
)
PASSTHROUGH=(PLAY_RATE_PER_MIN PLAY_MAX_ACTIVE_PER_IP PLAY_MAX_GAMES_PER_IP_DAY PLAY_MAX_CONCURRENT
  PLAY_MAX_GAME_MINUTES PLAY_DAILY_BUDGET_USD PLAY_MAX_TOKENS PLAY_ENABLED)

# The Space repo is built from TRACKED files only (git ls-files), never a directory copy, so an
# untracked .env, runs/ or .cache/ can't reach a public Space. Dockerfile and README sit at the
# Space root; the rest keeps its path.
TRACKED=(src/societysim configs/models.yaml configs/voices.yaml schema neon/schema.sql pyproject.toml
  uv.lock LICENSE .dockerignore)
FORBIDDEN='(^|/)(\.env[^/]*|[^/]*\.pem|[^/]*\.key|runs|\.cache)(/|$)'

stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT
cp server/Dockerfile "$stage/Dockerfile"
cp server/README.md "$stage/README.md"
# Committed content (git archive HEAD), not the working tree: an uncommitted edit can't ship.
git archive HEAD -- "${TRACKED[@]}" | tar -x -C "$stage"
bad=$(cd "$stage" && find . -type f | sed 's|^\./||' | grep -E "$FORBIDDEN" || true)
if [ -n "$bad" ]; then
  echo "error: staged files that look like secrets or local data:" >&2
  echo "$bad" >&2
  exit 1
fi
FILES=($(cd "$stage" && find . -type f | sed 's|^\./||' | sort))
if [ -n "$stage_only" ]; then
  mkdir -p "$stage_only"
  cp -R "$stage"/. "$stage_only"/
  printf '%s\n' "${FILES[@]}"
  exit 0
fi

secret_lines=()
missing_required=()
for s in "${SECRETS[@]}"; do
  IFS=: read -r src name req <<<"$s"
  if [ -n "${!src:-}" ]; then
    secret_lines+=("secret   $name  (from $src)")
  elif [ "$req" = yes ]; then
    secret_lines+=("MISSING  $name  (required; set $src)")
    missing_required+=("$src")
  else
    secret_lines+=("skipped  $name  ($src is not set; models that need it will fail)")
  fi
done

var_names=(PLAY_TRUSTED_PROXIES PLAY_ALLOWED_MODELS)
var_vals=("${PLAY_TRUSTED_PROXIES:-$DEFAULT_PROXIES}" "${PLAY_ALLOWED_MODELS:-$DEFAULT_MODELS}")
for v in "${PASSTHROUGH[@]}"; do
  if [ -n "${!v:-}" ]; then
    var_names+=("$v")
    var_vals+=("${!v}")
  fi
done

echo "Space: $space (sdk docker, app_port 7860)"
echo "Files uploaded:"
printf '  %s\n' "${FILES[@]}"
echo "Space secrets (names only):"
printf '  %s\n' "${secret_lines[@]}"
echo "Space variables:"
for i in "${!var_names[@]}"; do echo "  ${var_names[$i]}=${var_vals[$i]}"; done

if $dry_run; then
  echo "dry run: nothing uploaded."
  exit 0
fi

if [ "${#missing_required[@]}" -gt 0 ]; then
  echo "error: set ${missing_required[*]} (the server will not start without them)" >&2
  exit 1
fi
test -n "${HF_TOKEN:-}" || { echo "error: HF_TOKEN (write access to the Space) is not set" >&2; exit 1; }

# Names go in as arguments; the Python reads each value from the environment.
secret_args=()
for s in "${SECRETS[@]}"; do
  IFS=: read -r src name _ <<<"$s"
  [ -n "${!src:-}" ] && secret_args+=("$src=$name")
done
export STAGE="$stage" SPACE="$space"
# Variables are not secret, so NAME=VALUE arguments are fine.
var_args=()
for i in "${!var_names[@]}"; do var_args+=("${var_names[$i]}=${var_vals[$i]}"); done
export NSECRETS="${#secret_args[@]}"
uv run python - "${secret_args[@]}" "${var_args[@]}" <<'PY'
import os
import sys

from huggingface_hub import HfApi

api = HfApi(token=os.environ["HF_TOKEN"])
space = os.environ["SPACE"]
api.create_repo(space, repo_type="space", space_sdk="docker", exist_ok=True)
n = int(os.environ["NSECRETS"])
for pair in sys.argv[1 : 1 + n]:
    src, name = pair.split("=", 1)
    api.add_space_secret(space, name, os.environ[src])
    print(f"set secret   {name}")
for pair in sys.argv[1 + n :]:
    name, value = pair.split("=", 1)
    api.add_space_variable(space, name, value)
    print(f"set variable {name}")
api.upload_folder(repo_id=space, repo_type="space", folder_path=os.environ["STAGE"])
print(f"uploaded: https://huggingface.co/spaces/{space}")
PY
