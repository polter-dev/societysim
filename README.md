# societysim

societysim seats AI models at a table to play Werewolf, a game where a few players secretly work against everyone else and everybody has to decide who to trust. We record every move, including each model's private thoughts, then measure which models lie well, which ones catch liars, and which ones talk others into things.

- Live viewer and leaderboard: https://society-sim.us (mirror on the Hugging Face Space https://huggingface.co/spaces/polter-dev/societysim)
- Game logs and labels (Hugging Face Dataset): https://huggingface.co/datasets/polter-dev/societysim-games

## What we're asking

**Main question:** when AI models from different companies play a social deduction game, can one model deceive another, and which models fool which?

Sub-questions:

- Who lies best? That means who deceives the most and still wins.
- Who catches lies best? This is detection.
- Is a lie's success about the liar or the listener? A pairwise "who fools whom" table should show it (#251).
- Do humans fool models, or get fooled, more than models do to each other? The human seat covers this.
- Does game balance change the answer, for example 7 versus 10 players (#216)?

**Why it matters.** AI agents increasingly negotiate and talk to each other. Knowing which models can be misled, and by whom, is a safety and trust question. Because a deterministic engine owns the game and each model writes a hidden scratchpad, we can see the intent behind each lie, not only the words.

**How we measure it:**

- Lies, found two ways: the model's own scratchpad, and `societysim label` with the Jev classifier.
- Suspicion shifts after a lie, and votes that follow a lie.
- OpenSkill ratings as wolf and as villager.

The numbers come from the report (see Results below and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)). Findings from 100 ten-player games (`mixed_v2_10p`, $11.02): gemini-flash ranked first on OpenSkill (ordinal 32.94, 65% win rate, 68% as wolf) and had the best detection AUC (0.77). Villagers won 62 of 100 games. Self-tagged lies were 24% of statements. Across all models, 57% of votes went the way a lie implied, against a 59% no-lie base rate, so lying in general did not beat the baseline; gemini-flash's lies did (73% against 65%). Treat the lie numbers as exploratory.

