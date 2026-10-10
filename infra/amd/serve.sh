#!/usr/bin/env bash
# Serve one open model on the AMD Radeon AI PRO R9700 (gfx1201, 32 GB) as an OpenAI-compatible
# endpoint, for societysim T6.3. Built for the AUP Learning Cloud "Basic GPU Environment" pod:
# Ubuntu 24.04, Python 3.12, uv, system torch with ROCm 7.x, no Docker, no root, no inbound port.
#
#   bash infra/amd/serve.sh          # install (first run only), serve, print the next steps
#   bash infra/amd/serve.sh status   # what is running, the URL, latest serving stats
#   bash infra/amd/serve.sh stop     # stop server, tunnel and stats logger; print session hours
#   bash infra/amd/serve.sh --backend llamacpp   # skip vLLM: llama.cpp only (Strix Halo 8060S)
#
# Safe to re-run: finished steps are skipped and a healthy server is reused.
#
# Local mode (the default inside JupyterHub pods such as AUP, or AMD_TUNNEL=none): no tunnel.
# The server stays on 127.0.0.1 and societysim runs in the same pod (infra/amd/run_on_box.sh).
# AUP pods block Cloudflare tunnels: QUIC times out and TLS to the edge on 7844 is reset.
#
# Backend order (AMD_BACKEND=auto):
#   vLLM: AMD's gfx120X wheel on the system torch, the same wheel with its own torch,
#         AMD's Python 3.13 stack, then a source build against the system torch.
#   If no vLLM install passes the GPU check or the server will not come up, it falls back to
#   llama.cpp `llama-server` built with HIP for gfx120X (prebuilt, then a source build), on the
#   same port with the same API key. Callers see no difference.
#
# Settings (environment variables, all optional):
#   AMD_MODEL          HF model id for vLLM (default openai/gpt-oss-20b). It is also the served
#                      model name (vLLM --served-model-name, llama.cpp --alias), so /v1/models
#                      lists exactly this id. configs/models.yaml must send it verbatim: LiteLLM's
#                      openai/ route strips one prefix, so litellm_model is openai/$AMD_MODEL.
#   AMD_GGUF_REPO/FILE GGUF used by the llama.cpp fallback (default ggml-org/gpt-oss-20b-GGUF).
#   AMD_BACKEND        auto | vllm | llamacpp (default auto); `--backend X` sets it too
#   AMD_LLAMA_VARIANT  auto | vulkan | hip: which llama.cpp build to try first (default auto:
#                      vulkan on gfx1151 Strix Halo, where vLLM/ROCm is weak; hip elsewhere)
#   AMD_API_KEY        required, 32+ chars (openssl rand -hex 32); same value in the laptop .env
#   AMD_WORK           working dir for venvs, weights, logs (default ~/amd-serve)
#   AMD_PORT           local port (default 8000)
#   AMD_TUNNEL         none (or 0) = local mode, 1 = open a cloudflared quick tunnel
#                      (default: none inside JupyterHub, so on AUP; 1 elsewhere)
#   AMD_TUNNEL_PROTOCOL http2 | quic | auto (default http2; http2 works without outbound UDP)
#   AMD_MAX_MODEL_LEN  context per request (default 16384)
#   AMD_GPU_MEM_UTIL   vLLM --gpu-memory-utilization (default 0.90)
#   AMD_START_TIMEOUT  seconds to wait for the server, download included (default 1800)
#   AMD_STATS_INTERVAL seconds between stats samples (default 60)
#   AMD_PRICE_PER_HOUR USD per GPU hour for the session cost (default 0: minute quota)
#   VLLM_SRC_REF       vLLM git ref for the source build (default v0.16.0)

set -Eeuo pipefail

AMD_WORK="${AMD_WORK:-$HOME/amd-serve}"
AMD_MODEL="${AMD_MODEL:-openai/gpt-oss-20b}"
AMD_GGUF_REPO="${AMD_GGUF_REPO:-ggml-org/gpt-oss-20b-GGUF}"
AMD_GGUF_FILE="${AMD_GGUF_FILE:-gpt-oss-20b-MXFP4.gguf}"
AMD_BACKEND="${AMD_BACKEND:-auto}"
AMD_PORT="${AMD_PORT:-8000}"
if [ -n "${JUPYTERHUB_API_URL:-}" ]; then AMD_TUNNEL="${AMD_TUNNEL:-none}"; fi
AMD_TUNNEL="${AMD_TUNNEL:-1}"
AMD_TUNNEL_PROTOCOL="${AMD_TUNNEL_PROTOCOL:-http2}"
AMD_MAX_MODEL_LEN="${AMD_MAX_MODEL_LEN:-16384}"
AMD_GPU_MEM_UTIL="${AMD_GPU_MEM_UTIL:-0.90}"
AMD_START_TIMEOUT="${AMD_START_TIMEOUT:-1800}"
AMD_STATS_INTERVAL="${AMD_STATS_INTERVAL:-60}"
AMD_PRICE_PER_HOUR="${AMD_PRICE_PER_HOUR:-0}"
VLLM_SRC_REF="${VLLM_SRC_REF:-v0.16.0}"
if [ -z "${GPU_ARCH:-}" ]; then
  GPU_ARCH=gfx1201
  if command -v rocminfo >/dev/null && [[ $(rocminfo 2>/dev/null || true) == *gfx1151* ]]; then
    GPU_ARCH=gfx1151
  fi
fi
AMD_LLAMA_VARIANT="${AMD_LLAMA_VARIANT:-auto}"

