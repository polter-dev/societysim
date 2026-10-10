# societysim

societysim seats AI models at a table to play Werewolf, a game where a few players secretly work against everyone else and everybody has to decide who to trust. We record every move, including each model's private thoughts, then measure which models lie well, which ones catch liars, and which ones talk others into things.

- Live viewer and leaderboard (Hugging Face Space): TODO(batch)
- Game logs and labels (Hugging Face Dataset): TODO(batch)

## The game in one minute

Werewolf is a bluffing game. Seven players get secret roles:

- **Two wolves** know each other. They want to stay hidden while killing off the village.
- **One seer** can check one player each night and learn whether they are a wolf.
- **One doctor** can protect one player each night from the wolves.
- **Three villagers** have no powers, only their eyes and their votes.

The game alternates night and day. At **night**, the wolves pick a victim in a private chat, the seer checks someone and the doctor protects someone. At **day**, the village learns who died, everyone talks for two rounds (up to 80 words per turn), and then everyone votes at once. The player with the most votes is eliminated and their role is revealed.

The village wins when both wolves are gone. The wolves win when they equal or outnumber everyone else. If nobody has won after six days, it is a draw. The full rules are in [docs/RULES.md](docs/RULES.md).

That is why it makes a good test: wolves have to lie, the seer has to decide whether to reveal what they know, and everyone else has to work out who is lying.

## What you can see

Each turn, a model answers in two parts. The first is a private scratchpad with its reasoning and a suspicion score for every other living player (its honest chance that the player is a wolf). The second is a public action: what it says, who it votes for, or who it targets at night. A Python engine, not the model, owns the real game state and enforces the rules, so a model can lie to the other players but cannot cheat. Each model is shown only what its role allows it to know.

From the game logs we build:

- **A replay viewer.** A round table with the players, their speech, accusation and vote arrows, and a timeline you can scrub. Watch as the narrator (roles and the wolf chat visible) or as a villager would have seen it. Click a player to see their private scratchpad and suspicion scores next to what they said out loud. Labeled lies pop up where they happen.
- **Lie labels.** `societysim label` marks false role claims, false seer reports, a wolf turning on a fellow wolf, a seer hiding a wolf result, and votes that eliminated an innocent player. The raw log is never edited; labels go in a separate file.
- **A per-model leaderboard and report.** Win, draw and loss rates with confidence intervals, skill ratings as wolf and as villager, how often each model lies and whether the lie worked, how well its suspicion scores pointed at the real wolves, and how often other players voted for someone it accused. Lie success and persuasion are correlations, not proof of cause.
- **Highlights.** `societysim highlights` picks the best moments in a batch (a lie that swung a vote, a wolf who survived, a doctor save, a seer who found a wolf that the village then voted out) and links to them in the viewer.
- **Narration.** An offline script turns a finished game's public events into spoken clips with ElevenLabs. It never voices private reasoning or the wolf chat. The viewer does not play the clips yet.
- **A human seat.** `societysim play --human-seat p3` lets you take one seat yourself from the terminal, with the same information an AI in that seat gets.

## Results

TODO(batch): results table from `runs/<batch>/report/` (win rate, skill rating, lie rate and lie success, detection AUC per model). Tracked in #37.

## Tech stack

The code is Python 3.12 (engine, batch runner and analysis, with Pydantic for data checking and LiteLLM as the one client for every model provider) and a React + Vite + TypeScript viewer.

