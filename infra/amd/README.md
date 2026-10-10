# AMD GPU server (T6.3)

`serve.sh` serves gpt-oss-20b from the AMD Radeon AI PRO R9700 (32 GB, gfx1201) on AUP Learning Cloud as an OpenAI-compatible endpoint, then opens a cloudflared quick tunnel so a laptop can call it. The `amd-gpt-oss-20b` entry in `configs/models.yaml` points at that tunnel.

What the script does, in order:

1. Creates `~/amd-serve/venv` with `uv venv --system-site-packages`, so it sees the system ROCm torch.
2. Installs vLLM. It tries AMD's gfx120X wheel on the system torch, then the same wheel with the torch it was built for, then AMD's Python 3.13 stack, then a source build. Each attempt has to pass a GPU check before it counts.
3. Downloads the model and starts vLLM on `127.0.0.1:8000` with an API key and json_schema guided decoding (xgrammar, retried with outlines if the smoke test fails).
4. If vLLM can't run on this GPU, it serves the same model with llama.cpp `llama-server` built for gfx120X, on the same port with the same key.
5. Logs serving stats every minute to `~/amd-serve/logs/stats.log`.
6. Starts the tunnel over HTTP/2, waits for registration, and checks `/v1/models` through the public URL before printing the `.env` line.

Re-running it is safe. Finished steps are skipped and a healthy server is reused.

## First, on your laptop: make an API key

```bash
openssl rand -hex 32
```

Put it in `.env` in the repo root as `AMD_API_KEY=<that value>`. The server requires it, and `serve.sh` refuses to start without a key of at least 32 characters. The script never prints, logs or stores the key; it keeps only a hash so it can tell when the key changes.

## On the AMD box

1. Go to AUP Learning Cloud, then **Start My Server → Custom Repos → Basic GPU Environment**. Pick the R9700 system under Configuration and set the runtime to at least 3 hours. The first run downloads several GB of wheels and about 14 GB of weights to the NFS home, which is slow. Then **Launch Server**.
2. Open a Terminal and run:

   ```bash
   git clone https://github.com/polter-dev/societysim.git
   cd societysim
   read -rs AMD_API_KEY && export AMD_API_KEY   # paste the same key, press Enter (not echoed, not in history)
   bash infra/amd/serve.sh
   ```

   To test a branch that isn't merged yet, add `-b <branch>` to the `git clone`.

3. Wait for the box that starts with `Put this line in .env`. It looks like this:

   ```
   AMD_VLLM_BASE_URL=https://<random-words>.trycloudflare.com/v1
   ```

If you change the key, export the new one and run `serve.sh` again. It restarts the server and the stats logger with the new key.

The server, tunnel and stats logger keep running if you close the terminal tab. They stop when the session's runtime ends. In a new terminal, run `read -rs AMD_API_KEY && export AMD_API_KEY` again before `serve.sh`.

Useful while it runs:

```bash
bash infra/amd/serve.sh status          # what's running, the URL, latest stats
tail -f ~/amd-serve/logs/server.log     # vLLM / llama-server log, with throughput lines
tail -f ~/amd-serve/logs/stats.log      # sampled /metrics and GPU use
```

## On your laptop

Put the printed `AMD_VLLM_BASE_URL` line in `.env` next to `AMD_API_KEY`, replacing any older URL. The URL changes every time the tunnel restarts. Then:

```bash
uv run societysim doctor --model 'amd-*'
uv run societysim play --config configs/selfplay_amd.yaml --agents llm
```

`doctor` should print `amd-gpt-oss-20b: ok` with json_schema marked ok. If it prints `WORKS ONLY WITH json_object`, change `structured:` for that entry in `configs/models.yaml` to `json_object` and say so in the PR. The self-play game writes `runs/selfplay_amd/selfplay_amd-s0.jsonl`.

## Stopping

```bash
bash infra/amd/serve.sh stop
```

This stops the tunnel, stats logger and server and prints how many hours it served. Then stop the server in the hub too (**Control Panel → Stop My Server**) so it stops using quota. Weights and venvs stay in `~/amd-serve`, so the next session starts much faster.

## Cost

The AMD quota is in minutes, not dollars, so `AMD_PRICE_PER_HOUR` is 0 and the model entry has zero token prices. For a dollar comparison, `$/game = hours × AMD_PRICE_PER_HOUR` with an imputed rate (for example what a 32 GB cloud GPU rents for). Say in the report that the rate is imputed. `serve.sh stop` prints the served hours; run it with `AMD_PRICE_PER_HOUR=<rate>` to get the dollar figure too.

## Options

Set these before `bash infra/amd/serve.sh`:

| Variable | Default | Use |
|---|---|---|
| `AMD_BACKEND` | `auto` | `vllm` to fail instead of falling back, `llamacpp` to skip vLLM |
| `AMD_MODEL` | `openai/gpt-oss-20b` | Another HF model for vLLM. Also add a matching `amd-*` entry with `litellm_model: openai/<that id>` and the same `api_base` and `api_key_env` as `amd-gpt-oss-20b`. It has to fit in 32 GB with room for the KV cache: Qwen3-32B in BF16 (about 64 GB) does not, Qwen3-8B or an AWQ/FP8 14B does. |
| `AMD_GGUF_REPO`, `AMD_GGUF_FILE` | `ggml-org/gpt-oss-20b-GGUF`, `gpt-oss-20b-MXFP4.gguf` | Model for the llama.cpp fallback |
| `AMD_TUNNEL` | `1` | `0` for no tunnel (run the games on the box itself, see below) |
| `AMD_TUNNEL_PROTOCOL` | `http2` | Tunnel transport: `http2`, `quic`, or `auto`. Use `http2` when outbound UDP is blocked. |
| `AMD_WORK` | `~/amd-serve` | Venvs, weights, logs |

If vLLM failed once, later runs skip it and go straight to llama.cpp. Delete `~/amd-serve/vllm.failed` to try vLLM again.

To run the games on the box without the tunnel: `AMD_TUNNEL=0 bash infra/amd/serve.sh`, then in the repo run `uv sync --frozen`, put `AMD_VLLM_BASE_URL=http://127.0.0.1:8000/v1` and the key in `.env`, and use the same `doctor` and `play` commands.

## Troubleshooting

An HTTP 530 or 000 from the public URL means the tunnel is not registered. Re-run `bash infra/amd/serve.sh`, wait for the public endpoint check to pass, then replace `AMD_VLLM_BASE_URL` in your laptop's `.env` with the new URL. Run `bash infra/amd/serve.sh status` on the AMD box to check local health, tunnel registration, and the current URL. If registration fails, inspect `~/amd-serve/logs/tunnel.log`; leave `AMD_TUNNEL_PROTOCOL=http2` when the pod blocks outbound UDP.

## How models.yaml reaches the server

The `amd-gpt-oss-20b` entry uses `litellm_model: openai/gpt-oss-20b` with `api_base: ${AMD_VLLM_BASE_URL}` and `api_key_env: AMD_API_KEY`. Those two fields are per model, so only the amd-* calls use the tunnel URL and key. The OpenAI models keep their default endpoint. It works the same against vLLM and llama-server.