AMD_WHEELS="https://rocm.frameworks.amd.com/whl/gfx120X-all"  # vLLM + flash-attn for gfx120X
AMD_TORCH_INDEX="https://repo.amd.com/rocm/whl/gfx120X-all/"   # torch built for gfx120X
FLASH_WHL="$AMD_WHEELS/flash_attn-2.8.3-py3-none-any.whl"       # AMD's vLLM wheels require it
TORCH312="torch==2.9.1+rocm7.12.0"  # what AMD's cp312 vLLM wheel (rocm712) was built against
TV312="torchvision==0.24.0+rocm7.12.0"
TORCH313="torch==2.10.0+rocm7.13.0" # matches AMD's cp313 vLLM wheel
TV313="torchvision==0.25.0+rocm7.13.0"
LLAMACPP_RELEASE="https://api.github.com/repos/lemonade-sdk/llamacpp-rocm/releases/latest"  # HIP
LLAMACPP_VULKAN_RELEASES="https://api.github.com/repos/ggml-org/llama.cpp/releases?per_page=10"
CLOUDFLARED_URL="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64"

SYS_PY=/usr/bin/python3
VENV="$AMD_WORK/venv"       # Python 3.12, sees the system ROCm torch
VENV313="$AMD_WORK/venv313" # Python 3.13, AMD's matched torch + vLLM
LOCAL="http://127.0.0.1:$AMD_PORT"
REPO=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
export HF_HOME="${HF_HOME:-$AMD_WORK/hf}"
export PATH="$AMD_WORK/bin:$PATH:/opt/rocm/bin"
[ -x "$SYS_PY" ] || SYS_PY=python3

umask 077 # logs and state are for this user only
mkdir -p "$AMD_WORK/bin" "$AMD_WORK/logs" "$HF_HOME"
LOGS="$AMD_WORK/logs"
chmod 700 "$AMD_WORK" "$LOGS"

log() { printf '\n[%s] %s\n' "$(date +%H:%M:%S)" "$*"; }
warn() { printf '[%s] WARNING: %s\n' "$(date +%H:%M:%S)" "$*" >&2; }
die() {
  printf '[%s] ERROR: %s\n' "$(date +%H:%M:%S)" "$*" >&2
  exit 1
}
trap 'warn "command failed (exit $?) at line $LINENO: $BASH_COMMAND"' ERR

alive() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }
healthy() { curl -sf -m 5 "$LOCAL/health" >/dev/null 2>&1; }
# curl with the bearer key fed on stdin, so the key never appears in argv, logs or traces.
authcurl() { printf 'Authorization: Bearer %s\n' "$AMD_API_KEY" | curl -H @- "$@"; }

# Run a background process in its own session so it survives the terminal; record its pid.
spawn() { # spawn <pidfile> <logfile> cmd...
  local pidfile=$1 logfile=$2
  shift 2
  if command -v setsid >/dev/null; then set -- setsid "$@"; fi
  nohup "$@" >"$logfile" 2>&1 </dev/null &
  echo $! >"$pidfile"
}

kill_pidfile() {
  local f=$1
  if alive "$f"; then
    local pid
    pid=$(cat "$f")
    kill -- "-$pid" 2>/dev/null || kill "$pid" 2>/dev/null || true
    for _ in $(seq 20); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
    kill -9 -- "-$pid" 2>/dev/null || kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$f"
}

# --- preflight -------------------------------------------------------------------------------

preflight() {
  log "Preflight"
  echo "work dir: $AMD_WORK ($(df -h "$AMD_WORK" | awk 'NR==2 {print $4 " free"}'))"
  echo "python:   $("$SYS_PY" --version 2>&1) at $SYS_PY"
  command -v uv >/dev/null || die "uv not found on PATH"
  command -v curl >/dev/null || die "curl not found on PATH"
  echo "uv:       $(uv --version)"
  "$SYS_PY" - <<'EOF' || warn "system torch cannot see the GPU; the vLLM wheel on system torch will fail"
import torch
print(f"torch:    {torch.__version__} (HIP {torch.version.hip})")
assert torch.cuda.is_available()
p = torch.cuda.get_device_properties(0)
print(f"GPU:      {p.name}, {p.total_memory / 2**30:.0f} GiB, {getattr(p, 'gcnArchName', '?')}")
EOF
  if command -v rocminfo >/dev/null && [[ $(rocminfo 2>/dev/null || true) != *"$GPU_ARCH"* ]]; then
    warn "rocminfo does not list $GPU_ARCH; set GPU_ARCH if this is a different GPU"
  fi
}

# AMD_API_KEY must come from the user; the script never prints or stores it. Only its hash is
# kept, to notice a new key: KEY_CHANGED=1 means a running server has the old one and restarts.
KEY_CHANGED=0
api_key() {
  local key=${AMD_API_KEY:-}
  if [ "${#key}" -lt 32 ]; then
    die "AMD_API_KEY is unset or shorter than 32 characters. Generate one with
    openssl rand -hex 32
put it in .env on your laptop as AMD_API_KEY=..., then here run
    read -rs AMD_API_KEY && export AMD_API_KEY    # paste it, press Enter (not echoed)
and run this script again."
  fi
  export AMD_API_KEY
  rm -f "$AMD_WORK/api_key" # plain-text key written by an older version of this script
  local hash
  hash=$("$SYS_PY" -c 'import hashlib, os; print(hashlib.sha256(os.environ["AMD_API_KEY"].encode()).hexdigest())')
  if [ "$hash" != "$(cat "$AMD_WORK/api_key.sha256" 2>/dev/null || true)" ]; then
    echo "$hash" >"$AMD_WORK/api_key.sha256"
    KEY_CHANGED=1
  fi
}

base_venv() {
  if [ ! -x "$VENV/bin/python" ]; then
    log "Creating $VENV (Python 3.12, reuses the system ROCm torch)"
    uv venv --system-site-packages --python "$SYS_PY" "$VENV"
  fi
  uv pip install --python "$VENV/bin/python" -q huggingface_hub
}

# --- vLLM install ----------------------------------------------------------------------------

