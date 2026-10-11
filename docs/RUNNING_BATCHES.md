# Running experiment batches

How to run a real batch end to end: check, dry run, play, label, report, publish. Every command here is on `main`; flags come from `societysim <command> --help`. Paid steps are marked **paid**.

Examples to copy from: `configs/selfplay_baseline_10p.yaml` (9 models, 10 games each, self-play) and `configs/mixed_v2_10p.yaml` (100 mixed games, seats rotate through all 9 models).

## 1. Before you start

- **Install.** `uv sync --frozen`.
- **Keys.** Copy `.env.example` to `.env` and fill in the providers your config uses. The README lists which key does what. A real run exits early with `missing provider key: ...` if a model's key is unset. Never paste, print or commit `.env`.
- **Credits.** Check the balance on each provider you are about to use. A provider that runs out of credit mid-batch fails every call (see "Circuit breakers").
- **Check the models.** `uv run societysim doctor` (**paid**, a few cents) sends a tiny structured call to every model in `configs/models.yaml` in every structured mode and prints latency and cost. It exits 1 if a model has no working mode or its configured `structured` mode fails. Narrow it with `--model 'gpt-5*'` (a glob).
- **Game size.** 10-player games are the default, in the config schema too. Configs that stay 7-player (the early samples) set `n_players: 7` explicitly so their fingerprints don't change. Setting it in every batch config is still the clearest:

  ```yaml
  game:
    n_players: 10
  ```

  Both example configs do.

## 2. Dry run first

```bash
uv run societysim batch --config configs/selfplay_baseline_10p.yaml --dry-run
```

It plays nothing and calls no model. It prints one line per game (seed, estimated cost, `seat=model/role`) and a total against `budget_usd`. It warns if a model has no price in `models.yaml`; the budget cap can't see the cost of an unpriced model.

The estimate is price-based and conservative. It assumes 12 calls, 2k input and 400 output tokens per seat. Real spend has run about 1.5 to 4 times lower (T5.1 and T5.2). Real example: a 20-game 10-player self-play batch cost $2.45.

Set `budget_usd` near the dry-run total. The cap is a hard stop, and the guard reserves each game's estimate before starting it, so a cap well under the estimate starts fewer games than the money would allow.

## 3. Run it

### Private batch seed salt

`SOCIETYSIM_SEED_SALT` is off by default. Set it to a random value, for example one made with `secrets.token_hex(16)`, before starting a batch. Set the same value again for every run and dry run of that batch. A different or missing salt stops a resumed run with an error that names `SOCIETYSIM_SEED_SALT`; set the same salt (or unset it for an unsalted batch) and resume, and do not delete the directory. The deal in a salted batch cannot be rebuilt from the committed config alone. The salt is not written to the batch output.

The salt also changes which seats get which roles, so it changes the wolf and listener pairs. `configs/mixed_v2_10p_gpt.yaml` was tuned for n >= 5 games per GPT pair on the unsalted plan only, and most salts break that. A `--dry-run` with the salt set prints games per (wolf model, listener model) for the plan it would actually run, and warns about pairs under 5. For a salted run of that config, dry-run first, and if it warns, pick another salt and dry-run again before any game starts.

```bash
uv run societysim batch --config configs/selfplay_baseline_10p.yaml --agents llm   # paid
```

**`--agents llm` is required for a real run.** The default is `scripted` (random players, free, no model calls). A scripted run writes `runs/<batch_id>/` and records `agents: scripted` in `run_meta.json`. Running the same config with `--agents llm` afterwards stops with `was played with scripted agents, not llm`. Use a new `batch_id` or delete the directory first.

Output goes to `runs/<batch_id>/`: one `<game_id>.jsonl` per game, `<game_id>.run_meta.json`, and `run_meta.json` for the batch. `runs/` is gitignored.

Optional: `--neon` also streams events to Neon live (needs `NEON_DATABASE_URL`). A Neon problem is only a warning; the batch goes on without it. `--registry` points at a different model registry (default `configs/models.yaml`).

### Reading the progress line

