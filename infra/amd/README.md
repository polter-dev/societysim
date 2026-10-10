# AMD GPU server (T6.3)

`serve.sh` serves gpt-oss-20b from the AMD Radeon AI PRO R9700 (32 GB, gfx1201) on AUP Learning Cloud as an OpenAI-compatible endpoint on `127.0.0.1:8000`. The games run in the same pod, against that local endpoint. The `amd-gpt-oss-20b` entry in `configs/models.yaml` points at it through `AMD_VLLM_BASE_URL`.

Laptops can't reach the pod: AUP pods block Cloudflare quick tunnels (QUIC times out, and TLS to the edge on port 7844 is reset, see #114). Outbound HTTPS works, so the pod can still call OpenAI, Gemini and Hugging Face, and mixed batches run there too. The tunnel is kept as an option for environments that allow it (see [Optional: tunnel to a laptop](#optional-tunnel-to-a-laptop)).

What `serve.sh` does, in order:

1. Creates `~/amd-serve/venv` with `uv venv --system-site-packages`, so it sees the system ROCm torch.
2. Installs vLLM. It tries AMD's gfx120X wheel on the system torch, then the same wheel with the torch it was built for, then AMD's Python 3.13 stack, then a source build. Each attempt has to pass a GPU check before it counts.
3. Downloads the model and starts vLLM on `127.0.0.1:8000` with an API key and json_schema guided decoding (xgrammar, retried with outlines if the smoke test fails).
4. If vLLM can't run on this GPU, it serves the same model with llama.cpp `llama-server` built for gfx120X, on the same port with the same key.
5. Logs serving stats every minute to `~/amd-serve/logs/stats.log`.
6. Prints the exact commands to run societysim in the pod. Inside a JupyterHub pod (AUP) it opens no tunnel. Elsewhere, or with `AMD_TUNNEL=1`, it opens one (see below).

Re-running it is safe. Finished steps are skipped and a healthy server is reused.

## Strix Halo (Radeon 8060S) with llama.cpp

The R9700 pod is gone. The AUP cloud now offers the Radeon 8060S (Strix Halo iGPU, gfx1151, 64 GB unified memory) and the 890M (4 GB, too small for the model). vLLM on ROCm is shaky on gfx1151, so for this chip `serve.sh` runs gpt-oss-20b through llama.cpp's `llama-server` (OpenAI-compatible, same port 8000, same API key). It runs at $0/h: an AI player on a laptop-class chip.

1. In AUP: **Start My Server → Custom Repos → Basic GPU Environment**. Under Configuration pick **8060S, not Auto** (Auto can hand you the 890M). Set the runtime to the maximum. Launch.
2. Make the API key as described below if you haven't. In a Terminal (clone on the first run, then `git pull`):

   ```bash
   cd /opt/workspace && (git clone https://github.com/polter-dev/societysim.git || true) && cd societysim && git pull
   read -rs AMD_API_KEY && export AMD_API_KEY      # not echoed, not in history
   bash infra/amd/serve.sh --backend llamacpp
   bash infra/amd/run_on_box.sh configs/selfplay_amd_strix.yaml 3
   ```

What `--backend llamacpp` does: it skips vLLM and gets llama.cpp, preferring the prebuilt Vulkan release from ggml-org/llama.cpp (about 30 MB; `AMD_LLAMA_VARIANT=hip` tries the ROCm build first). If that binary can't see the GPU it tries the other prebuilt, then a source build (`-DGGML_VULKAN=ON`, or `-DGGML_HIP=ON -DAMDGPU_TARGETS=gfx1151` when ROCm dev tools exist). The log says which one it used. Then it downloads `ggml-org/gpt-oss-20b-GGUF` / `gpt-oss-20b-MXFP4.gguf` and starts `llama-server` with `--jinja -ngl 999 --parallel 4 -c 65536` (4 slots of 16k) and low reasoning effort.