# Write <venv>/serve.env: runtime variables vLLM on ROCm needs, sourced before verify and serve.
runtime_env() {
  local v=$1 d
  {
    echo "export FLASH_ATTENTION_TRITON_AMD_ENABLE=TRUE"
    if ! "$v/bin/python" -c 'import amdsmi' 2>/dev/null; then
      for d in "$v"/lib/python3*/site-packages/_rocm_sdk_core/share/amd_smi \
        /usr/local/lib/python3*/dist-packages/_rocm_sdk_core/share/amd_smi \
        /usr/lib/python3/dist-packages/_rocm_sdk_core/share/amd_smi \
        /opt/rocm/share/amd_smi; do
        [ -d "$d" ] && echo "export PYTHONPATH=$d\${PYTHONPATH:+:\$PYTHONPATH}" && break
      done
    fi
  } >"$v/serve.env"
}

verify_vllm() { # verify_vllm <venv>: vLLM imports, its kernels load, and torch runs on the GPU
  local v=$1
  runtime_env "$v"
  echo "verifying vLLM in $v"
  (
    # shellcheck disable=SC1091
    source "$v/serve.env"
    "$v/bin/python" - <<'EOF'
import torch, vllm
from vllm.platforms import current_platform
print(f"  vllm {vllm.__version__}, torch {torch.__version__} from {torch.__file__}")
assert torch.cuda.is_available(), "torch sees no GPU"
print(f"  GPU {torch.cuda.get_device_name(0)} {torch.cuda.get_device_properties(0).gcnArchName}")
assert current_platform.is_rocm(), f"vLLM picked platform {current_platform.device_name!r}"
import vllm._C  # noqa: F401  compiled kernels; fails on an ABI or arch mismatch
x = torch.randn(512, 512, device="cuda")
assert torch.isfinite((x @ x).sum()).item()
print("  vLLM check passed")
EOF
  )
}

# Remove torch/triton copies that a dependency install put into a --system-site-packages venv,
# so the system ROCm build is used again.
purge_shadow_torch() {
  local v=$1 names
  names=$("$v/bin/python" - <<'EOF'
import importlib.metadata as md, sys
for n in ("torch", "torchvision", "torchaudio", "triton", "pytorch-triton-rocm"):
    try:
        d = md.distribution(n)
    except md.PackageNotFoundError:
        continue
    if str(d.locate_file("")).startswith(sys.prefix):
        print(n)
EOF
  )
  if [ -n "$names" ]; then
    echo "removing venv copies that shadow the system ROCm build: $names"
    # shellcheck disable=SC2086
    uv pip uninstall --python "$v/bin/python" $names
  fi
}

amd_wheel() { # amd_wheel cp312|cp313: URL of AMD's gfx120X vLLM wheel for that Python
  local name
  name=$(curl -fsSL "$AMD_WHEELS/vllm/" | grep -oE "vllm-[^\"/<>]*-$1-$1-linux_x86_64\.whl" | tail -1)
  [ -n "$name" ] || return 1
  echo "$AMD_WHEELS/${name//+/%2B}" # the server needs the + encoded
}

# Install AMD's cp312 vLLM wheel and its flash-attn into $VENV with torch pinned to $1.
# The wheel does not list torch, but some of its dependencies do; the pin plus AMD's index keep
# uv from pulling a CUDA torch from PyPI.
install_amd312() { # install_amd312 <torch pin> <torchvision pin>
  local url
  url=$(amd_wheel cp312) || return 1
  echo "wheel: $url, pins: $*"
  uv pip install --python "$VENV/bin/python" --index-strategy unsafe-best-match \
    --extra-index-url "$AMD_TORCH_INDEX" "$@" "$FLASH_WHL" "$url"
}

# 1) AMD wheel on the system torch (same version pins, so usually no torch download).
vllm_amd_system_torch() {
  local pins
  # torchvision 0.N pairs with torch 2.(N-15); pin it too or uv picks one built for a newer torch.
  pins=$("$SYS_PY" - <<'EOF'
import importlib.metadata as md
import torch
v = torch.__version__
try:
    tv = md.version("torchvision")
except md.PackageNotFoundError:
    minor, local = int(v.split(".")[1]), v.partition("+")[2]
    tv = f"0.{minor + 15}.0" + (f"+{local}" if local else "")
print(f"torch=={v} torchvision=={tv}")
EOF
  ) || return 1
  # shellcheck disable=SC2086
  install_amd312 $pins || return 1
  verify_vllm "$VENV"
}

# 2) Same wheel with the torch it was built for (rocm712 in its name), in the venv.
vllm_amd_matched_torch() {
  install_amd312 "$TORCH312" "$TV312" || return 1
  verify_vllm "$VENV"
}

# 3) AMD's documented gfx120X stack: Python 3.13, torch 2.10 + ROCm 7.13, vLLM 0.19.
vllm_amd_py313() {
  local url
  url=$(amd_wheel cp313) || return 1
  [ -x "$VENV313/bin/python" ] || uv venv --python 3.13 "$VENV313" || return 1
  uv pip install --python "$VENV313/bin/python" --index-strategy unsafe-best-match \
    --extra-index-url "$AMD_TORCH_INDEX" "$TORCH313" "$TV313" "$FLASH_WHL" "$url" || return 1
  verify_vllm "$VENV313"
}

