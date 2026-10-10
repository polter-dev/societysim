# Demo scripts

Six sponsor rooms on Sunday (hours 42 to 46), one 60-second pitch each. Every section has the spoken script (about 150 words), the click path, the numbers behind it, and what to do if the live demo breaks.

`TODO(batch)` marks a number that comes from the T5.2 self-play batch (#35) or the T5.3 mixed batch (#36). Fill those in #38 once the batches finish and #37 has run `label`, `report` and `publish`. Other `TODO(...)` tags name the open issue that settles them.

## Who goes where

| Track | Presenter | Backup |
|---|---|---|
| Hugging Face | Marcus | Seth (takes it if Marcus's rooms overlap) |
| AMD | Marcus | none |
| Databricks | Marcus | none |
| Neon | Seth | none |
| Figma | Augustin | none |
| Adobe | Augustin | none |

## Before walking in

- Open the HF Space on the game index, a finished game, and the leaderboard page. `TODO(#37)`: Space URL and Dataset URL.
- Have the backup video from #38 on the laptop, not only online.
- Keep a local viewer running (`cd viewer && npm ci && npm run dev`) with one mixed-batch game loaded through `?log=<url>`, in case the Space or the venue wifi is down.
- Pick the demo game ahead of time: a mixed game with at least one labeled lie that moved a vote. `TODO(batch)`: game id and the seq numbers to jump to.

## Hugging Face: Best Use of Open Source AI

### Script

> SocietySim is a social deduction benchmark for language models. Seven players sit down to a game of Werewolf. Most of them are open models served through Hugging Face Inference Providers: Qwen3 235B, Llama 4 Maverick, Gemma 4, gpt-oss-120b and DeepSeek V3.1. Gemini and GPT fill the other seats. A deterministic Python engine owns the rules, so a model can lie to the other players but can't bend a rule. Every turn, each model writes private reasoning, a suspicion score for every other player, and one public move. After the game we label each lie and check whether it worked. In our batch of TODO(batch) games, TODO(batch: model) won most often as a wolf, and lies swayed TODO(batch) percent of the votes they targeted. A cheap open-model game costs about two cents. Every game is published as a Hugging Face Dataset, and this replay viewer and the leaderboard run on a Hugging Face Space.

### Demo path

1. Space, game index. Point at the model names on each game.
2. Open the demo game. It opens in villager view, which hides reasoning, suspicion, lie markers and label pop-ups, so switch the View select to Narrator first (or open the game with `?mode=narrator`).
3. Press Space to play, then pause on the first accusation arrow.
4. Click a wolf's seat. The inspector shows its private reasoning next to what it said in public.
5. Jump to the labeled lie (pop-up over the table), then to the vote it moved.
6. Leaderboard page: win rate by model. `TODO(batch)`: which rows to point at.
7. Switch tabs to the HF Dataset page and show the dataset card and the files.

### Numbers

- Open models at the table: 5 of the 9 in `configs/mixed_v1.yaml`, all via HF Inference Providers (`configs/models.yaml`).
- Smoke batch (#34): 10 of 10 games finished, about $0.02 per game, 19 games an hour at concurrency 4, no rate-limit errors.
- Gemma 4 invalid output went from 48% in the smoke batch to 0 in 3 games after #149.
- `TODO(batch)`: games played, total spend, win rates with confidence intervals, lie success, top of the leaderboard.

### Backup if the live demo fails

Run the local viewer with the same game, and show the dataset card from a screenshot.

## AMD: Best Use of AMD Technologies (ROCm)

### Script

> We wanted one player at the table that we host ourselves. We run gpt-oss-20b on an AMD Radeon AI PRO R9700, 32 gigabytes, on AUP Learning Cloud, served by vLLM on ROCm. vLLM's guided decoding makes the model answer in our exact JSON schema, and our health check passes in all three structured-output modes. In a full seven-player game it made 30 moves with zero invalid outputs and zero fallbacks. Model time for the whole game was about six minutes, around 12 seconds a turn, at 33 to 41 tokens a second for a single stream. The server was up for 0.41 hours and cost us nothing on the AUP quota. At the default reasoning effort a game couldn't finish within a session, so we set it to low. Calls run one at a time and the GPU sat mostly idle, so parallel turns are the next step.

### Demo path

1. Show `infra/amd/README.md` on GitHub: the one-command bring-up (`bash infra/amd/serve.sh`) and the in-pod run (`infra/amd/run_on_box.sh`).
2. Show the `amd-gpt-oss-20b` entry in `configs/models.yaml`: an OpenAI-compatible route to the local vLLM server, with `json_schema` structured output.
3. Load the AMD self-play game in the viewer (the log is in `runs/selfplay_amd/` on Marcus's laptop) and scrub through one day.
4. Show the numbers below on one slide or in the #114 results comment.

Don't plan on a live pod. AUP sessions are time-boxed, and the first start downloads several GB of wheels and about 14 GB of weights.

### Numbers (from #114)

- Hardware: AMD Radeon AI PRO R9700, 32 GB, gfx1201, AUP Learning Cloud (Dublin). vLLM on ROCm serving `openai/gpt-oss-20b`.
- `doctor` in the pod: `json_schema` 2.67 s, `json_object` 1.70 s, `none` 1.75 s.
- One `selfplay_amd` game (7 players): 62 events, 30 model turns, 0 `invalid_output`, 0 fallbacks. Wolves won by parity.
- 9,044 output tokens (about 300 per turn). Model time 373 s (about 6.2 min, 12.4 s per turn).
- 33 to 41 tok/s for one stream, about 0.33 s to the first token.
- Before #151 (default reasoning effort): about 33 tok/s and only 32 events in more than 15 minutes.
- GPU while idle: 3% busy, 23 W, 90% of VRAM allocated (KV cache).
- Cost: served 0.41 h, $0 on the AUP quota. Imputed $/game is 0.41 h times `AMD_PRICE_PER_HOUR`, and we call it imputed. `TODO(#38)`: pick the hourly rate and the source for it.
- For comparison, a cheap HF game in the smoke batch (#34) cost about $0.02 and took about 9 minutes of wall clock (median, 4 games at once). The setups differ, so present these side by side without calling one faster.
- `TODO(batch)`: the AMD model isn't in the T5.2 or T5.3 batch configs, because laptops can't reach the pod. If someone runs a mixed batch inside the pod before Sunday, add its win rate here and say "plays next to the API models". Otherwise the pitch stays as written above (self-play only).

### Backup if the live demo fails

The viewer replay of the AMD game from the laptop, plus the #114 results comment.

## Neon: Best Use of Neon

### Script

> Every game we run is a stream of events: speeches, accusations, night kills, votes. Neon Postgres holds all of them. Each game becomes a row in a games table, marked running, finished or failed, along with its models and winner. Each event lands in an events table as JSONB keyed by game and sequence number. Labels sit in their own table, and a leaderboard view computes win rates in SQL. Because events are stored whole as JSONB, a schema change needs no migration. When a batch finishes, one command, sync-neon, backfills every game, and it's safe to re-run. A read-only anonymous role can select from those four objects and nothing else, so a browser can query them without a backend. Right now Neon holds TODO(batch) games and TODO(batch) events.

`TODO(#120/#157)`: only if held PR #120 (viewer reads the Data API) and #157 (`NeonSink` attached during games) both merge before Sunday, replace the sync-neon sentence with: "The viewer calls the Neon Data API directly, and for a game that's still running it asks for anything newer than the last sequence number every two seconds, so you can watch it live." Until then the script above is the one to say.

### Demo path

1. Neon console, SQL editor: `select status, count(*) from games group by status;` then `select * from leaderboard;`.
2. Show `uv run societysim sync-neon runs/<batch>` and its `synced N games, N events, N labels` line (or a screenshot of it).
3. Back in the SQL editor, pull one game's events in order: `select seq, type, actor, target from events where game_id = '<id>' order by seq limit 20;`
4. `TODO(#120/#157)`: only if both merge, open the viewer built against Neon (index and leaderboard from the Data API), then a running game with new events arriving every 2 s.

### Status and numbers

- Merged (#39): `neon/schema.sql` (`games`, `events`, `labels`, `leaderboard` view, SELECT granted to `anonymous` only), `NeonSink`, and `societysim sync-neon runs/<batch>` for backfill.
- Open, on `hold` for Augustin: PR #120 (#40), the Neon `GameSource` with the 2-second live poll. The viewer part of this pitch depends on it.
- `TODO(#40)`: confirm a bare request with no `Authorization` header returns 200 from the Data API, and that CORS allows the Space's origin.
- `TODO(#157)`: `NeonSink` exists, but `play` and `batch` don't attach it yet, so games reach Neon through `sync-neon` after they finish. #157 wires it in for live spectating.
- `TODO(batch)`: game, event and label counts after the T5.2/T5.3 backfill.

### Backup if the live demo fails

Show the static Space (it reads `report/index.json`, no Neon) and run the two queries in the Neon console.

## Databricks: Best Use of Databricks

### Script

> We want to know which models lie and whose lies work, across every game we've played. We load every game into Delta tables on Databricks Free Edition with one script: games, events, labels, and a per-batch metrics table. A notebook ports our whole metrics layer to Spark SQL: win rates, lie counts by type, lie success, detection and persuasion. Its last cell checks every value against our Python metrics and fails on any mismatch, so the dashboard and the leaderboard can't drift apart. On our test data that was over 3,700 checks with zero mismatches. The AI/BI dashboard is built from those tables: win rate by model and role, which lies swayed votes, how well villagers spotted wolves, and games over time. Each experiment batch is also logged as an MLflow run. In the mixed batch, TODO(batch: model) had the most successful lies.

### Demo path

1. Catalog Explorer: `workspace.societysim` with `games`, `events`, `labels`, `batch_metrics` and the `metrics_*` tables.
2. Open `metrics.py`, scroll to the parity cell, and show it passing.
3. Open the AI/BI dashboard: win rate by model and role, then the lie success chart, then detection.
4. Experiments page: one MLflow run per batch, with params and metrics.

### Numbers

- Ingest tested live against Free Edition with the fixture (#42): it created `workspace.societysim.{games,events,labels,batch_metrics}`.
- Notebook parity, run on local PySpark (#127): 1,050 checks on the fixture and 2,730 on a 25-game scripted batch, 0 mismatches.
- Dashboard (#131): five datasets (win rate by model and role, lie success, detection AUC and Brier for village seats, persuasion, games over time).
- `TODO(#42)`: dashboard published on the live workspace, MLflow runs logged, parity cell green on the real batches.
- `TODO(batch)`: row counts, top lie-success model, detection AUC per model.

### Backup if the live demo fails

Screenshots of the dashboard and the parity cell, plus `uv run python scripts/databricks_ingest.py --fixture --dry-run` in a terminal.

## Figma: Best Design

### Script

> We built the viewer for someone who has never played Werewolf. The flow is short: pick a game from the index, watch it, click any player to see what they were thinking, and jump straight to the moments where someone lied. The table shows who is speaking, who accused whom, and how the votes are adding up. A toggle switches between the narrator's view, which shows every role, and a villager's view, which hides anything a villager couldn't know yet, so you can play along. The timeline has markers for every kill, vote and lie, and the whole thing works from the keyboard: arrows to step, Home and End to jump, Space to play. Our tests run axe on the full page in both views at night, during a tie vote, on a death and at game end, and they check that the narrator view's secrets never reach the villager view. Reduced-motion mode turns the animations off.

`TODO(#121)`: add "All colors, type and motion come from one design token file, and tests check their contrast." only if held PR #121 merges.

### Demo path

1. Game index, then open a game.
2. Without the mouse: Tab to the timeline, arrow through a few events, press End, then Home.
3. Switch the View select to Narrator (the viewer opens in villager view, which has no reasoning, suspicion or lie markers).
4. Click a seat. The inspector shows private reasoning and suspicion bars.
5. Click a lie marker on the timeline. The label pop-up names the lie.
6. Switch the View select back to Villager. Roles, reasoning and lie markers disappear until a villager could know them.
7. `TODO(#22)`: the Figma file with the flow wireframes and tokens.

### What exists

- Merged: index and leaderboard pages (#31), table with seats and day/night tint (#24), speech bubbles and chat log (#25), accusation and vote arrows with a live tally (#26), label pop-ups (#27), narrator/villager toggle (#28), inspector (#29), timeline scrubber with markers, playback and keyboard control (#30), accessibility pass (#32): axe runs in tests in both views with its color-contrast rule off, because jsdom has no layout; contrast was checked by hand in a browser.
- Open, on `hold` for Augustin: PR #121 (#33) moves every design value into `viewer/src/tokens.css` and checks contrast in tests. The token line in the script depends on it.
- Not started: #22, the Figma wireframes and tokens.

### Backup if the live demo fails

The local viewer, or the backup video, plus Figma screenshots.

## Adobe: Prettiest Hack

### Script

> The replay is an illustrated round table. By day it is lit by sunlight from the upper left, and at night the light shifts to moonlight and a candle glow. When a player dies, their role card flips face up. Every role gets the same neutral card, so a seat's color never gives away a hidden role. Accusations and votes draw in as curved arrows, arrowhead last, and each one bows to its own side, so two players accusing each other don't share a line. The speaking seat glows, its bubble sits above the table, and lie labels pop up just below. A wolf turning on a fellow wolf gets a teal marker, so it never reads as the red of a kill. Headings use a serif display face, and every color is a design token that passes contrast checks in day and night lighting. On a phone, the whole table scales down together.

### Demo path

1. Open the demo game with `?mode=narrator` (lie labels only show in narrator view), at night 1, to show the moonlight and candle glow.
2. Play into day 1. The light crossfades to day.
3. Pause on a vote with arrows going both ways between two players.
4. Jump to a death. The role card flips.
5. Narrow the window to phone width.

### What exists

- All of the above is in PR #121 (#33), open and on `hold` for Augustin to merge or rework. If it isn't merged by the code freeze, this pitch has nothing to show, so it's the first thing to settle in #38.
- Contrast in PR #121: arrows at 4.12 to 8.57:1 on the table and cloth in both lightings, seat card text at 6.13 or higher.
- Before and after screenshots are on the `screenshots/issue-33` branch.

### Backup if the live demo fails

The before and after screenshots from PR #121, and the backup video.

## Open items for #38

- Every `TODO(batch)` above, once T5.2 and T5.3 are labeled and reported.
- Space and Dataset URLs (#37).
- Neon: whether PR #120 and #157 merge (they decide which Neon script to use), and the bare Data API request check (#40).
- Databricks dashboard and MLflow on the live workspace (#42).
- Viewer PRs #120 and #121, and the Figma file (#22): Augustin decides.
- The AMD hourly rate used for imputed $/game.
