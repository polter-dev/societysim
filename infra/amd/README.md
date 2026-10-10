# AMD GPU server (T6.3)

`serve.sh` serves gpt-oss-20b from the AMD Radeon AI PRO R9700 (32 GB, gfx1201) on AUP Learning Cloud as an OpenAI-compatible endpoint, then opens a cloudflared quick tunnel so a laptop can call it. The `amd-gpt-oss-20b` entry in `configs/models.yaml` points at that tunnel.

What the script does, in order:

1. Creates `~/amd-serve/venv` with `uv venv --system-site-packages`, so it sees the system ROCm torch.
2. Installs vLLM. It tries AMD's gfx120X wheel on the system torch, then the same wheel with the torch it was built for, then AMD's Python 3.13 stack, then a source build. Each attempt has to pass a GPU check before it counts.
3. Downloads the model and starts vLLM on `127.0.0.1:8000` with an API key and json_schema guided decoding (xgrammar, retried with outlines if the smoke test fails).
4. If vLLM can't run on this GPU, it serves the same model with llama.cpp `llama-server` built for gfx120X, on the same port with the same key.
5. Logs serving stats every minute to `~/amd-serve/logs/stats.log`.
6. Starts the tunnel and prints the two lines for `.env`.

Re-running it is safe. Finished steps are skipped and a healthy server is reused.

## On the AMD box

1. Go to AUP Learning Cloud, then **Start My Server → Custom Repos → Basic GPU Environment**. Pick the R9700 system under Configuration and set the runtime to at least 3 hours. The first run downloads several GB of wheels and about 14 GB of weights to the NFS home, which is slow. Then **Launch Server**.
2. Open a Terminal and run:

   ```bash
   git clone -b issue-41-amd-vllm https://github.com/polter-dev/societysim.git   # after merge: drop "-b issue-41-amd-vllm"
   cd societysim
   bash infra/amd/serve.sh
   ```

3. Wait for the box that starts with `Put these two lines in .env`. It looks like this:

   ```
   AMD_VLLM_BASE_URL=https://<random-words>.trycloudflare.com/v1
   AMD_API_KEY=amd-...
   ```

   The key is generated on the first run and saved in `~/amd-serve/api_key`. To choose your own key, run `AMD_API_KEY=... bash infra/amd/serve.sh` instead.

The server, tunnel and stats logger keep running if you close the terminal tab. They stop when the session's runtime ends.

Useful while it runs:

```bash
bash infra/amd/serve.sh status          # what's running, the URL, latest stats
tail -f ~/amd-serve/logs/server.log     # vLLM / llama-server log, with throughput lines
tail -f ~/amd-serve/logs/stats.log      # sampled /metrics and GPU use
```

## On your laptop

Put the two printed lines in `.env` in the repo root, replacing any older values. The URL changes every time the tunnel restarts. Then:

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
| `AMD_MODEL` | `openai/gpt-oss-20b` | Another HF model for vLLM. Also add a matching `amd-*` entry with `litellm_model: llamafile/<that id>`. It has to fit in 32 GB with room for the KV cache: Qwen3-32B in BF16 (about 64 GB) does not, Qwen3-8B or an AWQ/FP8 14B does. |
| `AMD_GGUF_REPO`, `AMD_GGUF_FILE` | `ggml-org/gpt-oss-20b-GGUF`, `gpt-oss-20b-MXFP4.gguf` | Model for the llama.cpp fallback |
| `AMD_TUNNEL` | `1` | `0` for no tunnel (run the games on the box itself, see below) |
| `AMD_WORK` | `~/amd-serve` | Venvs, weights, logs |

If vLLM failed once, later runs skip it and go straight to llama.cpp. Delete `~/amd-serve/vllm.failed` to try vLLM again.

To run the games on the box without the tunnel: `AMD_TUNNEL=0 bash infra/amd/serve.sh`, then in the repo run `uv sync --frozen`, put `AMD_VLLM_BASE_URL=http://127.0.0.1:8000/v1` and the key in `.env`, and use the same `doctor` and `play` commands.

## Why `llamafile/` in models.yaml

The server speaks the OpenAI API, but `ModelSpec` has no per-model `api_base`, and the `openai/` and `hosted_vllm/` routes already take their base URL from OPENAI_* and from the HF router settings. LiteLLM's `llamafile/` route is a plain OpenAI client that reads `LLAMAFILE_API_BASE` and `LLAMAFILE_API_KEY`, and `models.yaml` maps those from `AMD_VLLM_BASE_URL` and `AMD_API_KEY`. It works the same against vLLM and llama-server.