# 4) Source build against the system torch, kernels for $GPU_ARCH only.
vllm_source() {
  local src="$AMD_WORK/vllm-src" jobs
  command -v git >/dev/null || return 1
  if [ ! -d "$src/.git" ]; then
    git clone --depth 1 --branch "$VLLM_SRC_REF" https://github.com/vllm-project/vllm "$src" ||
      return 1
  fi
  uv pip uninstall --python "$VENV/bin/python" vllm 2>/dev/null || true
  purge_shadow_torch "$VENV"
  (cd "$src" && "$VENV/bin/python" use_existing_torch.py) || return 1
  uv pip install --python "$VENV/bin/python" cmake ninja packaging "setuptools>=77" \
    setuptools-scm wheel jinja2 pybind11 || return 1
  uv pip install --python "$VENV/bin/python" -r "$src/requirements/rocm.txt" || return 1
  purge_shadow_torch "$VENV"
  jobs=$(nproc)
  [ "$jobs" -gt 64 ] && jobs=64
  echo "building vLLM $VLLM_SRC_REF for $GPU_ARCH with $jobs jobs (log: $LOGS/vllm-build.log)"
  if ! (cd "$src" && PYTORCH_ROCM_ARCH="$GPU_ARCH" MAX_JOBS="$jobs" ROCM_HOME=/opt/rocm \
    "$VENV/bin/python" setup.py develop >"$LOGS/vllm-build.log" 2>&1); then
    tail -n 30 "$LOGS/vllm-build.log"
    return 1
  fi
  purge_shadow_torch "$VENV"
  verify_vllm "$VENV"
}

# Sets VLLM_VENV to a verified install. Remembers success and failure across runs.
install_vllm() {
  if [ -s "$AMD_WORK/vllm.ok" ] && verify_vllm "$(cat "$AMD_WORK/vllm.ok")" >/dev/null 2>&1; then
    VLLM_VENV=$(cat "$AMD_WORK/vllm.ok")
    log "vLLM already installed in $VLLM_VENV"
    return 0
  fi
  if [ -f "$AMD_WORK/vllm.failed" ] && [ "$AMD_BACKEND" = auto ]; then
    log "vLLM failed on an earlier run ($(cat "$AMD_WORK/vllm.failed")); skipping it." \
      "Delete $AMD_WORK/vllm.failed to try again."
    return 1
  fi
  local m
  for m in vllm_amd_system_torch vllm_amd_matched_torch vllm_amd_py313 vllm_source; do
    log "vLLM install: trying $m (log: $LOGS/$m.log)"
    if "$m" 2>&1 | tee "$LOGS/$m.log"; then # pipefail: fails when $m fails
      VLLM_VENV=$([ "$m" = vllm_amd_py313 ] && echo "$VENV313" || echo "$VENV")
      echo "$VLLM_VENV" >"$AMD_WORK/vllm.ok"
      log "vLLM install OK via $m"
      return 0
    fi
    warn "$m did not produce a working vLLM"
  done
  echo "no install method passed the GPU check" >"$AMD_WORK/vllm.failed"
  return 1
}

# --- serving ---------------------------------------------------------------------------------

wait_healthy() { # wait_healthy <pidfile> <logfile>
  local start=$SECONDS
  echo "waiting up to ${AMD_START_TIMEOUT}s for $LOCAL/health (log: $2)"
  until healthy; do
    if ! alive "$1"; then
      warn "server exited during startup; last lines of $2:"
      tail -n 40 "$2" >&2
      return 1
    fi
    if [ $((SECONDS - start)) -ge "$AMD_START_TIMEOUT" ]; then
      warn "server not healthy after ${AMD_START_TIMEOUT}s"
      kill_pidfile "$1"
      return 1
    fi
    sleep 10
    if [ $(((SECONDS - start) % 60)) -lt 10 ]; then
      echo "  $((SECONDS - start))s: $(tail -n 1 "$2" | cut -c1-160)"
    fi
  done
  echo "healthy after $((SECONDS - start))s"
  date +%s >"$AMD_WORK/started_at"
}

download_hf_model() {
  log "Downloading $AMD_MODEL to $HF_HOME (skips files already there)"
  "$VENV/bin/python" - "$AMD_MODEL" <<'EOF'
import sys
from huggingface_hub import snapshot_download
# gpt-oss ships extra copies of the weights under original/ and metal/; vLLM needs neither.
print(snapshot_download(sys.argv[1], ignore_patterns=["original/*", "metal/*", "*.gguf", "*.pth"]))
EOF
}

start_vllm() { # start_vllm xgrammar|outlines
  local so
  if "$VLLM_VENV/bin/python" -c 'import vllm.config as c; assert hasattr(c, "StructuredOutputsConfig")' \
    2>/dev/null; then
    so=(--structured-outputs-config "{\"backend\": \"$1\"}")
  else
    so=(--guided-decoding-backend "$1")
  fi
  log "Starting vLLM: $AMD_MODEL on $LOCAL, structured-output backend $1"
  # The key goes in VLLM_API_KEY (same as --api-key), so vLLM's startup log of its arguments and
  # `ps` never show it. smoke() checks that unauthenticated requests get 401.
  # shellcheck disable=SC1091
  (
    source "$VLLM_VENV/serve.env"
    VLLM_API_KEY="$AMD_API_KEY" spawn "$AMD_WORK/server.pid" "$LOGS/server.log" \
      "$VLLM_VENV/bin/vllm" serve "$AMD_MODEL" --served-model-name "$AMD_MODEL" \
      --host 127.0.0.1 --port "$AMD_PORT" \
      --max-model-len "$AMD_MAX_MODEL_LEN" --gpu-memory-utilization "$AMD_GPU_MEM_UTIL" \
      --max-num-seqs 16 "${so[@]}"
  )
  echo "vllm $1" >"$AMD_WORK/backend"
  wait_healthy "$AMD_WORK/server.pid" "$LOGS/server.log"
}

# One json_schema request against the local server, the same shape `societysim doctor` sends.
smoke() {
  log "Smoke test: json_schema chat completion"
  local code
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 10 -X POST -H 'Content-Type: application/json' \
    -d '{}' "$LOCAL/v1/chat/completions")
  if [ "$code" != 401 ]; then
    [ "$AMD_TUNNEL" = 1 ] &&
      die "a request without the API key got HTTP $code, not 401. Not opening a public tunnel."
    warn "a request without the API key got HTTP $code, expected 401"
  fi
  "$SYS_PY" - "$LOCAL" "$AMD_MODEL" <<'EOF'