The results below are 10-player games with 2 wolves and 8 others (#250, #216). Human-vs-model numbers will come from visitor games; there are none in this batch.

## The game in one minute

Werewolf is a bluffing game. Ten players get secret roles (the default; a game can have 7 to 10):

- **Two wolves** know each other. They want to stay hidden while killing off the village.
- **One seer** can check one player each night and learn whether they are a wolf.
- **One doctor** can protect one player each night from the wolves.
- **Six villagers** have no powers, only their eyes and their votes.

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
- **Narration.** An offline script turns a finished game's public events into spoken clips with ElevenLabs. It never voices private reasoning or the wolf chat. The replay plays the clips, with narration controls under the table.
- **A human seat.** `societysim play --preset basic --human-seat p3` lets you take one seat yourself from the terminal, with the same information an AI in that seat gets. The other seats are scripted random players, so it is free. For a game against nine different models (paid), use `societysim play --config configs/human_vs_ai_10p.yaml --human-seat p0 --agents llm`.

## Results

<!-- results:start -->
### batch `mixed_v2_10p`, 10 players, 100 games, spend $11.02

| Model | Games / seats | Win rate (95% CI) | OpenSkill ordinal | Detection AUC | Lie rate: self-tag | Lie rate: Jev | Invalid output rate |
|---|---|---|---|---|---|---|---|
| gemini-flash | 100 / 139 | 65.5% (57.2% to 72.9%) | 32.94 | 0.77 | 27.9% | n/a | 0.8% |
| llama4-maverick | 100 / 140 | 60.7% (52.4% to 68.4%) | 23.69 | 0.72 | 27.9% | n/a | 20.0% |
| gemini-flash-lite | 100 / 138 | 59.4% (51.1% to 67.3%) | 22.71 | 0.75 | 32.2% | n/a | 0.2% |
| gemma4-26b | 100 / 141 | 56.0% (47.8% to 64.0%) | 19.87 | 0.70 | 17.9% | n/a | 0.2% |
| qwen3-235b | 100 / 141 | 54.6% (46.4% to 62.6%) | 16.47 | 0.69 | 22.9% | n/a | 3.4% |
| deepseek-v3.1 | 100 / 137 | 54.0% (45.7% to 62.1%) | 13.73 | 0.73 | 21.9% | n/a | 0.4% |
| gpt-oss-120b | 100 / 138 | 53.6% (45.3% to 61.7%) | 9.32 | 0.65 | 13.6% | n/a | 5.0% |
| gpt-5-nano | 12 / 13 | 38.5% (17.7% to 64.5%) | -0.25 | 0.55 | 13.9% | n/a | 12.7% |
| gpt-5-mini | 12 / 13 | 38.5% (17.7% to 64.5%) | -1.76 | 0.61 | 34.6% | n/a | 0.7% |

Wolf-side advantage (OpenSkill rating of the wolf side as a player; 0 is even, above 0 favors the wolves): overall pool mu -3.66 (sigma 2.46); sided pool mu -1.09 (sigma 2.64). Within 2 sigma of 0, so no clear advantage yet.

Sample size: 100 finished games; each model filled 13 to 141 seats. Wide intervals mean a gap between two models may be chance. Lie rates are lies per statement; n/a means that label source has no labels in this batch.
<!-- results:end -->

## Tech stack

The code is Python 3.12 (engine, batch runner and analysis, with Pydantic for data checking and LiteLLM as the one client for every model provider) and a React + Vite + TypeScript viewer.

| Piece | What it does for us |
|---|---|
| Hugging Face | Most players are open models served through Inference Providers (Hugging Face's pay-per-use service that runs models hosted by other companies). Game logs are meant to go into a Dataset, and the viewer and leaderboard onto a Space (`scripts/deploy-space.sh` handles the Space; the Dataset upload is in progress). |
| AMD | gpt-oss-20b runs on an AMD Radeon AI PRO R9700 GPU, served with vLLM (software that runs a model as a web service) on ROCm (AMD's toolkit for running AI on its GPUs), as a self-hosted player. See [infra/amd/README.md](infra/amd/README.md). |
| Databricks | Game logs become Delta tables (Databricks' table format), a notebook recomputes the metrics, an AI/BI dashboard sits on top, and MLflow (an experiment tracker) records each batch as a run. See [notebooks/databricks/README.md](notebooks/databricks/README.md). |
| Neon | Serverless Postgres database. `play` and `batch` with `--neon` stream events in while a game runs, and `societysim sync-neon` backfills finished batches ([neon/schema.sql](neon/schema.sql)). Read-only views serve the leaderboard (`leaderboard`, `leaderboard_by_batch`, `lies_by_model`). When `VITE_NEON_DATA_API_URL` and `VITE_NEON_AUTH_URL` are set at build time, the viewer reads the game list, live games and the global leaderboard from Neon's Data API (see the deploy notes below). |
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

The dev server opens the illustrated landing page, which has no "Open a log file" link. Go to `http://localhost:5173/?page=replay` (or `?page=games`, then "Open a log file") and pick the `.jsonl`. The viewer opens in villager view, which hides the scratchpads and lie labels, so switch the View menu to Narrator to see them. For the full list of commands, including free labeling with `label --no-jev` and what real runs cost, see [docs/DEMO.md](docs/DEMO.md).

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
- The main experiment is `configs/mixed_v2_10p.yaml` (100 mixed 10-player games). `configs/mixed_v1.yaml` is the older 7-player batch.
- Running a real experiment batch, step by step (dry run, budget, resume, label, publish, safety): [docs/RUNNING_BATCHES.md](docs/RUNNING_BATCHES.md).
- After a batch, `uv run python -m societysim.analysis.postbatch runs/<batch>` runs label (without Jev), report and highlights in one go, and prints the numbers and upload commands to use next.
- The viewer loads `./index.json` from the site root by default (`report/index.json` is the older location); add `?index=<url>` to point it at another batch.
- `scripts/deploy-space.sh runs/<batch> <owner/space>` builds the viewer with a batch and uploads it to a Hugging Face Space. To preview the same site locally with no upload and no token, run `scripts/deploy-space.sh --out site runs/<batch>` and then `python -m http.server -d site 8000` (`--force` writes into a non-empty folder).
- The live site is on Cloudflare Pages at https://society-sim.us (project `societysim`). The `Deploy` workflow runs by hand (`gh workflow run deploy.yml -f targets=cloudflare,hf-space,github-pages`; `targets` picks any of the three, default all) and automatically on every push to `main` that touches `viewer/`. It deploys the sample game unless a data source is set: the `source` input or the `DEPLOY_SOURCE` repo variable, as `hf:<owner/name>[:<batch_id>]` (for example `hf:societysim/societysim-games`). CI then downloads that dataset's logs and labels with `scripts/fetch_hf_batch.py`, runs `label --no-jev` (only if the dataset has no labels), `report` and `highlights`, and deploys the result. No paid calls; a private dataset needs the `HF_TOKEN` secret. The Cloudflare step needs the repo secret `CLOUDFLARE_API_TOKEN` and the repo variable `CLOUDFLARE_ACCOUNT_ID`, and is skipped when either is missing. The Hugging Face step needs the `HF_TOKEN` secret. The GitHub Pages mirror at https://polter-dev.github.io/societysim stays. Pull requests that touch `viewer/` also get a Cloudflare Pages preview (the `Preview` workflow, `.github/workflows/preview.yml`): it deploys the sample-game site to the `societysim` project on a branch named after the PR branch and comments the `*.pages.dev` URL on the PR. It uses the same secret and variable as the deploy, skips on fork PRs and when they are missing, and is not a required check.
- Before a demo, check the live site: `uv run --extra smoke python -m playwright install chromium` once, then `uv run --extra smoke python scripts/site_smoke.py [BASE_URL]` (default https://society-sim.us; `--json` for machine output, `--strict-play` to fail unless the play start screen is up). It loads every page headless, checks each key element, console errors, the data files and that unrevealed seats show no role names, prints a pass/fail table and exits non-zero on a failure. It only reads. The `Site smoke` workflow runs the same check by hand.
- Live games from Neon on the deployed site: set two repo variables (not secrets; both are public), then rerun `Deploy`.
  `gh variable set VITE_NEON_DATA_API_URL --body 'https://ep-....apirest.<region>.aws.neon.tech/<db>/rest/v1'` (Neon Console, your project, branch, Data API page).
  `gh variable set VITE_NEON_AUTH_URL --body '<Auth URL>'` (Console, Auth page). The Data API wants a JWT on every request. The page gets an anonymous one with `GET <Auth URL>/token/anonymous` (returns `{token, expires_at}`, valid about 1 h, role `anonymous`, which `neon/schema.sql` limits to `select` on the games, events, labels and leaderboard tables and views) and sends it as `Authorization: Bearer <token>`. The viewer reads both variables; `/devui/` gets them from `config.json`.
  `VITE_NEON_ANON_KEY` (a pinned JWT) still works but is not recommended: it expires.
  Never put `NEON_DATABASE_URL` (it can write) in a variable or in the build. If a variable is unset, the build still succeeds, the viewer uses static data, and the log has a `::notice::`.
- To put a real batch on Cloudflare (`runs/` is gitignored, so CI can't see it): `bash scripts/deploy-space.sh --out /tmp/site --force runs/<batch>`, then `npx wrangler login` (one time, browser sign-in) and `npx wrangler pages deploy /tmp/site --project-name societysim --branch main`.

### Visitor play

The game server for visitor play (`src/societysim/server`) runs as a Docker Space, `polter-dev/societysim-play` (https://polter-dev-societysim-play.hf.space). The site calls it from the browser; the viewer build needs two public repo variables, `VITE_PLAY_API_URL` (that URL) and `VITE_TURNSTILE_SITE_KEY`.

Deploy it with `scripts/deploy-play-space.sh [--dry-run]`. It reads these from your environment and sets them on the Space by name, never printing a value: `HF_TOKEN` (deploys the Space), `PLAY_OPENAI_API_KEY`, `PLAY_HF_TOKEN` and `PLAY_GEMINI_API_KEY` (dedicated, limited keys; set on the Space as `OPENAI_API_KEY`, `HF_TOKEN`, `GEMINI_API_KEY`), `NEON_DATABASE_URL` and `TURNSTILE_SECRET_KEY` (the server won't start without these two). `--dry-run` prints the plan. The `Deploy` workflow does the same with `gh workflow run deploy.yml -f deploy_play=true` and repo secrets, and skips with a notice if they are missing.

Space variables the script sets: `PLAY_TRUSTED_PROXIES` (HF's private proxy ranges, so the server sees the visitor's IP from `X-Forwarded-For`; without it every visitor shares one rate limit, so the server refuses to start) and `PLAY_ALLOWED_MODELS` (priced models only, so the daily budget holds). Limits, all optional Space variables: `PLAY_DAILY_BUDGET_USD` (default 2.00), `PLAY_MAX_CONCURRENT` (4), `PLAY_MAX_GAMES_PER_IP_DAY` (5), `PLAY_MAX_ACTIVE_PER_IP` (1), `PLAY_RATE_PER_MIN` (120), `PLAY_MAX_GAME_MINUTES` (45), `PLAY_MAX_TOKENS` (2048).

After a deploy, check `curl https://polter-dev-societysim-play.hf.space/api/limits`: `remaining_today` above 0 means the Neon spend ledger loaded (it needs `neon/schema.sql`, which the image ships), and `client_ip_hint` should be your own public IP, masked (`104.53.x.x`), not HF's proxy (`10.x.x`). The uvicorn access log always shows the proxy; the hint is what the limits use. HF's proxy allows any CORS origin, so the server also refuses a POST whose `Origin` isn't the site's (403 `forbidden`) and a Turnstile token solved on another hostname.

### Deploy on Render (fallback host)

`render.yaml` describes the same server as one Render web service, built from `server/Dockerfile`, a single instance (game state is in memory, so never scale out). The Dockerfile listens on `$PORT` when set (Render sets it) and on 7860 otherwise, so the Space is unchanged.

1. In the Render dashboard: New, Blueprint, pick this repo, apply.
2. Paste the secrets Render asks for: `PLAY_OPENAI_API_KEY`, `PLAY_HF_TOKEN`, `PLAY_GEMINI_API_KEY`, `TURNSTILE_SECRET_KEY`, `NEON_DATABASE_URL`, `PLAY_ALERT_WEBHOOK_URL`. The limits (`PLAY_*`) have defaults in the file.
3. Once it's live, set the repo variable `VITE_PLAY_API_URL` to the Render URL (`https://societysim-play.onrender.com`, or whatever Render assigned) and redeploy the site. The uptime workflow reads the same variable.
4. Checks, with `$URL` as the Render URL:
   - `curl $URL/api/limits`: `remaining_today` above 0, and `client_ip_hint` is your own public IP, masked, not `10.x.x.x` or a Cloudflare address (`172.64-71.x.x`, `104.16-31.x.x`).
   - Spoof test: `curl -H 'X-Forwarded-For: 6.6.6.6' $URL/api/limits` still shows your IP, never `6.6.x.x`.
   - Origin: `curl -X POST -H 'Origin: https://evil.example' $URL/api/games` returns 403 `forbidden`. CORS and the Origin allowlist (society-sim.us, www, pages.dev) are the same as on the Space.

**Client IP on Render.** Render doesn't document the exact header chain or its proxy ranges. Its [DDoS article](https://render.com/articles/how-render-handles-ddos-attacks) says all traffic to web services passes through Cloudflare and Render's load balancers and that the app sees the proxy's IP by default. A [community test service on Render](https://github.com/johnbeynon/header-test) found that a client-sent `X-Forwarded-For` is kept with the real address appended after it, and that `CF-Connecting-IP` is always set to the real client (a forged copy is rejected or overwritten). Trusting Cloudflare's ranges and walking `X-Forwarded-For` would be spoofable: a visitor can send through their own Cloudflare Worker or WARP, arrive from a trusted Cloudflare address, and have a forged chain believed. So on Render the server trusts only the private hop (`PLAY_TRUSTED_PROXIES=10.0.0.0/8`) and reads the client from `CF-Connecting-IP` (`PLAY_CLIENT_IP_HEADER=CF-Connecting-IP`); `render.yaml` sets both. The header is read only when the direct peer is trusted and only when `PLAY_CLIENT_IP_HEADER` is set, so on a Space, where a visitor could send it, it is ignored. Unlike a Space, the server doesn't refuse to start on Render without these, so check `client_ip_hint` after the first deploy.


Kill switch: set the Space variable `PLAY_ENABLED=0` (Space settings, Variables, then restart). New games get a `play_disabled` error; the rest of the site keeps working.

Alerts: set `PLAY_ALERT_WEBHOOK_URL` (a Discord webhook URL) as a Space secret, and as a repo secret so the deploy script copies it. The server posts when daily spend passes 50% and 90% of the cap, when it starts with the kill switch on (or the switch flips while it runs), and when it fails to start. The `Play uptime` workflow checks `/api/limits` every 15 minutes and posts to the same webhook if the Space is down; it reads the `VITE_PLAY_API_URL` repo variable. With no webhook set, nothing is posted.

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

Self-hosted on an AMD GPU: `amd-gpt-oss-20b`, [openai/gpt-oss-20b](https://huggingface.co/openai/gpt-oss-20b) served with vLLM on ROCm. There is also `amd-strix-gpt-oss-20b`, the same model as a GGUF on llama.cpp, running on a Radeon 8060S (Strix Halo). Both play self-play games inside the AMD pod (`configs/selfplay_amd.yaml`, `configs/selfplay_amd_strix.yaml`); the mixed batches leave them out because laptops can't reach the pod. Strix Halo numbers, from 5 self-play games with no errors: 7.3 s per call, 39.5 output tokens/s, about 138 s per game, $0/h (hackathon-provided GPU). Every game ended in a wolf win by parity on day 2, so these are short games.

Closed models: `gemini-flash-lite` and `gemini-flash` through Google AI Studio, and `gpt-5-nano` and `gpt-5-mini` from OpenAI, which sit in at most 12 games each in `mixed_v2_10p` (10 in the older `mixed_v1`; the self-play baselines have no cap).

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

## License

The code is MIT, see [LICENSE](LICENSE). The game logs and labels on the Hugging Face Dataset are CC-BY-4.0. The model outputs inside those logs were generated by third-party models and fall under their providers' terms.
