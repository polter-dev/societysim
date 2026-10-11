# SocietySim

AI models play Werewolf. We record what they say, what they privately suspect, and who believes them.

- [Watch games and see the leaderboard](https://society-sim.us)
- [Play a seat against AI models](https://society-sim.us/?page=modes)
- [Download game logs and labels](https://huggingface.co/datasets/polter-dev/societysim-games)

## What it is

In Werewolf, two wolves hide among eight other players. A game has 10 players by default: two wolves, one seer, one doctor, and six villagers. Games can have 7 to 10 players. This batch used the 10-player default. Players talk and vote by day; the wolves attack, the seer checks a role, and the doctor protects a player at night. See the [rules](docs/RULES.md).

Models from several providers fill the seats. Each model sends a private scratchpad, including a suspicion score for every other living player, and a public action. A deterministic Python engine holds the true roles and enforces the rules. The [viewer](https://society-sim.us) replays the event log from a villager's view or with private information revealed.

## Research questions

Answers below come from 100 ten-player games in `mixed_v2_10p`, except where stated. Lie results use the models' own tags and show associations, not proof that a lie caused a vote. Jev labels were not used in this batch. The two GPT-5 models played 12 games each, so their results have smaller samples.

1. **Can one model deceive another, and which models fool which?** Not on average. Across the batch, 57% of votes followed a lie's implied choice, against a 59% no-lie baseline. Gemini Flash is the exception (see 2). The [research page](https://society-sim.us/?page=research) has the pairwise table of who followed whom.
2. **Who lies best?** Gemini Flash had the highest lie success rate: its lies moved 45% of targeted suspicions (n=943), and 73% of votes followed them (n=1005) against a 65% no-lie base (130 votes, 27 abstentions); GPT-oss-120b moved 16% of targeted suspicions. GPT-5 Mini moved 38% (n=138) and had 85% of votes follow (n=141) against a 29% base (14 votes, 1 abstention), while GPT-5 Nano moved 0% (n=29) and had 0% of votes follow (n=34) against a 21% base (24 votes, 1 abstention); those two models played only 12 games each.
3. **Who catches lies best?** Gemini Flash had the highest wolf-detection AUC of the 100-game models, at 0.77. GPT-oss-120b had the lowest of those, at 0.65.
4. **Is success about the liar or the listener?** Both matter in the data. The same liar gets different results from different listeners: DeepSeek V3.1 followed 26% of GPT-oss-120b's lies (69 votes) against an 81% baseline, and Gemini Flash followed 40%. Most pairs sit below their baseline. These data cannot isolate cause.
5. **Do humans fool models, or get fooled, more than models do to each other?** This batch has no visitor games, so there is no comparison yet. The [play page](https://society-sim.us/?page=modes) lets a visitor take a seat.
6. **Does seven versus ten players change the answer?** We have no model comparison across those sizes. In separate scripted games, ten players produced 53.4 statements per game versus 20.5 with seven, giving each pair more chances to interact; those bots do not measure model deception.

## Key findings

Villagers won 62 of 100 games. Gemini Flash led the OpenSkill ranking (33.01) and won 65% of its seats (95% interval: 57% to 73%). The intervals overlap across models, and the lie results are exploratory. The 100 games cost $11.02 in model calls.

## Results

The table below is written by `scripts/readme_results.py` from the batch report. See the [research page](https://society-sim.us/?page=research) for more comparisons.

<!-- results:start -->
### batch `mixed_v2_10p`, 10 players, 100 games, spend $11.02

| Model | Games / seats | Win rate (95% CI) | OpenSkill ordinal | Detection AUC | Lie rate: self-tag | Lie rate: Jev | Invalid output rate |
|---|---|---|---|---|---|---|---|
| gemini-flash | 100 / 139 | 65.5% (57.2% to 72.9%) | 33.01 | 0.77 | 27.9% | n/a | 0.8% |
| llama4-maverick | 100 / 140 | 60.7% (52.4% to 68.4%) | 22.49 | 0.72 | 27.9% | n/a | 20.0% |
| gemini-flash-lite | 100 / 138 | 59.4% (51.1% to 67.3%) | 22.30 | 0.75 | 32.2% | n/a | 0.2% |
| gemma4-26b | 100 / 141 | 56.0% (47.8% to 64.0%) | 18.65 | 0.70 | 17.9% | n/a | 0.2% |
| qwen3-235b | 100 / 141 | 54.6% (46.4% to 62.6%) | 15.52 | 0.69 | 22.9% | n/a | 3.4% |
| deepseek-v3.1 | 100 / 137 | 54.0% (45.7% to 62.1%) | 11.73 | 0.73 | 21.9% | n/a | 0.4% |
| gpt-oss-120b | 100 / 138 | 53.6% (45.3% to 61.7%) | 8.01 | 0.65 | 13.6% | n/a | 5.0% |
| gpt-5-nano | 12 / 13 | 38.5% (17.7% to 64.5%) | -0.14 | 0.55 | 13.9% | n/a | 12.7% |
| gpt-5-mini | 12 / 13 | 38.5% (17.7% to 64.5%) | -2.28 | 0.61 | 34.6% | n/a | 0.7% |

Wolf-side advantage (OpenSkill rating of the wolf side as a player; 0 is even, above 0 favors the wolves): overall pool mu -3.86 (sigma 2.52); sided pool mu -1.09 (sigma 2.64). Within 2 sigma of 0, so no clear advantage yet.

Sample size: 100 finished games; each model filled 13 to 141 seats. Wide intervals mean a gap between two models may be chance. Lie rates are lies per statement; n/a means that label source has no labels in this batch.
<!-- results:end -->

## How it works

The Python engine deals roles from a seed. An orchestrator asks models for JSON actions and validates the replies. Events go to an append-only JSONL log; lie labels sit in a separate file. Analysis builds the report, and a React viewer replays the games. See the [architecture](docs/ARCHITECTURE.md) and [log schema](docs/LOG_SCHEMA.md).

## Quick start

Install [uv](https://docs.astral.sh/uv/) and Node.js. This scripted game needs no API keys:

```bash
uv sync --frozen
uv run societysim play --config configs/selfplay_smoke.yaml
cd viewer && npm ci && npm run dev
```

The dev server opens the landing page. Go to `http://localhost:5173/?page=replay`, choose "Open a log file," and pick `runs/selfplay_smoke/selfplay_smoke-s0.jsonl`. Use Narrator view to see private scratchpads.

To take a seat in a free terminal game, run `uv run societysim play --preset basic --human-seat p3`. The other seats are scripted random players. For a paid game against nine different models, run `uv run societysim play --config configs/human_vs_ai_10p.yaml --human-seat p0 --agents llm`. See the [demo commands](docs/DEMO.md), [batch guide](docs/RUNNING_BATCHES.md), and [deployment guide](docs/DEPLOYMENT.md) for more.

## Repo map

- `src/societysim/`: engine, agents, batch runner, server, and analysis
- `viewer/`: React replay viewer and play interface
- `configs/`: model and game settings
- `schema/` and `fixtures/`: event schemas and sample logs

## Team and tracks

Marcus works on the engine, Seth on the runner and data, and Augustin on the viewer. See [CONTRIBUTING.md](CONTRIBUTING.md) to help.

Hugging Face serves most of the open models and hosts the public dataset; Cloudflare hosts the site. The project also uses [AMD GPUs](infra/amd/README.md), [Databricks](notebooks/databricks/README.md), [Neon](neon/schema.sql), ElevenLabs narration, TypeSafe Jev labels, Figma design, and Gemini and OpenAI models.

## Prior art and models

We built the engine fresh and borrowed ideas from earlier Werewolf and Mafia work:

- Google [Werewolf Arena](https://arxiv.org/abs/2407.13943): replay viewer ideas.
- [WOLF](https://arxiv.org/abs/2512.09187): event log layout and deception taxonomy.
- [Foaster Werewolf benchmark](https://werewolf.foaster.ai/): Elo split by wolf and villager, speech tags.
- Kaggle Game Arena Werewolf ([arXiv 2609.31473](https://arxiv.org/abs/2609.31473)): the model of a public leaderboard from mixed-model games.
- [Mini-Mafia](https://arxiv.org/abs/2509.23023): name and recency biases, which is why we shuffle names by seed, drawn from feminine, masculine or neutral pools matched to each avatar.

Open models run through Hugging Face: [Qwen3-235B-A22B-Instruct-2507](https://huggingface.co/Qwen/Qwen3-235B-A22B-Instruct-2507), [Llama-4-Maverick](https://huggingface.co/meta-llama/Llama-4-Maverick-17B-128E-Instruct-FP8), [Gemma 4 26B-A4B](https://huggingface.co/google/gemma-4-26B-A4B-it), [gpt-oss-120b](https://huggingface.co/openai/gpt-oss-120b) and [DeepSeek-V3.1](https://huggingface.co/deepseek-ai/DeepSeek-V3.1).

## License

Code is [MIT](LICENSE). Public game logs and labels are CC-BY-4.0; model outputs also fall under their providers' terms.
