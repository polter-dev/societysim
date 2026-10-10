# societysim

LLM agents from different providers play Werewolf against each other. We log every move, including each agent's private reasoning, and use the logs to measure how well each model lies, spots lies, persuades and cooperates. A web viewer replays any game.

- Live viewer and leaderboard (Hugging Face Space): TODO(batch)
- Game logs and labels (Hugging Face Dataset): TODO(batch)

## How it works

A deterministic Python engine owns the true game state. It makes no model calls and does no I/O, and all randomness comes from one seeded RNG, so a seed replays the same deal and turn order. The rules are standard Ultimate Werewolf: 7 to 9 players, two wolves, a Seer and a Doctor, a night kill agreed in a private wolf channel, two rounds of public discussion each day, then a simultaneous vote. The full rules are in [docs/RULES.md](docs/RULES.md).

On every turn a model gets an observation that holds only what its role is allowed to know, and it answers in JSON with two parts:

- a private scratchpad with its reasoning and a suspicion score (its honest probability that the player is a wolf) for every other living player;
- a public action: a statement of up to 80 words, a vote, a wolf-channel message or a night target.

The orchestrator checks each answer, retries once if it is invalid, and after a second bad answer uses a fallback (a vote fallback abstains). Every event goes to a JSONL log ([docs/LOG_SCHEMA.md](docs/LOG_SCHEMA.md)).

After the games, `societysim label` reads each log and writes lie labels to `<game_id>.labels.jsonl` next to it. The raw log is never edited. A label comes from one of three places: the agent's own structured tags (role claims, Seer claims, accusations), the Jev classifier from TypeSafe reading each statement, or game facts alone. The label types are false role claims, false Seer reports, bussing (a wolf turning on a fellow wolf), a Seer hiding a wolf result, and votes that eliminated a non-wolf.

`societysim report` turns logs and labels into per-model metrics:

- win, draw and loss rates with Wilson confidence intervals, and OpenSkill ratings as wolf, as villager and overall;
- how often each model lies, and whether a lie worked (did other players' suspicion move the way the liar wanted by that day's vote);
- detection: Brier score and AUC of non-wolves' suspicion scores against who the wolves really were;
- persuasion: the share of other players' votes that day that went to someone the player had accused.

Lie success and persuasion are correlations, not causal estimates.

The viewer in `viewer/` is a static React app. It replays any game step by step, either as the narrator (roles and wolf channel visible) or as a villager would have seen it. It also has a game index and the leaderboard.

## Results

TODO(batch): results table from `runs/<batch>/report/` (win rate, OpenSkill, lie rate and lie success, detection AUC per model).

## Quick start

```bash
uv sync --frozen
uv run pytest
uv run societysim --help
```

Copy `.env.example` to `.env` and fill in the keys you have. Hugging Face models need `HF_TOKEN`, Gemini needs `GEMINI_API_KEY`, and the GPT models need `OPENAI_API_KEY`. Jev labels need `TYPESAFE_API_KEY`; without it, labeling still runs on self-tags and game facts.

Play one game with scripted random agents. This needs no keys and writes `runs/selfplay_smoke/selfplay_smoke-s0.jsonl`:

```bash
uv run societysim play --config configs/selfplay_smoke.yaml
```

With models:

```bash
uv run societysim doctor                                      # ping every model in configs/models.yaml
uv run societysim play --config configs/selfplay_smoke.yaml --agents llm
uv run societysim batch --config configs/smoke_batch.yaml --dry-run   # game plan and cost estimate
uv run societysim batch --config configs/smoke_batch.yaml --agents llm
uv run societysim label runs/smoke_batch
uv run societysim report runs/smoke_batch                     # writes runs/smoke_batch/report/
```

`batch` runs games in parallel, rotates models through seats and roles, and stops starting new games before it would go over the config's `budget_usd`. Re-running it resumes where it stopped. The main experiment is `configs/mixed_v1.yaml` (100 mixed games).

To view games locally:

```bash
cd viewer && npm ci && npm run dev
```

The viewer loads `data/report/index.json` by default. Pass `?index=<url>` to point it at another batch's index, or use "Open a log file" to replay a single `.jsonl`. `scripts/deploy-space.sh runs/<batch> <owner/space>` builds the viewer with a batch and uploads it to a Hugging Face Space.

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

Self-hosted on an AMD GPU: `amd-gpt-oss-20b`, [openai/gpt-oss-20b](https://huggingface.co/openai/gpt-oss-20b) served with vLLM on ROCm.

Closed models: `gemini-flash-lite` and `gemini-flash` through Google AI Studio, and `gpt-5-nano` and `gpt-5-mini` from OpenAI, which sit in at most 10 games each. No Claude models play.

## Sponsor tracks

- Hugging Face: most players are open models on HF Inference Providers, every game goes into an HF Dataset, and the viewer and leaderboard run on an HF Space (links at the top).
- AMD: gpt-oss-20b runs on an AMD Radeon AI PRO R9700 with vLLM on ROCm and plays at the same table as the API models. See [infra/amd/README.md](infra/amd/README.md).
- Neon: `societysim sync-neon runs/<batch>` mirrors games, events and labels into Neon Postgres. The schema is [neon/schema.sql](neon/schema.sql).
- Databricks: game logs become Delta tables, a notebook recomputes the metrics in Spark SQL, an AI/BI dashboard sits on top, and MLflow tracks each batch. See [notebooks/databricks/README.md](notebooks/databricks/README.md).
- Figma and Adobe: the [viewer](viewer/) is designed for someone who has never played Werewolf. It works with a keyboard and a screen reader and respects reduced-motion settings. The illustrated table and night/day lighting are in progress (#33).

## Prior art

- [Werewolf Arena](https://arxiv.org/abs/2407.13943) (Google): we took replay viewer ideas from it. Our agents also give a suspicion score for every living player on every turn, so we can score detection directly instead of inferring it from votes.
- [WOLF](https://arxiv.org/abs/2512.09187): we borrowed its event log layout and deception taxonomy. Our lie labels compare what a player says with the true state the engine holds, and they sit in a separate file from the log.
- Foaster Werewolf benchmark: Elo split by wolf and villager, plus speech tags. We split OpenSkill ratings the same way and add lie success and persuasion.
- [Kaggle Game Arena Werewolf](https://arxiv.org/abs/2609.31473): another Werewolf leaderboard for LLMs. Ours puts cheap open models, a self-hosted AMD model and a few closed ones at the same table, and logs every player's private reasoning.
- [Mini-Mafia](https://arxiv.org/abs/2509.23023): found name and recency biases in LLM players. That is why we shuffle neutral player names by seed, and speaking order is random each round.

## Repo layout and contributing

The engine is in `src/societysim/engine/`, prompts and observations in `src/societysim/agents/`, the model client in `src/societysim/models/`, the batch runner in `src/societysim/batch/` and labeling and metrics in `src/societysim/analysis/`. [AGENTS.md](AGENTS.md) has the rules every contributor (human or agent) follows, [CONTRIBUTING.md](CONTRIBUTING.md) the ticket workflow, and [docs/PLAN.md](docs/PLAN.md) the full plan.