- Download: about 12 GB (MXFP4), roughly 5 to 20 minutes depending on the pod's network. It runs under `nohup` and logs progress to `~/amd-serve/logs/download.log` (`tail -f` it). Closing the terminal does not stop it.
- Resume after a disconnect: export `AMD_API_KEY` again and re-run `bash infra/amd/serve.sh --backend llamacpp`. A running download is re-attached, a partial one resumes, a finished file is reused, and a healthy server is reused too.
- `bash infra/amd/serve.sh status` shows health. `bash infra/amd/serve.sh stop` stops everything and prints the served hours at $0/h.
- `configs/selfplay_amd_strix.yaml` uses the `amd-strix-gpt-oss-20b` entry, which reads `AMD_LLAMACPP_BASE_URL` (default `http://127.0.0.1:8000/v1`) and `AMD_API_KEY`. `run_on_box.sh` runs `doctor` before the games.

## Make an API key (once)

On your laptop:

```bash
openssl rand -hex 32
```

Keep it in your laptop's `.env` as `AMD_API_KEY=<that value>` or in a password manager. The server requires it, and `serve.sh` refuses to start without a key of at least 32 characters. The scripts never print, log or store the key; `serve.sh` keeps only a hash so it can tell when the key changes.

## Running on the AMD box

1. Go to AUP Learning Cloud, then **Start My Server → Custom Repos → Basic GPU Environment**. Pick the R9700 system under Configuration and set the runtime to at least 3 hours. The first run downloads several GB of wheels and about 14 GB of weights to the NFS home, which is slow. Then **Launch Server**.
2. Open a Terminal. The first time, clone the repo (ours lives at `/opt/workspace/societysim`); after that, pull:

   ```bash
   cd /opt/workspace
   git clone https://github.com/polter-dev/societysim.git   # first time only
   cd societysim && git pull
   read -rs AMD_API_KEY && export AMD_API_KEY   # paste the key, press Enter (not echoed, not in history)
   bash infra/amd/serve.sh
   ```

   To test a branch that isn't merged yet, `git checkout <branch>` before `serve.sh`.

3. Wait for the box that starts with `Local mode, no tunnel`. It prints the next steps:

   ```bash
   cd /opt/workspace/societysim
   uv sync --frozen
   export AMD_VLLM_BASE_URL=http://127.0.0.1:8000/v1
   uv run societysim doctor --model amd-gpt-oss-20b
   uv run societysim play --config configs/selfplay_amd.yaml --agents llm
   ```

   `doctor` should print `amd-gpt-oss-20b: ok` with json_schema marked ok. If it prints `WORKS ONLY WITH json_object`, change `structured:` for that entry in `configs/models.yaml` to `json_object` and say so in a PR.

   The server only listens on `127.0.0.1`, so use that address, not a hostname that might resolve to IPv6 first.

4. Or do doctor, the games and the packing in one go with `run_on_box.sh`:

   ```bash
   bash infra/amd/run_on_box.sh configs/selfplay_amd.yaml 3   # doctor, 3 self-play games (seeds 0-2), tarball
   ```

   With a game config it plays N games (default 1; set `SEED` to start elsewhere) into `runs/<config name>/`. With a batch config (one with `batch_id:`) it runs `societysim batch`, which takes the game count from the config and resumes a half-done batch. It runs `doctor` on every model in the config first and stops if one fails. It sets `AMD_VLLM_BASE_URL=http://127.0.0.1:8000/v1` unless you export another value.

The server and stats logger keep running if you close the terminal tab. They stop when the session's runtime ends. In a new terminal, run `read -rs AMD_API_KEY && export AMD_API_KEY` again before `serve.sh` or the games. If you change the key, export the new one and run `serve.sh` again; it restarts the server and the stats logger with the new key.

Useful while it runs:

```bash
bash infra/amd/serve.sh status          # what's running, the URL, latest stats
tail -f ~/amd-serve/logs/server.log     # vLLM / llama-server log, with throughput lines
tail -f ~/amd-serve/logs/stats.log      # sampled /metrics and GPU use
```

### Keys for mixed batches

A mixed batch also calls OpenAI, Gemini or Hugging Face, so the pod needs those keys too. The safest way writes nothing to disk. Export each one in the terminal that runs the batch:

```bash
read -rs OPENAI_API_KEY && export OPENAI_API_KEY
read -rs GEMINI_API_KEY && export GEMINI_API_KEY
read -rs HF_TOKEN && export HF_TOKEN
bash infra/amd/run_on_box.sh configs/mixed_v1.yaml
```

If you'd rather use a file, make a pod-local `.env` that only you can read, and delete it when the run is done:

```bash
cd /opt/workspace/societysim
(umask 077 && touch .env) && chmod 600 .env
nano .env                    # OPENAI_API_KEY=..., GEMINI_API_KEY=..., HF_TOKEN=..., AMD_API_KEY=...
bash infra/amd/run_on_box.sh configs/mixed_v1.yaml
rm -f .env                   # when you're done
```

**Warning:** the home directory on AUP is shared NFS storage. Files there outlive the session and sit on storage that other people administer. Don't leave keys in it: keep `.env` at mode 600, delete it after the run, and never commit it (it's in `.gitignore`). An exported variable wins over the same name in `.env`.

To put the AMD model in a mixed batch, list `amd-gpt-oss-20b` under `models:` in the batch config.

### Getting the runs off the box

`run_on_box.sh` packs `runs/<batch>/` into `runs/<batch>.tar.gz` and prints its full path. To download it, open the Jupyter file browser (left sidebar), go to `runs/`, right-click the `.tar.gz` and choose **Download**. If the file browser can't reach the repo folder, copy the tarball to your home first (`cp runs/<batch>.tar.gz ~/`).

On your laptop, unpack it into the repo and carry on as usual:

```bash
tar -xzf <batch>.tar.gz -C runs/
uv run societysim label runs/<batch>
uv run societysim report runs/<batch>
```

