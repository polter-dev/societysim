# Demo plan

DRAFT. Part of #38. Some numbers wait on Marcus's 10-player batch close-out (#294) and the report from #37. The 3-minute talk track and slide outline are in [PITCH.md](PITCH.md).

How to read this file:

- `TODO(batch)` is a number that comes from a final batch report. Don't say it out loud until it is filled in. Never fill one with a guess.
- `TODO(verify)` is a number or URL that came from a status note or an issue comment and has not been re-checked against the live site or the report.
- Other `TODO(...)` tags name the issue that settles them.

## Contents

1. Who goes where
2. Main demo flow (3 to 5 minutes)
3. Track scripts: Hugging Face, AMD, Databricks, Neon, Gemini, Figma (plus Adobe, if that room is still on)
4. Backup plan
5. Pre-demo checklist
6. Code-freeze checklist
7. Reference: running and checking a batch
8. Open items

## 1. Who goes where

| Track | Presenter | Backup |
|---|---|---|
| Hugging Face | Marcus | Seth (takes it if Marcus's rooms overlap) |
| AMD | Marcus | none |
| Databricks | Marcus | none |
| Neon | Seth | none |
| Gemini | TODO(#38): not in the room split from the ticket. Decide who. | |
| Figma | Augustin | none |
| Adobe | Augustin | none; the Devpost draft doesn't list Adobe, so confirm the room is still on |

## 2. Main demo flow (3 to 5 minutes)

Site: https://society-sim.us. Use this for any judge who walks up, then switch to the track script. Navigation bar order on the site: Games, Leaderboard, Research, New game, Play, Open a log file.

Before you start, have two tabs ready: the demo game (open it with `?mode=narrator` appended, because the viewer opens in villager view and hides everything private) and the Leaderboard page. `TODO(batch)`: demo game id and the seq numbers to jump to. Get them from `uv run societysim highlights runs/<batch>`: take the top `lie_moved_vote` row, where `seq` is [lie seq, vote resolution seq] and `link` is a query string to append to the site URL (#160).

| Time | Page | What to do | What to say |
|---|---|---|---|
| 0:00 | Games (index) | Show the list. Point at the model names on each game. | "Models from different companies play Werewolf against each other. Every game is logged." |
| 0:30 | Replay | Open the demo game in Narrator view. Press Space to play, pause on the first accusation arrow. | "A Python engine owns the real game state. A model can lie but can't bend a rule." |
| 1:00 | Replay, stepper | Step with the arrow keys, or the step controls under the table. | "We can walk it one event at a time." |
| 1:15 | Replay, inspector | Click a wolf's seat. | "This is the private scratchpad next to what the wolf said out loud. Every turn also carries a suspicion score for every living player." |
| 1:40 | Replay, narration | Turn narration on (narration controls under the table). Let one or two clips play. | "Narration only reads public events. Private reasoning and the wolf channel are never voiced." Skip this step if the clips are missing for the demo game. |
| 2:00 | Replay, lie label | Jump to the labeled lie pop-up, then the vote it moved. | "We label a lie by comparing the claim with the engine's truth. The raw log is never edited." |
| 2:20 | Leaderboard | Open the page. Show win rate, then the detectors and liars views. | `TODO(batch)`: the one row to point at and the number to say. |
| 2:45 | Research | Open the deception matrix. | "Rows are the liar model, columns are the listener. Each cell is how often the listener's vote followed the lie, against how often it followed a true claim." `TODO(batch)`: one cell to read out, with its n. Say "exploratory" if n is small. |
| 3:15 | Model profile | From the leaderboard, click a model name. | "One page per model: how it does as wolf and as villager, how often it lies, how well its suspicion scores find the wolves." |
| 3:30 | Spot the wolf | Back in a replay, open the "Spot the wolf" panel under the table, guess a seat, step forward. | "You get the same public information a villager had. Can you beat the models?" |
| 4:00 | Live mode | Open a game that is still running (a running game shows a live badge). Needs the Neon deploy variables. If nothing is running, play the 60-second backup video instead. | "Games stream into Neon while they run. The page asks for anything newer every two seconds." |
| 4:30 | Play | Open the Play page. Show the model picker and the seat list. Start a game only if you have time and the play server is up (see backup plan). | "Visitors can take a seat at the table. There's a daily spend cap and a per-visitor limit." |

If you are running long, drop in this order: narration, model profile, play.

Do not claim anything from Live mode or Play that you did not see work during the pre-demo checklist.

## 3. Track scripts

Each script is about 60 seconds spoken. Each says what to click, what to say, and where the evidence lives. Say only the numbers that are filled in.

### Hugging Face: Best Use of Open Source AI

**Evidence:** HF Dataset with the game logs and labels (planned name `polter-dev/societysim-games`; Seth was asked to publish an earlier batch to `Sethery32/societysim-games`, `TODO(verify)` which one is final and public). HF Space with the viewer (`polter-dev/societysim`, `TODO(verify)` that it is up). Model list in `configs/models.yaml` and the README.

**Click path**
1. Space (or society-sim.us), game index. Point at the model names.
2. Open the demo game in Narrator view. Click a wolf's seat to show reasoning next to speech.
3. Leaderboard page.
4. Switch tabs to the Dataset page. Show the card and the files.

**Say**

> SocietySim is a social deduction benchmark for language models. Players sit down to a game of Werewolf. Most of them are open models served through Hugging Face Inference Providers: Qwen3 235B, Llama 4 Maverick, Gemma 4, gpt-oss-120b and DeepSeek V3.1. Gemini and GPT fill the other seats. A deterministic engine owns the rules, so a model can lie to the others but can't bend a rule. Every turn, each model writes private reasoning, a suspicion score for every other player, and one public move. After the game we label each lie and check whether it worked. Every game is published as a Hugging Face Dataset, and the replay viewer and leaderboard run on a Space. In our batch of TODO(batch) games, TODO(batch: model) won most often as a wolf.

**Numbers**
- Open models at the table: 5 of the 9 in `configs/mixed_v1.yaml`, all through HF Inference Providers.
- Smoke batch (#34): 10 of 10 games finished, about $0.02 per game.
- `TODO(batch)`: games played, total spend, win rates with confidence intervals, lie success, top of the leaderboard.

**If it breaks:** local viewer with the same game, and a screenshot of the dataset card.

### AMD: Best Use of AMD Technologies (ROCm)

**Evidence:** `infra/amd/README.md`, the `amd-gpt-oss-20b` and `amd-strix-gpt-oss-20b` entries in `configs/models.yaml`, the `## Speed` table in a report's `report.md`, the #114 results comment, the #253 results comment. The AMD self-play logs are in `runs/selfplay_amd/` on Marcus's laptop, not in git.

**Click path**
1. `infra/amd/README.md` on GitHub: `bash infra/amd/serve.sh` and `infra/amd/run_on_box.sh`.
2. The `amd-*` entries in `configs/models.yaml`: an OpenAI-compatible route to the local server.
3. The AMD self-play game in the viewer (from the laptop), scrub through one day.
4. The speed numbers below, on one slide.

Don't plan on a live pod. AUP sessions are time-boxed, the first start downloads several GB of wheels and about 14 GB of weights, and an AUP image pull failed earlier in this event.

**Say**

> We wanted one player at the table that we host ourselves. We run gpt-oss-20b on an AMD Radeon AI PRO R9700, 32 gigabytes, on AUP Learning Cloud, served by vLLM on ROCm. Guided decoding makes the model answer in our exact JSON schema. In a full seven-player game it made 30 moves with zero invalid outputs and zero fallbacks. Model time for the whole game was about six minutes. We also ran the same model as a GGUF on llama.cpp on a Radeon 8060S, a Strix Halo, in five self-play games with no errors. These are self-play games; the AMD model isn't in the mixed batch, because laptops can't reach the pod.

**Numbers (from #114 and #253)**
- R9700 vLLM, one game: 62 events, 30 model turns, 0 `invalid_output`, 0 fallbacks. 9,044 output tokens. Model time 373 s, about 12.4 s per turn. 33 to 41 tok/s for one stream.
- Strix Halo llama.cpp, 5 self-play games: 7.3 s per call, 39.5 output tokens/s, about 138 s per game, $0/h (hackathon-provided GPU). All five ended in a wolf win by parity on day 2, so these are short games.
- `TODO(#38)`: the hourly rate for imputed $/game. Until then say "no cost on the AUP quota", not a $/game.
- `TODO(batch)`: the AMD speed table from the final report, if a pod run lands before Sunday.
- Don't call AMD faster or slower than the API providers. The setups differ.

**If it breaks:** viewer replay of the AMD game from the laptop, plus the #114 and #253 comments.

### Databricks: Best Use of Databricks

**Evidence:** Databricks workspace: catalog `workspace.societysim` (`games`, `events`, `labels`, `batch_metrics`, `metrics_*`, `deception_pairs`), the AI/BI dashboard (id `01f1c4a8de811c60854d375219a83fbb` per Marcus's status, `TODO(verify)`), the MLflow experiment (`387213714723263`, `TODO(verify)`). Code in `notebooks/databricks/` and `scripts/databricks_ingest.py`.

**Click path**
1. Catalog Explorer: `workspace.societysim` and its tables.
2. Open the notebook `metrics.py`, scroll to the parity cell, show it passing.
3. Open the AI/BI dashboard: win rate by model and role, lie success, detection, then the deception view.
4. Experiments page: one MLflow run per batch.

**Say**

> We want to know which models lie and whose lies work, across every game we've played. One script loads every game into Delta tables: games, events, labels and a per-batch metrics table. A notebook ports our whole metrics layer to Spark SQL, and its last cell checks every value against our Python metrics and fails on any mismatch, so the dashboard and the leaderboard can't drift apart. The AI/BI dashboard sits on those tables: win rate by model and role, which lies swayed votes, how well villagers spotted wolves, and who fools whom. Each experiment batch is logged as an MLflow run. TODO(batch: model) had the most successful lies.

**Numbers**
- Live on real data per Marcus's status (#42): 58 games from `mixed_v1`, `selfplay_baseline` and `selfplay_amd`; win rates matched the report. `TODO(verify)`.
- Parity checks (#127, local PySpark): 1,050 on the fixture, 2,730 on a 25-game scripted batch, 0 mismatches.
- `TODO(#42)`: rerun on `mixed_v2_10p` after the batch, then quote its row count and parity result.
- `TODO(batch)`: top lie-success model, detection AUC per model.

**If it breaks:** screenshots of the dashboard and the parity cell, plus `uv run python scripts/databricks_ingest.py --fixture --dry-run` in a terminal.

### Neon: Best Use of Neon

**Evidence:** Neon console SQL editor; `neon/schema.sql`; the live site reading the Data API; the `leaderboard`, `leaderboard_by_batch` and `lies_by_model` views (and the detectors and liars leaderboard views from #344).

**Click path**
1. Neon console, SQL editor: `select status, count(*) from games group by status;` then `select * from leaderboard;`.
2. Stream a game live: `uv run societysim play --config <game config> --agents llm --neon` (paid; see the pre-demo checklist). Show events arriving in the SQL editor.
3. Pull one game in order: `select seq, type, actor, target from events where game_id = '<id>' order by seq limit 20;`
4. On society-sim.us, open the Leaderboard (served from Neon) and then a running game with the live badge.
5. Show `uv run societysim sync-neon runs/<batch>` output, or a screenshot of its `synced N games, N events, N labels` line.

**Say**

> Every game we run is a stream of events: speeches, accusations, night kills, votes. Neon Postgres holds all of them. Each game is a row in a games table, marked running, finished or failed. Each event is a JSONB row keyed by game and sequence number, so a schema change needs no migration. Labels sit in their own table, and leaderboard views compute win rates in SQL. Games stream into Neon while they run, and sync-neon backfills a batch you played without streaming. The website reads it directly: a short-lived anonymous token gets a read-only role that can select from a few tables and views, and nothing else. Events and labels for a running game stay hidden until it ends, so a spectator can't see secrets. For a game in progress, the page asks for new events every two seconds. Right now Neon holds TODO(batch) games and TODO(batch) events.

**Numbers and facts**
- Privacy (#323, applied live per Marcus's status): `events` and `labels` return 403 to the anonymous role; running games expose public events only through views. `TODO(verify)` on the day.
- Visitor play uses Neon for the spend ledger and for streaming public events. Only public events reach Neon while a game runs; the full log is backfilled after it ends.
- Labels in Neon were empty at the last note (#294). Until they are synced, lie-success columns in the leaderboard views are null. `TODO(batch)`: sync labels after the 10-player batch, then confirm `lie_success` is filled before you point at it.
- `TODO(batch)`: game, event and label counts.

**If it breaks:** the static site (reads `report/index.json`, no Neon) and the two SQL queries in the console.

### Gemini

Gemini is in the lineup and the Devpost draft lists the Gemini prize. No ticket exists for it, so this script is thin on purpose.

**Evidence:** `configs/models.yaml` (`gemini-flash-lite`, `gemini-flash`, through a Google AI Studio key); the leaderboard and model profile pages; the batch reports.

**Click path**
1. `configs/models.yaml`: the two Gemini entries.
2. Leaderboard, then the model profile for `gemini-flash` and `gemini-flash-lite`.
3. Research page: find the Gemini rows and columns in the deception matrix.

**Say**

> Gemini plays at the same table as the open models, as a wolf and as a villager, in both Flash and Flash-Lite. We rate every model the same way: win rate, lie success, and how well its suspicion scores find the wolves. TODO(batch): one sentence on what the Gemini models did best.

**Numbers**
- Last look, 7-player self-play baseline (#35 note, 45 games): `gemini-flash` had the highest detection score, about 0.91, ahead of `deepseek` (0.85) and `llama4` (0.81). `TODO(verify)` against the report before saying it, and call it a 7-player self-play result.
- Live leaderboard last seen: `gemini-flash-lite` the top detector, `gemini-flash` the top liar by suspicion received. `TODO(verify)` on the day.
- Honest caveat: `gemini-flash` calls were slow (45 to 389 s per call in the preflight, #35) and the free-tier rate limit is low. If asked, say so.
- `TODO(batch)`: Gemini rows from the final mixed report.

**If it breaks:** model profile screenshots.

### Figma: Best Design

**Evidence:** the Figma file with the flow wireframes and tokens (`TODO(#22)`: link; not started as of the last note), `docs/design/` if it exists by Sunday, `viewer/src/tokens.css` if PR #121 landed, the accessibility tests in `viewer/src`.

**Click path**
1. Games index, then open a game.
2. Without the mouse: Tab to the timeline, arrow through a few events, press End, then Home.
3. Switch the View select to Narrator. Click a seat for the inspector.
4. Click a lie marker on the timeline.
5. Switch back to Villager. Roles, reasoning and lie markers disappear until a villager could know them.
6. Open the Figma file. `TODO(#22)`.

**Say**

> We built the viewer for someone who has never played Werewolf. The flow is short: pick a game, watch it, click any player to see what they were thinking, and jump to the moments where someone lied. A toggle switches between the narrator's view, which shows every role, and a villager's view, which hides anything a villager couldn't know yet, so you can play along. The timeline has markers for every kill, vote and lie, and the whole thing works from the keyboard: arrows to step, Home and End to jump, Space to play. Tests run axe on the page in both views, and they check that the narrator view's secrets never reach the villager view. Reduced-motion mode turns animations off.

`TODO(#121)`: add "All colors, type and motion come from one token file, and tests check their contrast" only if that PR is merged. Check `viewer/src/tokens.css` exists on `main`.

**Numbers:** contrast was checked by hand in a browser (jsdom has no layout, so the axe color-contrast rule is off in tests). Don't say "fully WCAG compliant".

**If it breaks:** local viewer, the backup video, Figma screenshots.

### Adobe: Prettiest Hack (only if that room is still on)

Short version of the earlier script. Confirm which of these exist on `main` before the freeze (they came from PR #121 and the later illustrated-frontend work, PR #288).

> The replay is an illustrated round table. By day it is lit from the upper left, and at night the light shifts to moonlight and a candle glow. When a player dies, their role card flips face up. Accusations and votes draw in as curved arrows, arrowhead last. The speaking seat glows, and lie labels pop up just below. On a phone the whole table scales down together.

Click path: demo game with `?mode=narrator`, start at night 1, play into day 1, pause on a vote, jump to a death, narrow the window to phone width. Backup: screenshots on the `screenshots/issue-33` branch and the backup video.

## 4. Backup plan

Decide the fallback before you walk in, not in front of a judge.

| What fails | Fallback |
|---|---|
| society-sim.us is down or slow | Use the HF Space or the GitHub Pages mirror (https://polter-dev.github.io/societysim). Then the local viewer. |
| Venue wifi is down | Local viewer on the laptop with the demo game loaded through `?log=<url>` or "Open a log file". Run `cd viewer && npm ci && npm run dev` the night before. Phone hotspot is the next step. |
| Live mode has no running game | Play the 60-second backup video. Don't start a paid game on stage. |
| Play server | The HF Space `polter-dev/societysim-play` was paused by HF ("flagged as abusive", appeal sent) and the Render fallback `societysim-play` was being set up. Check `curl <VITE_PLAY_API_URL>/api/limits` before the demo. If it fails, skip the Play step and say visitor play is "built and rate limited, hosting is moving". Don't claim it is live. `TODO(verify)`: current host. |
| Neon console is slow | Screenshots of the two queries, plus the static site. |
| Databricks or MLflow is down | Screenshots of the dashboard and the parity cell. |
| AMD pod is not up | Never planned as live. Use the AMD self-play replay and the speed table. |
| Narration clips don't play | Skip the step. They're an extra, not the point. |

Backup assets, all stored on the laptop, not only online:
- Recorded video, 60 seconds for live mode, plus a 3-minute walkthrough of the main flow. `TODO(#38)`: record both after the code freeze and note the file path here.
- Screenshots: dashboard, parity cell, Neon queries, deception matrix, model profile, the Figma file, AMD speed table.
- Local copy of the demo game's `.jsonl`, its `.labels.jsonl`, and its `.audio/` folder.
- A copy of this file as plain text on the phone.

## 5. Pre-demo checklist

Free unless marked.

- [ ] Run the site smoke check (#362): `uv run python scripts/site_smoke.py` against https://society-sim.us (the script is not on `main` yet; if it still isn't, check each page by hand: index, a replay, leaderboard, Research, a model profile, New game, Play, Spot the wolf). Note any failure and fix or avoid that page.
- [ ] `uv run societysim doctor` (paid: makes a tiny structured call to every model in `configs/models.yaml`, so cost is a few cents; read the output for any model with no working mode). To check one model: `--model 'gemini-*'`.
- [ ] Warm the HF Space: open it and click through the index, a replay and the leaderboard until it's awake. A sleeping Space takes a minute to start. If the Space is paused or flagged, use the Cloudflare site and say nothing about the Space.
- [ ] Check the play server: `curl <play URL>/api/limits`. `remaining_today` above 0 and `client_ip_hint` showing your own masked IP mean it's healthy.
- [ ] Check the live site loads Neon data: the Leaderboard shows rows and the Games page lists games. If both fall back to static data, the Neon deploy variables are missing; say "static data" and don't claim live.
- [ ] Open the demo game in Narrator view; confirm the lie pop-up and the vote it moved are at the seq numbers written above.
- [ ] Narration: check the demo game has an `.audio/` folder and the clips play. If not, drop the step.
- [ ] Databricks: open the dashboard, confirm it renders and the parity cell is green.
- [ ] Neon console: open the SQL editor with the queries pasted in.
- [ ] Laptop: local viewer running, backup video and screenshots on disk, charger, phone hotspot.
- [ ] Every `TODO(batch)` in this file has either a number from the report or has been cut from the script.
- [ ] Rehearse each script once, out loud, with a timer. Each should land near 60 seconds.

## 6. Code-freeze checklist

The ticket sets the freeze at hour 44.

- [ ] Announce the freeze in the team channel. After this, only demo-blocking fixes, each approved by Marcus.
- [ ] Put the `hold` label on every open PR: `gh pr list --state open --json number --jq '.[].number' | xargs -I{} gh pr edit {} --add-label hold`. Check the list afterwards.
- [ ] Confirm the last `Deploy` run on `main` succeeded and the site shows the final batch (`DEPLOY_SOURCE` points at the final dataset).
- [ ] Confirm the Devpost submission has the final links, video link and gallery images.
- [ ] Re-run the pre-demo checklist after the final deploy.
- [ ] Record the backup video and take the screenshots from the frozen site.
- [ ] No new features, no schema changes, no config changes to deployed hosts.

## 7. Reference: running and checking a batch

Real batches need `--agents llm` (the default is scripted, which costs nothing). Only the `--agents llm` line spends money. Don't run a paid command during demo prep.

```bash
uv run societysim batch --config configs/smoke_batch.yaml --dry-run        # plan and cost estimate, runs nothing
uv run societysim batch --config configs/smoke_batch.yaml --agents llm     # paid: real model calls
uv run societysim label runs/smoke_batch --no-jev                          # free labels only, no Jev
uv run societysim report runs/smoke_batch
uv run societysim highlights runs/smoke_batch
uv run python -m societysim.analysis.narrate runs/smoke_batch/<game_id>.jsonl --dry-run
uv run python -m societysim.analysis.postbatch runs/<batch>                # label, report, highlights, narrate dry run, prints next commands
```

- `narrate` is a module, not a `societysim` subcommand. `--dry-run` only prints the clip and character count; the real run needs `ELEVENLABS_API_KEY` and is billed per character.
- 10-player batch (estimate $13.62 against a $15 cap, 100 games): `uv run societysim batch --config configs/mixed_v2_10p.yaml --agents llm --dry-run`. Marcus's run is `mixed_v2_10p` (#294).
- Use `label --no-jev` for free or test runs. Without `--no-jev`, and with `TYPESAFE_API_KEY` set, `label` also calls Jev, which costs money.
- A scripted run of `configs/smoke_batch.yaml` writes to the same folder as the real run, and the real run then stops with a mismatch error. Delete `runs/smoke_batch` between the two.
- If a model fails 10 calls in a row with no tokens, the batch stops starting new games (`provider_down`), lets running games finish, and exits with status 1. Fix the provider and run the same command again: finished games are skipped (#177).
- Each call times out after 120 seconds; set `timeout_s` on a provider in `configs/models.yaml` to change it (#181).
- Failed and empty replies are never written to the response cache (#188).
- Deploy a real batch: `bash scripts/deploy-space.sh --out /tmp/site --force runs/<batch>`, then `npx wrangler login` once and `npx wrangler pages deploy /tmp/site --project-name societysim --branch main`. Or set the `DEPLOY_SOURCE` repo variable to `hf:<owner/name>[:<batch_id>]` and rerun `Deploy`. Live Neon games on the site need the repo variables `VITE_NEON_DATA_API_URL` and `VITE_NEON_AUTH_URL` (README, deploy section).
- Visitor play kill switch: set the Space variable `PLAY_ENABLED=0` and restart (README, "Visitor play").

## 8. Open items for #38

- Every `TODO(batch)`: wait for Marcus's 10-player close-out (#294), then `postbatch`, the HF publish, Neon label sync and the Databricks rerun (#42).
- Every `TODO(verify)`: re-check against the live site and the report.
- Demo game id and the seq numbers to jump to.
- HF Space and Dataset URLs, and whether the Space appeal was accepted.
- Who presents Gemini, and whether the Adobe room is on.
- Figma file link (#22) and whether PR #121's token file landed.
- The AMD hourly rate for imputed $/game.
- Play server host (HF Space paused vs Render).
- Rehearsal, backup video, and the room split: the ticket's acceptance criteria that only humans can close.