| Piece | What it does for us |
|---|---|
| Hugging Face | Most players are open models served through Inference Providers (Hugging Face's pay-per-use service that runs models hosted by other companies). Game logs are meant to go into a Dataset, and the viewer and leaderboard onto a Space (`scripts/deploy-space.sh` handles the Space; the Dataset upload is in progress). |
| AMD | gpt-oss-20b runs on an AMD Radeon AI PRO R9700 GPU, served with vLLM (software that runs a model as a web service) on ROCm (AMD's toolkit for running AI on its GPUs), as a self-hosted player. See [infra/amd/README.md](infra/amd/README.md). |
| Databricks | Game logs become Delta tables (Databricks' table format), a notebook recomputes the metrics, an AI/BI dashboard sits on top, and MLflow (an experiment tracker) records each batch as a run. See [notebooks/databricks/README.md](notebooks/databricks/README.md). |
| Neon | Serverless Postgres database. `play` and `batch` with `--neon` stream events in while a game runs, and `societysim sync-neon` backfills finished batches ([neon/schema.sql](neon/schema.sql)). Read-only views serve the leaderboard (`leaderboard`, `leaderboard_by_batch`, `lies_by_model`). The viewer does not read from Neon yet. |
| ElevenLabs | Text-to-speech for game narration. |
| TypeSafe (Jev) | A classifier that reads each statement to help label lies. Labeling still works without it. |
| Figma | Design for a viewer that someone who has never played Werewolf can follow. The viewer supports keyboard use, screen readers and reduced motion. |
| Google AI Studio and OpenAI | Gemini and GPT models also sit at the table. No Claude models play. |

## Quick start

You need [uv](https://docs.astral.sh/uv/) and, for the viewer, Node.js. Everything in this block is free and needs no keys.

```bash
uv sync --frozen
uv run societysim --help
uv run societysim play --config configs/selfplay_smoke.yaml   # one game with scripted random players
```

That writes `runs/selfplay_smoke/selfplay_smoke-s0.jsonl`. To watch it:

```bash
cd viewer && npm ci && npm run dev
```

Then use "Open a log file" in the viewer and pick the `.jsonl`. The viewer opens in villager view, which hides the scratchpads and lie labels, so switch the View menu to Narrator to see them. For the full list of commands, including free labeling with `label --no-jev` and what real runs cost, see [docs/DEMO.md](docs/DEMO.md).

Games with real models need keys. Copy `.env.example` to `.env` and fill in the ones you have:

- `HF_TOKEN`: the Hugging Face models
- `GEMINI_API_KEY`: Gemini
- `OPENAI_API_KEY`: `gpt-5-nano` and `gpt-5-mini`
- `AMD_VLLM_BASE_URL` and `AMD_API_KEY`: the AMD model
- `TYPESAFE_API_KEY`: Jev lie labels
- `ELEVENLABS_API_KEY`: narration
- `NEON_DATABASE_URL`: `--neon` and `sync-neon`

```bash
uv run societysim doctor                                                 # ping every model in configs/models.yaml
uv run societysim batch --config configs/smoke_batch.yaml --dry-run     # game plan and cost estimate
uv run societysim batch --config configs/smoke_batch.yaml --agents llm  # paid: real model calls
uv run societysim label runs/smoke_batch
uv run societysim report runs/smoke_batch                               # writes runs/smoke_batch/report/
```

What these do:

- `batch` runs games in parallel and rotates models through seats and roles.
- It stops starting new games before it would go over the config's `budget_usd`, and resumes if you run it again.
- The main experiment is `configs/mixed_v1.yaml` (100 mixed games).
- After a batch, `uv run python -m societysim.analysis.postbatch runs/<batch>` runs label (without Jev), report and highlights in one go, and prints the numbers and upload commands to use next.
- The viewer loads `data/report/index.json` by default; add `?index=<url>` to point it at another batch.
- `scripts/deploy-space.sh runs/<batch> <owner/space>` builds the viewer with a batch and uploads it to a Hugging Face Space. To preview the same site locally with no upload and no token, run `scripts/deploy-space.sh --out site runs/<batch>` and then `python -m http.server -d site 8000` (`--force` writes into a non-empty folder).
- The live site is on Cloudflare Pages at https://society-sim.us (project `societysim`). The `Deploy` workflow runs by hand (`gh workflow run deploy.yml -f targets=cloudflare,hf-space,github-pages`; `targets` picks any of the three, default all) and automatically on every push to `main` that touches `viewer/`. It deploys the sample game unless a data source is set: the `source` input or the `DEPLOY_SOURCE` repo variable, as `hf:<owner/name>[:<batch_id>]` (for example `hf:polter-dev/societysim-games`). CI then downloads that dataset's logs and labels with `scripts/fetch_hf_batch.py`, runs `label --no-jev` (only if the dataset has no labels), `report` and `highlights`, and deploys the result. No paid calls; a private dataset needs the `HF_TOKEN` secret. The Cloudflare step needs the repo secret `CLOUDFLARE_API_TOKEN` and the repo variable `CLOUDFLARE_ACCOUNT_ID`, and is skipped when either is missing. The Hugging Face step needs the `HF_TOKEN` secret. The GitHub Pages mirror at https://polter-dev.github.io/societysim stays.
- Live games from Neon on the deployed site: set two repo variables (not secrets; both are public and read-only), then rerun `Deploy`.
  `gh variable set VITE_NEON_DATA_API_URL --body 'https://ep-....apirest.<region>.aws.neon.tech/<db>/rest/v1'` (Neon Console, your project, branch, Data API page; it is the same value as `VITE_NEON_DATA_API_URL` in `.env`).
  `gh variable set VITE_NEON_ANON_KEY --body '<jwt>'`. The key is optional: the viewer sends it as `Authorization: Bearer <key>`, and with none Neon runs the request as the `anonymous` role, which `neon/schema.sql` limits to `select` on the games, events, labels and leaderboard tables and views. If the Data API answers 401 without a key, it needs a JWT. Neon's docs say anonymous access "still uses a JWT, but no user sign-in is required": with Managed Better Auth, `GET <Auth URL>/token/anonymous` (Console, project, branch, Auth page) returns one; with an external provider, use that provider's guest token. These tokens may be short-lived, so check the expiry before pinning one in a variable.
  Never put `NEON_DATABASE_URL` (it can write) in a variable or in the build. If either variable is unset, the build still succeeds, the viewer uses static data, and the log has a `::notice::`. When both are set, `/devui/` also gets a `config.json` that pre-fills its Neon fields.
- To put a real batch on Cloudflare (`runs/` is gitignored, so CI can't see it): `bash scripts/deploy-space.sh --out /tmp/site --force runs/<batch>`, then `npx wrangler login` (one time, browser sign-in) and `npx wrangler pages deploy /tmp/site --project-name societysim --branch main`.

## Models

The lineup is in [configs/models.yaml](configs/models.yaml). All calls go through LiteLLM.

Open models through Hugging Face Inference Providers:

| Name | Model |
|---|---|
| `qwen3-235b` | [Qwen/Qwen3-235B-A22B-Instruct-2507](https://huggingface.co/Qwen/Qwen3-235B-A22B-Instruct-2507) |
| `llama4-maverick` | [meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8](https://huggingface.co/meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8) |
| `gemma4-26b` | [google/gemma-4-26B-A4B-it](https://huggingface.co/google/gemma-4-26B-A4B-it) |
| `gpt-oss-120b` | [openai/gpt-oss-120b](https://huggingface.co/openai/gpt-oss-120b) |
| `deepseek-v3.1` | [deepseek-ai/DeepSeek-V3.1](https://huggingface.co/deepseek-ai/DeepSeek-V3.1) |

Self-hosted on an AMD GPU: `amd-gpt-oss-20b`, [openai/gpt-oss-20b](https://huggingface.co/openai/gpt-oss-20b) served with vLLM on ROCm. So far it plays self-play games inside the AMD pod (`configs/selfplay_amd.yaml`); the mixed batches leave it out because laptops can't reach the pod. TODO(batch): AMD throughput, latency and cost per game.

Closed models: `gemini-flash-lite` and `gemini-flash` through Google AI Studio, and `gpt-5-nano` and `gpt-5-mini` from OpenAI, which sit in at most 10 games each.

## How it works

The engine in `src/societysim/engine/` is deterministic: it makes no model calls and does no I/O, and all randomness comes from one seeded generator, so a seed replays the same deal and turn order. The orchestrator checks each model answer, retries once if it is invalid, and then uses a fallback (a vote fallback abstains). Every event goes to a JSONL log ([docs/LOG_SCHEMA.md](docs/LOG_SCHEMA.md)). More in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Prior art

- [Werewolf Arena](https://arxiv.org/abs/2407.13943) (Google): we took replay viewer ideas from it. Our agents also give a suspicion score for every living player on every turn, so we can score detection directly instead of inferring it from votes.
- [WOLF](https://arxiv.org/abs/2512.09187): we borrowed its event log layout and deception taxonomy. Our lie labels compare what a player says with the true state the engine holds, and they sit in a separate file from the log.
- [Foaster Werewolf benchmark](https://huggingface.co/spaces/Foaster/Werewolf_benchmark): Elo split by wolf and villager, plus speech tags. We split OpenSkill ratings the same way and add lie success and persuasion.
- [Kaggle Game Arena Werewolf](https://arxiv.org/abs/2609.31473): another Werewolf leaderboard for LLMs. Ours puts cheap open models and a few closed ones at the same table, and logs every player's private reasoning.
- [Mini-Mafia](https://arxiv.org/abs/2509.23023): found name and recency biases in LLM players. That is why we shuffle neutral player names by seed, and speaking order is random each round.

## Contributing

[AGENTS.md](AGENTS.md) has the rules every contributor (human or agent) follows, [CONTRIBUTING.md](CONTRIBUTING.md) the ticket workflow, and [docs/PLAN.md](docs/PLAN.md) the full plan. The engine is in `src/societysim/engine/`, prompts and observations in `src/societysim/agents/`, the model client in `src/societysim/models/`, the batch runner in `src/societysim/batch/`, and labeling and metrics in `src/societysim/analysis/`.