`PUBLISH=1 bash infra/amd/run_on_box.sh ...` also runs `societysim publish runs/<batch>` from the pod once that command exists (T3.5, #21). Until then it says so and skips it.

## Stopping

```bash
bash infra/amd/serve.sh stop
```

This stops the server, stats logger and any tunnel, and prints how many hours it served. Then stop the server in the hub too (**Control Panel → Stop My Server**) so it stops using quota. Weights and venvs stay in `~/amd-serve`, so the next session starts much faster. Delete any pod-local `.env` before you go.

## Cost

The AMD quota is in minutes, not dollars, so `AMD_PRICE_PER_HOUR` is 0 and the model entry has zero token prices. For a dollar comparison, `$/game = hours × AMD_PRICE_PER_HOUR` with an imputed rate (for example what a 32 GB cloud GPU rents for). Say in the report that the rate is imputed. `serve.sh stop` prints the served hours; run it with `AMD_PRICE_PER_HOUR=<rate>` to get the dollar figure too.

## Options

Set these before `bash infra/amd/serve.sh`:

| Variable | Default | Use |
|---|---|---|
| `AMD_BACKEND` | `auto` | `vllm` to fail instead of falling back, `llamacpp` to skip vLLM |
| `AMD_MODEL` | `openai/gpt-oss-20b` | Another HF model for vLLM. Also add a matching `amd-*` entry with `litellm_model: openai/<that id>` (for example `openai/Qwen/Qwen3-8B`) and the same `api_base` and `api_key_env` as `amd-gpt-oss-20b`. It has to fit in 32 GB with room for the KV cache: Qwen3-32B in BF16 (about 64 GB) does not, Qwen3-8B or an AWQ/FP8 14B does. |
| `AMD_LLAMA_VARIANT` | `auto` | llama.cpp build to try first: `vulkan` (auto on gfx1151) or `hip` |
| `AMD_GGUF_REPO`, `AMD_GGUF_FILE` | `ggml-org/gpt-oss-20b-GGUF`, `gpt-oss-20b-MXFP4.gguf` | Model for the llama.cpp fallback |
| `AMD_TUNNEL` | `none` in JupyterHub (AUP), `1` elsewhere | `none` (or `0`) for local mode, `1` to open a cloudflared quick tunnel |
| `AMD_TUNNEL_PROTOCOL` | `http2` | Tunnel transport: `http2`, `quic`, or `auto`. Use `http2` when outbound UDP is blocked. |
| `AMD_WORK` | `~/amd-serve` | Venvs, weights, logs |

If vLLM failed once, later runs skip it and go straight to llama.cpp. Delete `~/amd-serve/vllm.failed` to try vLLM again.

## Optional: tunnel to a laptop

Where outbound tunnels work (not on AUP today), `AMD_TUNNEL=1 bash infra/amd/serve.sh` starts a cloudflared quick tunnel over HTTP/2, waits for it to register, and checks `/v1/models` through the public URL. Then it prints a line like this for your laptop's `.env`, next to `AMD_API_KEY`:

```
AMD_VLLM_BASE_URL=https://<random-words>.trycloudflare.com/v1
```

The URL changes every time the tunnel restarts. On the laptop, run the same `doctor` and `play` commands as above.

An HTTP 530 or 000 from the public URL means the tunnel is not registered. Re-run `AMD_TUNNEL=1 bash infra/amd/serve.sh`, wait for the public endpoint check to pass, then replace `AMD_VLLM_BASE_URL` in your laptop's `.env` with the new URL. `bash infra/amd/serve.sh status` shows local health, tunnel registration and the current URL. If registration fails, inspect `~/amd-serve/logs/tunnel.log`; leave `AMD_TUNNEL_PROTOCOL=http2` when the pod blocks outbound UDP. If it still fails, use local mode.

## How models.yaml reaches the server

The `amd-gpt-oss-20b` entry uses `litellm_model: openai/openai/gpt-oss-20b` with `api_base: ${AMD_VLLM_BASE_URL}` and `api_key_env: AMD_API_KEY`. Those two fields are per model, so only the amd-* calls use the AMD URL and key. The OpenAI models keep their default endpoint. It works the same against vLLM and llama-server.

The prefix is doubled on purpose. LiteLLM's `openai/` route strips the first segment and sends the rest as the model id, and `serve.sh` serves the model under its HF id (`--served-model-name $AMD_MODEL` for vLLM, `--alias $AMD_MODEL` for llama.cpp), so `/v1/models` lists `openai/gpt-oss-20b`. With a single prefix every call sends `gpt-oss-20b` and gets a 404.

`doctor`, `play` and `batch` call `GET {api_base}/models` first and stop with a clear message if the id they would send isn't listed. If that happens, check what the server lists:

```bash
curl -s -H "Authorization: Bearer $AMD_API_KEY" "$AMD_VLLM_BASE_URL/models"
```

Then either set `litellm_model` to `openai/<listed id>` or restart `serve.sh` so it serves `$AMD_MODEL` again. A server started by any earlier `serve.sh` already lists `openai/gpt-oss-20b`, so pulling this change needs no restart.

## Reasoning effort and speed

The `amd-gpt-oss-20b` entry sets `reasoning_effort: low` and `max_tokens: 2048` (#151). LiteLLM's `openai/` route sends `reasoning_effort` as a top-level request field, and vLLM applies it to gpt-oss. Pulling this change needs no server restart.

Baseline before the change, at gpt-oss's default (medium) effort: vLLM generated about 33 tokens/s with one request running, and after about 15 minutes the game log had only 32 events (a full game is 300 to 400). Most of each turn went to reasoning tokens. At 33 tokens/s the 2048-token cap bounds a runaway turn at about a minute.