import json, os, sys, time, urllib.request
base, model = sys.argv[1:3]
key = os.environ["AMD_API_KEY"]
schema = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}, "word": {"type": "string"}},
    "required": ["ok", "word"],
    "additionalProperties": False,
}
body = {
    "model": model,
    "max_tokens": 1024,
    "messages": [
        {"role": "system", "content": "Reply with JSON only."},
        {"role": "user", "content": 'Return a JSON object {"ok": true, "word": "<any one word>"}.'},
    ],
    "response_format": {
        "type": "json_schema",
        "json_schema": {"name": "Ping", "schema": schema, "strict": True},
    },
}
req = urllib.request.Request(
    f"{base}/v1/chat/completions",
    json.dumps(body).encode(),
    {"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
)
t = time.time()
try:
    r = json.load(urllib.request.urlopen(req, timeout=300))
except urllib.error.HTTPError as e:
    sys.exit(f"  HTTP {e.code}: {e.read().decode()[:400]}")
content = r["choices"][0]["message"].get("content") or ""
print(f"  {time.time() - t:.1f}s, usage {r.get('usage')}, content {content[:200]!r}")
d = json.loads(content)
assert isinstance(d.get("ok"), bool) and isinstance(d.get("word"), str), "reply misses the schema"
print("  json_schema OK")
EOF
}

serve_vllm() {
  install_vllm || return 1
  download_hf_model || return 1
  if start_vllm xgrammar; then
    smoke && return 0
    warn "json_schema failed with xgrammar; restarting with outlines"
    kill_pidfile "$AMD_WORK/server.pid"
    if start_vllm outlines; then
      smoke && return 0
      warn "json_schema failed with outlines too. Keeping vLLM up; set structured: json_object" \
        "for the amd-* entry in configs/models.yaml (doctor will confirm)."
      return 0
    fi
  fi
  echo "vLLM installed but the server would not start or serve; see $LOGS/server.log" \
    >"$AMD_WORK/vllm.failed"
  return 1
}

# --- llama.cpp fallback ----------------------------------------------------------------------

LLAMA_DIR="$AMD_WORK/llamacpp"

llama_ok() { # llama_ok <binary>: runs and sees a real AMD GPU (ROCm or Vulkan, not llvmpipe)
  local bin=$1 out
  out=$(LD_LIBRARY_PATH="$(dirname "$bin"):${LD_LIBRARY_PATH:-}" "$bin" --list-devices 2>&1) ||
    true
  echo "$out" | tail -n 5
  [[ ${out,,} == *rocm* || ${out,,} == *gfx1* || ${out,,} == *radeon* || ${out,,} == *amd* ]]
}

# Unpack a release archive (.zip or .tar.gz) into <dir>; sets LLAMA_BIN, true if it sees the GPU.
llama_unpack() { # llama_unpack <archive> <dir>
  rm -rf "$2" && mkdir -p "$2"
  case "$1" in
    *.zip) "$SYS_PY" -m zipfile -e "$1" "$2" || return 1 ;;
    *) tar -xzf "$1" -C "$2" || return 1 ;;
  esac
  find "$2" -type f \( -name 'llama-*' -o -name '*.so*' \) -exec chmod +x {} +
  LLAMA_BIN=$(find "$2" -type f -name llama-server | head -1)
  [ -n "$LLAMA_BIN" ] && llama_ok "$LLAMA_BIN"
}

# Newest asset URL whose name contains <pattern>, from a GitHub releases JSON (object or list).
release_asset() { # release_asset <api url> <pattern>
  curl -fsSL "$1" | "$SYS_PY" -c '
import json, sys
d = json.load(sys.stdin)
for r in d if isinstance(d, list) else [d]:
    for a in r["assets"]:
        if sys.argv[1] in a["name"]:
            print(a["browser_download_url"])
            sys.exit()
' "$2"
}

llama_fetch() { # llama_fetch <name> <api url> <pattern>: download, unpack, GPU check
  local url archive="$AMD_WORK/llamacpp-$1.tar.gz"
  url=$(release_asset "$2" "$3") || return 1
  [ -n "$url" ] || return 1
  [[ $url == *.zip ]] && archive="$AMD_WORK/llamacpp-$1.zip"
  echo "downloading $url"
  curl -fL --retry 3 -o "$archive" "$url" || return 1
  llama_unpack "$archive" "$LLAMA_DIR/$1"
}

llama_vulkan_prebuilt() { llama_fetch vulkan "$LLAMACPP_VULKAN_RELEASES" "bin-ubuntu-vulkan-x64.tar.gz"; }

llama_hip_prebuilt() { # lemonade-sdk ships one zip per gfx family: gfx1151, gfx120X, ...
  local fam=$GPU_ARCH
  [[ $fam == gfx120* ]] && fam=gfx120X
  llama_fetch hip "$LLAMACPP_RELEASE" "ubuntu-rocm-$fam-x64.zip"
}

llama_source() { # llama_source hip|vulkan
  local kind=$1 src="$LLAMA_DIR/src-$1" flags
  [ -d "$src/.git" ] || git clone --depth 1 https://github.com/ggml-org/llama.cpp "$src" ||
    return 1
  if [ "$kind" = hip ]; then
    command -v hipconfig >/dev/null || { warn "hipconfig not found (no ROCm dev tools)"; return 1; }
    flags=(-DGGML_HIP=ON "-DAMDGPU_TARGETS=$GPU_ARCH")
    export HIPCXX="$(hipconfig -l)/clang" HIP_PATH="$(hipconfig -R)"
  else
    command -v glslc >/dev/null || { warn "glslc not found (needs libvulkan-dev and glslc)"; return 1; }
    flags=(-DGGML_VULKAN=ON)
  fi
  command -v cmake >/dev/null || uv pip install --python "$VENV/bin/python" cmake ninja || return 1
  export PATH="$VENV/bin:$PATH"
  echo "building llama-server ($kind, $GPU_ARCH; log: $LOGS/llama-build-$kind.log)"
  if ! (cmake -S "$src" -B "$src/build" "${flags[@]}" -DCMAKE_BUILD_TYPE=Release \
    -DLLAMA_CURL=OFF &&
    cmake --build "$src/build" --config Release -j "$(nproc)" --target llama-server) \
    >"$LOGS/llama-build-$kind.log" 2>&1; then
    tail -n 30 "$LOGS/llama-build-$kind.log"
    return 1
  fi
  LLAMA_BIN="$src/build/bin/llama-server"
  llama_ok "$LLAMA_BIN"
}
llama_vulkan_source() { llama_source vulkan; }
llama_hip_source() { llama_source hip; }