```
[3/90] selfplay_baseline_10p-g0002 finished (villagers) in 4m12s | spent $0.31 of $12.00 | 8 running | ETA ~1h20m
```

`ETA estimating…` shows until the first game of this run finishes. After that the ETA is games left divided by the observed finish rate, so it already includes concurrency and slow models. 10-player games take about 2 to 17 minutes each at 7 to 8 in flight, so expect the first few lines to be slow and the ETA to move around.

`max_concurrency` in the config is games in flight. Per-provider caps live in `configs/models.yaml`.

### Budget cap and circuit breakers

The batch stops starting new games when any of these hit. Games already running always finish.

| Stop | Status | What it means | Exit code |
|---|---|---|---|
| Budget | `budget_stopped` | Spend so far, plus what running games still need, plus the next game's estimate, would pass `budget_usd`. | 0 |
| Invalid output | `stopped_invalid` | A model's `invalid_output` rate passed `max_invalid_rate` (default 0.25) after at least `min_calls_for_invalid_check` calls (default 40). The rate is invalid outputs the model caused, over the model's call attempts. Provider failures (5xx, 429, timeout; `meta.reason == "provider"`) count as neither, so a provider outage does not trip it (#363). `run_meta.json` has `invalid_stop` naming the model. | 2 |
| Provider down | `failed` | A model failed 10 calls in a row with no tokens back (out of credit, quota, revoked key). `run_meta.json` has `provider_down`. | 1 |
| A game raised | `failed` | Listed under `failed` in `run_meta.json`. | 1 |

`stopped_invalid` means a model is producing JSON the engine can't use, so its games would be mostly fallbacks. Look at that model's `invalid_output` events before you raise the limit, and skip the ones with `meta.reason == "provider"`: those are provider errors, not bad JSON, and they don't count toward this stop. For provider trouble, check `provider_errors` and `provider_error_rate` per model in `report/metrics.json`; raising `max_invalid_rate` won't help with those. To continue anyway, raise `max_invalid_rate` in the config and rerun. The check also covers games already on disk, so rerunning without raising it stops again at once.

After the run, `report/metrics.json` also has, per model, `fallback_rate` (turns that ended in a fallback move), `retry_success_rate` (retries whose second attempt was accepted) and `invalid_attempt_rate` (rejected replies per call attempt, which still counts a retry that worked, so it overstates the turns lost) (#408). None of them count provider errors.

Other final statuses: `completed`, `interrupted` (Ctrl-C or kill), `running` (still going, or killed hard before it could save).

## 4. Stop and resume

Stop with Ctrl-C (the batch cancels running games and saves `interrupted`; exit code 130) or kill the process. To resume, **run the same command again**.

- A game counts as finished only if its log ends with a `game_end` event. Finished games are skipped. Any other game is replayed from scratch and its log overwritten.
- Money already spent, including on games that were cut off, stays in the `run_meta.json` ledger and counts against the budget on resume.
- You may change `n_games`, `budget_usd`, `max_concurrency`, `max_invalid_rate` and `min_calls_for_invalid_check` between runs. Changing the seed, the model list, `mode`, any `game:` setting, a model's `models.yaml` entry, or `--agents` makes the batch a different one, and the run stops with a mismatch message. Use a new `batch_id` instead.
- `CorruptMeta`: `run_meta.json` exists but can't be parsed, so the runner refuses to treat the directory as new. It is written through a temp file, so a kill normally leaves the old copy intact; corruption usually means a hand edit or a full disk. Fix the file, or delete it after checking the game logs. If you delete it, the ledger is gone and spend is recounted from finished games only, so money spent on cut-off games drops out of the budget count.

## 5. After the run

All of this works from the batch directory. Start with the free steps.

```bash
uv run societysim label runs/<batch_id> --no-jev      # free: rule-based labels
uv run societysim report runs/<batch_id>              # free: report/ with report.md, metrics.json, charts, leaderboard.json, index.json
```

`uv run python -m societysim.analysis.postbatch runs/<batch_id>` runs label (no Jev), report and highlights in one go and prints the commands for the steps below. It never runs them.

**Jev (paid).** `label` without `--no-jev` calls Jev whenever `TYPESAFE_API_KEY` is set, and the CLI loads `.env` itself, so a plain `label` on your machine spends money. It caches answers in `<batch>/.cache/jev` and writes `agreement.json`. If Jev fails, the rule-based labels are still written. Use `--no-jev` for test and free runs.

The raw `<game_id>.jsonl` logs are never edited. Labels go in `<game_id>.labels.jsonl` next to them.

### Publish and sync

```bash
uv run societysim publish runs/<batch_id> --repo <org>/<name>   # needs HF_TOKEN with write access
uv run societysim sync-neon runs/<batch_id>                     # needs NEON_DATABASE_URL
```

- `publish` pushes logs, labels, parquet tables and a dataset card to a Hugging Face Dataset. The repo is **private** if it gets created; `--public` is opt-in, so ask the team first. `--repo` defaults to `$SOCIETYSIM_HF_DATASET`. Run `label` first, or the labels are missing. It is safe to rerun: files are named per game and get overwritten. The dataset card is one aggregate card for the whole repo (#335): each publish merges the new batch into `manifest.json` and rewrites the card, so it always describes every batch in the repo.
- `sync-neon` creates the tables if needed, then backfills the batch and prints `synced N games, N events, N labels`. It is the same data `--neon` streams live.
- To put the batch on the site, see the deploy notes in the README (`scripts/deploy-space.sh`, Cloudflare Pages).
- After a deploy, check the live site (read-only, free): `uv run --extra smoke python -m playwright install chromium` once, then `uv run --extra smoke python scripts/site_smoke.py` (#370, #381). It loads each page headless, including the play start screen (bare `?page=play`), and exits 1 if one is broken. It never starts a game: a guard in the browser aborts any POST, PUT, PATCH or DELETE to `/api/...` and fails the run if a page tries one. The last table row, `guard / no play API writes`, should say 0 writes were attempted. `--strict-play` is still accepted but does nothing.

## 6. Safety

- **Tests never see real keys** (#336). `pytest` strips every key, token and DSN from the environment and turns off `.env` loading. Don't set `SOCIETYSIM_LIVE=1` to get around this. It puts the real keys back and runs the tests marked `live`, which call paid APIs and real databases. Only set it for a single deliberate live check, and unset it after.
- **Neon migrations** go through `sync-neon --migrate` only. Without `--apply` it prints the SQL and runs nothing. `--migrate --apply` changes the live database and needs Seth and Marcus to say OK first. Migration `001_events_inserted_at.sql` is already applied on the live database, so `--prune` works there; a new database made from `neon/schema.sql` already has the column.
- **`--prune` is a dry run first.** `sync-neon --prune` lists partial, fixture and test games (partial means older than `--older-than-hours`, default 6) and deletes nothing. Read the list, then add `--apply`. `--batch <id>` adds a test batch to the list; `sync-neon --delete <game_id>` removes one game.
- Don't run paid commands (`--agents llm`, `doctor`, Jev `label`, `uv run python -m societysim.analysis.narrate <game_id>.jsonl` or `--build-bank --yes` without `--dry-run`, which are billed per character) without checking the dry-run estimate and your `budget_usd` first.
- Keys stay in `.env`. Don't put `NEON_DATABASE_URL` in a CI variable or a viewer build; it can write.

## 7. Windows notes

- Game logs are written as UTF-8 (#230). Logs from before that fix may hold cp1252 bytes; they still load (with a warning, once per file) and new files are always UTF-8.
- `PYTHONIOENCODING` is not needed. `doctor` already re-encodes provider error text that the console can't show.
- Run the deploy scripts (`scripts/deploy-space.sh`) from Git Bash: `bash scripts/deploy-space.sh ...`.
- If `runs/` sits in a OneDrive folder, a sync can lock `run_meta.json` for a moment. The runner retries for about two seconds. If you still see a permission error, pause OneDrive or move the checkout out of it.