install_llamacpp() {
  if [ -s "$LLAMA_DIR/bin.ok" ] && [ -x "$(cat "$LLAMA_DIR/bin.ok")" ]; then
    LLAMA_BIN=$(cat "$LLAMA_DIR/bin.ok")
    log "llama.cpp already installed: $LLAMA_BIN"
    return 0
  fi
  mkdir -p "$LLAMA_DIR"
  local order variant=$AMD_LLAMA_VARIANT m
  if [ "$variant" = auto ]; then
    if [ "$GPU_ARCH" = gfx1151 ]; then variant=vulkan; else variant=hip; fi
  fi
  case "$variant" in
    vulkan) order="llama_vulkan_prebuilt llama_hip_prebuilt llama_vulkan_source llama_hip_source" ;;
    hip) order="llama_hip_prebuilt llama_hip_source llama_vulkan_prebuilt llama_vulkan_source" ;;
    *) die "AMD_LLAMA_VARIANT must be auto, vulkan or hip" ;;
  esac
  log "llama.cpp install: GPU_ARCH=$GPU_ARCH, first choice $variant, hipconfig: $(command -v hipconfig || echo no), glslc: $(command -v glslc || echo no)"
  for m in $order; do
    log "llama.cpp install: trying $m"
    if "$m"; then
      echo "$LLAMA_BIN" >"$LLAMA_DIR/bin.ok"
      log "llama.cpp OK via $m: $LLAMA_BIN"
      return 0
    fi
    warn "$m did not produce a llama-server that sees the GPU"
  done
  return 1
}

# Download the GGUF in the background (nohup, resumable) so a short session can disconnect.
# Re-running this script re-attaches: an unfinished download resumes, a finished one is reused.
download_gguf() { # sets GGUF to the local path
  local pid="$AMD_WORK/download.pid" dl="$LOGS/download.log"
  if ! alive "$pid"; then
    log "Downloading $AMD_GGUF_REPO/$AMD_GGUF_FILE (about 12 GB, resumes; progress: $dl)"
    spawn "$pid" "$dl" "$VENV/bin/python" -c 'import sys
from huggingface_hub import hf_hub_download as d
print("GGUF=" + d(sys.argv[1], sys.argv[2]))' "$AMD_GGUF_REPO" "$AMD_GGUF_FILE"
  fi
  while alive "$pid"; do sleep 5; done
  GGUF=$(sed -n 's/^GGUF=//p' "$dl" | tail -n 1)
  if [ -z "$GGUF" ] || [ ! -s "$GGUF" ]; then
    tail -c 400 "$dl" | tr '\r' '\n' | tail -n 3
    return 1
  fi
  rm -f "$pid"
}

serve_llamacpp() {
  install_llamacpp || return 1
  local GGUF help extra=()
  download_gguf || return 1
  echo "$GGUF"
  LD_LIBRARY_PATH="${LLAMA_BIN%/*}:${LD_LIBRARY_PATH:-}"
  export LD_LIBRARY_PATH
  help=$("$LLAMA_BIN" --help 2>&1 || true)
  # gpt-oss: keep reasoning short so the JSON answer fits in max_tokens.
  if [[ $help == *--chat-template-kwargs* ]]; then
    extra=(--chat-template-kwargs '{"reasoning_effort": "low"}')
  fi
  log "Starting llama-server on $LOCAL (4 parallel slots of $AMD_MAX_MODEL_LEN tokens)"
  # LLAMA_API_KEY is llama-server's env form of --api-key; keeps the key off the command line.
  LLAMA_API_KEY="$AMD_API_KEY" spawn "$AMD_WORK/server.pid" "$LOGS/server.log" "$LLAMA_BIN" \
    -m "$GGUF" --host 127.0.0.1 --port "$AMD_PORT" --alias "$AMD_MODEL" -ngl 999 \
    -c $((AMD_MAX_MODEL_LEN * 4)) --parallel 4 --jinja --metrics "${extra[@]}"
  echo "llama.cpp" >"$AMD_WORK/backend"
  wait_healthy "$AMD_WORK/server.pid" "$LOGS/server.log" || return 1
  smoke || warn "json_schema failed on llama.cpp; set structured: json_object for the amd-* entry"
}

# --- stats and tunnel ------------------------------------------------------------------------

stats_loop() { # runs detached via `serve.sh _stats`; appends one sample per interval
  local re='^(vllm:(num_requests_(running|waiting)|prompt_tokens_total|generation_tokens_total'
  re+='|e2e_request_latency_seconds_(sum|count)|time_to_first_token_seconds_(sum|count)'
  re+='|(gpu|kv)_cache_usage_perc|request_success_total)'
  re+='|llamacpp:(prompt_tokens_total|tokens_predicted_total|prompt_tokens_seconds'
  re+='|predicted_tokens_seconds|requests_processing|requests_deferred|n_decode_total))'
  while true; do
    {
      echo "=== $(date '+%Y-%m-%dT%H:%M:%S%z') backend=$(cat "$AMD_WORK/backend" 2>/dev/null)"
      authcurl -s -m 5 "$LOCAL/metrics" | grep -E "$re" || true
      if command -v rocm-smi >/dev/null; then
        rocm-smi --showuse --showmemuse --showpower 2>/dev/null | grep -E '^(GPU|card)' || true
      elif command -v amd-smi >/dev/null; then
        amd-smi metric --usage --mem-usage --power 2>/dev/null | head -n 20 || true
      fi
    } >>"$LOGS/stats.log"
    sleep "$AMD_STATS_INTERVAL"
  done
}

start_stats() { # the logger takes AMD_API_KEY from its env; cmd_up restarts it with the server
  alive "$AMD_WORK/stats.pid" && return 0
  spawn "$AMD_WORK/stats.pid" /dev/null bash "$0" _stats
  log "Serving stats every ${AMD_STATS_INTERVAL}s -> $LOGS/stats.log (vLLM also logs throughput to $LOGS/server.log)"
}

validate_tunnel_protocol() {
  case "$AMD_TUNNEL" in
    1 | 0 | none) ;;
    *) die "AMD_TUNNEL must be none, 0 or 1" ;;
  esac
  case "$AMD_TUNNEL_PROTOCOL" in
    http2 | quic | auto) ;;
    *) die "AMD_TUNNEL_PROTOCOL must be http2, quic or auto" ;;
  esac
}

start_tunnel() {
  local cf="$AMD_WORK/bin/cloudflared" url="" code registered=0
  validate_tunnel_protocol
  if [ ! -x "$cf" ]; then
    log "Downloading cloudflared"
    curl -fL --retry 3 -o "$cf.tmp" "$CLOUDFLARED_URL" && chmod +x "$cf.tmp" && mv "$cf.tmp" "$cf"
  fi
  "$cf" --version
  if alive "$AMD_WORK/tunnel.pid" && [ -s "$AMD_WORK/public_url" ] &&
    grep -q 'Registered tunnel connection' "$LOGS/tunnel.log" 2>/dev/null; then
    url=$(cat "$AMD_WORK/public_url")
    log "Tunnel already running: $url"
  else
    kill_pidfile "$AMD_WORK/tunnel.pid"
    rm -f "$AMD_WORK/public_url"
    log "Opening a cloudflared quick tunnel to $LOCAL"
    spawn "$AMD_WORK/tunnel.pid" "$LOGS/tunnel.log" "$cf" tunnel --no-autoupdate \
      --protocol "$AMD_TUNNEL_PROTOCOL" --url "$LOCAL"
    for _ in $(seq 30); do
      url=$(grep -oE 'https://[-a-z0-9]+\.trycloudflare\.com' "$LOGS/tunnel.log" | head -1 || true)
      if grep -q 'Registered tunnel connection' "$LOGS/tunnel.log"; then
        registered=1
        [ -n "$url" ] && break
      fi
      alive "$AMD_WORK/tunnel.pid" || break
      sleep 1
    done
    if [ "$registered" != 1 ] || [ -z "$url" ]; then
      kill_pidfile "$AMD_WORK/tunnel.pid"
      rm -f "$AMD_WORK/public_url"
      tail -n 20 "$LOGS/tunnel.log" >&2
      die "cloudflared did not register a tunnel with a URL within 30s. UDP/QUIC may be blocked; keep AMD_TUNNEL_PROTOCOL=http2 and re-run serve.sh"
    fi
    echo "$url" >"$AMD_WORK/public_url"
  fi
  echo "checking $url/v1/models from the public side (DNS can take a minute)"
  for _ in $(seq 30); do
    code=$(authcurl -s -o /dev/null -w '%{http_code}' -m 10 "$url/v1/models" || true)
    [ "$code" = 200 ] && { echo "public endpoint OK"; return 0; }
    sleep 4
  done
  kill_pidfile "$AMD_WORK/tunnel.pid"
  rm -f "$AMD_WORK/public_url"
  die "public URL did not return HTTP 200 from /v1/models (last result: ${code:-000}); check $LOGS/tunnel.log and re-run serve.sh"
}

summary() {
  log "Ready. Backend: $(cat "$AMD_WORK/backend")"
  local BASE_VAR=AMD_VLLM_BASE_URL MODEL_GLOB=amd-gpt-oss-20b SELFPLAY_CFG=configs/selfplay_amd.yaml
  if [ "$GPU_ARCH" = gfx1151 ] && [ "$(cat "$AMD_WORK/backend")" = llama.cpp ]; then
    BASE_VAR=AMD_LLAMACPP_BASE_URL MODEL_GLOB=amd-strix-gpt-oss-20b
    SELFPLAY_CFG=configs/selfplay_amd_strix.yaml
  fi
  if [ "$AMD_TUNNEL" != 1 ]; then
    cat <<EOF
----------------------------------------------------------------------------
Local mode, no tunnel. Run societysim here in this pod, from a shell with
the same AMD_API_KEY exported (this script never prints the key):

  cd $REPO
  uv sync --frozen
  export $BASE_VAR=$LOCAL/v1
  uv run societysim doctor --model '$MODEL_GLOB'
  uv run societysim play --config $SELFPLAY_CFG --agents llm

Or doctor, N games and a tarball of runs/<batch> to download, in one go:

  bash infra/amd/run_on_box.sh $SELFPLAY_CFG 3

Logs:  $LOGS/server.log  (server)   $LOGS/stats.log  (stats)
Stop:  bash $0 stop
----------------------------------------------------------------------------
EOF
    return 0
  fi
  cat <<EOF
----------------------------------------------------------------------------
Put this line in .env on the machine that runs societysim, next to the same
AMD_API_KEY you exported here (this script never prints the key):

AMD_VLLM_BASE_URL=$(cat "$AMD_WORK/public_url")/v1

Then:  uv run societysim doctor --model 'amd-*'
Logs:  $LOGS/server.log  (server)   $LOGS/stats.log  (stats)
Stop:  bash $0 stop
----------------------------------------------------------------------------
The quick-tunnel URL changes every time the tunnel restarts.
EOF
}

# --- commands --------------------------------------------------------------------------------

cmd_up() {
  touch "$LOGS/serve.log" && chmod 600 "$LOGS"/*.log
  exec > >(tee -a "$LOGS/serve.log") 2>&1
  log "serve.sh up: model=$AMD_MODEL backend=$AMD_BACKEND work=$AMD_WORK"
  validate_tunnel_protocol
  api_key
  preflight
  base_venv
  if [ "$KEY_CHANGED" = 0 ] && alive "$AMD_WORK/server.pid" && healthy; then
    log "Server already running ($(cat "$AMD_WORK/backend")); reusing it"
  else
    [ "$KEY_CHANGED" = 1 ] && log "API key changed; restarting the server and stats logger"
    kill_pidfile "$AMD_WORK/server.pid"
    kill_pidfile "$AMD_WORK/stats.pid" # it holds the old key
    local served=0
    case "$AMD_BACKEND" in
      auto | vllm)
        if serve_vllm; then
          served=1
        elif [ "$AMD_BACKEND" = vllm ]; then
          die "vLLM failed and AMD_BACKEND=vllm; see $LOGS"
        else
          warn "vLLM cannot run here; falling back to llama.cpp (same URL, same key)"
          kill_pidfile "$AMD_WORK/server.pid"
        fi
        ;;
      llamacpp) ;;
      *) die "AMD_BACKEND must be auto, vllm or llamacpp" ;;
    esac
    if [ "$served" = 0 ]; then
      serve_llamacpp || die "llama.cpp fallback failed too; see $LOGS"
    fi
  fi
  start_stats
  [ "$AMD_TUNNEL" = 1 ] && start_tunnel
  summary
}

cmd_status() {
  local f
  for f in server tunnel stats; do
    printf '%-7s %s\n' "$f" "$(alive "$AMD_WORK/$f.pid" && echo "running (pid $(cat "$AMD_WORK/$f.pid"))" || echo stopped)"
  done
  echo "backend $(cat "$AMD_WORK/backend" 2>/dev/null || echo none)"
  echo "health  $(healthy && echo ok || echo down)"
  echo "registered $(alive "$AMD_WORK/tunnel.pid" && grep -q 'Registered tunnel connection' "$LOGS/tunnel.log" 2>/dev/null && echo yes || echo no)"
  echo "url     $([ -s "$AMD_WORK/public_url" ] && echo "$(cat "$AMD_WORK/public_url")/v1" || echo "$LOCAL/v1 (local only)")"
  [ -f "$LOGS/stats.log" ] && { echo "--- latest stats"; tail -n 20 "$LOGS/stats.log"; }
  return 0
}

cmd_stop() {
  log "Stopping tunnel, stats logger and server"
  kill_pidfile "$AMD_WORK/tunnel.pid"
  kill_pidfile "$AMD_WORK/stats.pid"
  kill_pidfile "$AMD_WORK/server.pid"
  rm -f "$AMD_WORK/public_url"
  if [ -s "$AMD_WORK/started_at" ]; then
    awk -v s="$(cat "$AMD_WORK/started_at")" -v n="$(date +%s)" -v p="$AMD_PRICE_PER_HOUR" \
      'BEGIN { h = (n - s) / 3600; printf "served %.2f h; at $%s/h that is $%.2f\n", h, p, h * p }' |
      tee -a "$LOGS/stats.log"
    rm -f "$AMD_WORK/started_at"
  fi
  echo "stopped"
}

# Offline regression check: a quick tunnel may print a URL without ever registering.
test_tunnel_never_registers() (
  AMD_WORK=$(mktemp -d)
  AMD_TUNNEL_PROTOCOL=http2
  trap 'rm -rf "$AMD_WORK"' EXIT
  LOGS="$AMD_WORK/logs"
  mkdir -p "$AMD_WORK/bin" "$LOGS"
  printf '#!/usr/bin/env bash\necho mock-cloudflared\n' >"$AMD_WORK/bin/cloudflared"
  chmod +x "$AMD_WORK/bin/cloudflared"
  spawn() {
    [[ " $* " == *" --protocol http2 "* ]] || die "mock: expected HTTP/2"
    echo 'https://mock.trycloudflare.com' >"$2"
    echo 123 >"$1"
  }
  alive() { return 1; }
  kill_pidfile() { rm -f "$1"; }
  sleep() { :; }
  authcurl() { die "mock: public check ran before registration"; }
  if (start_tunnel) >"$AMD_WORK/output" 2>&1; then
    die "mock: a URL without registration was accepted"
  fi
  grep -q 'did not register a tunnel' "$AMD_WORK/output" || die "mock: missing registration error"
  if grep -q 'checking https://mock.trycloudflare.com\|AMD_VLLM_BASE_URL=' "$AMD_WORK/output"; then
    die "mock: unregistered URL was offered for use"
  fi
  grep -q 'https://mock.trycloudflare.com' "$LOGS/tunnel.log" || die "mock: missing URL in log"
  [ ! -e "$AMD_WORK/public_url" ] || die "mock: public URL was saved"
  [ ! -e "$AMD_WORK/tunnel.pid" ] || die "mock: tunnel pid was kept"
  echo "tunnel registration mock: passed"
)

if [ "${1:-}" = --backend ]; then
  [ -n "${2:-}" ] || die "usage: $0 --backend auto|vllm|llamacpp"
  AMD_BACKEND=$2
  shift 2
fi

case "${1:-up}" in
  up) cmd_up ;;
  status) cmd_status ;;
  stop) cmd_stop ;;
  _stats) stats_loop ;;
  _test_tunnel) test_tunnel_never_registers ;;
  *) die "usage: $0 [up|status|stop]" ;;
esac
