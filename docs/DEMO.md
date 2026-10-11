# Demo plan

DRAFT. Part of #38. Numbers here were checked against the live site, the Neon read views and the batch reports on 2026-10-11 (about 03:45 UTC, before the code freeze). The 3-minute talk track and slide outline are in [PITCH.md](PITCH.md).

How to read this file:

- `TODO(batch)` is a number that comes from a final batch report. Don't say it out loud until it is filled in. Never fill one with a guess. None are left; a 40-game GPT-coverage batch (#306) is still running, and nothing here depends on it.
- `TODO(verify)` is a number or URL that came from a status note or an issue comment and has not been re-checked against the live site or the report. One is left, on the Databricks MLflow id.
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

Site: https://society-sim.us. Use this for any judge who walks up, then switch to the track script. The site opens on an illustrated landing page (Play, How to play, About) with no navigation bar. The other pages, except the mode picker and Advanced setup below, have one, in this order: Home, Games, Leaderboard, Research, New game, Play, Open a log file. Play opens a mode picker ("Choose your fate": Normal, Watch, Advanced), not a game. The mode picker and Advanced setup have only a back arrow in the corner (the picker's goes to the landing page, Advanced's goes back to the picker), and the browser Back button works too.

The game screen (replay and live play) is one fixed screen with no page scroll at laptop size (#465, checked live at 1037 x 783): an Agents list on the left, the round table in the middle with the day or night banner above it, the Game events list on the right, and a bottom bar with the beat arrows, Play, Speed, an Event slider and the timeline markers. Three buttons in the bottom right open panels: Discussion and votes, Spot the wolf, Narration. Use a laptop-size window, not a phone.

Before you start, have two tabs ready: the demo game (open it with `?mode=narrator` appended, because the viewer opens in villager view and hides everything private) and the Leaderboard page. Demo game: `mixed_v2_10p-g0043`, open https://society-sim.us/?game=mixed_v2_10p-g0043&seq=49&mode=narrator (the address bar rewrites `seq=49` to `step=49`; both land on event 49, shown as "Event 49, 50 / 241"). It is the top `lie_moved_vote` row from `uv run societysim highlights runs/mixed_v2_10p`: at seq 48 to 49, Hugo (p5, gemini-flash, a wolf) accuses his own partner Iris (p2, gpt-oss-120b). All 8 village voters then vote Iris out (votes seq 69 to 78, elimination seq 79). Hugo survives and the wolves win by parity on day 5. Be honest about it: four players had already accused Iris before Hugo joined in, so he rode the pile-on rather than starting it.

| Time | Page | What to do | What to say |
|---|---|---|---|
| 0:00 | Games (index) | Open `https://society-sim.us/?page=games` (or the landing page, About, then "Browse games"). The list opens with `visitor-...` rows, which are test and visitor games (many failed or cut off, winners hidden); the 100 `mixed_v2_10p-g...` games sit further down, so scroll or skip to the demo game link. Winners are hidden until you tick "Show winners (spoilers)". Point at the model names on each game. | "Models from different companies play Werewolf against each other. Every game is logged." |
| 0:30 | Replay | Open the demo game in Narrator view. Press Space (or click Play in the bottom bar) to play, and pause on the first accusation arrow. At event 49 the red arrows on Iris are already drawn. | "A Python engine owns the real game state. A model can lie but can't bend a rule." |
| 1:00 | Replay, stepper | Step with the arrow keys, the ◀ ▶ beat buttons at the bottom left, or drag the Event slider. | "We can walk it one event at a time." |
| 1:15 | Replay, inspector | Click Hugo's plate (a wolf). A dialog opens with his private reasoning, a suspicion bar for every other player, and his decision history; Esc closes it. | "This is the private scratchpad next to what the wolf said out loud. Every turn also carries a suspicion score for every living player." |
| 1:40 | Replay, narration | **Skip this step for the demo game on the live site.** The Narration panel (bottom right) says "Audio is not available for this game yet," because the CI deploy has no `.audio` folder (the browser console shows a 404 for `mixed_v2_10p-g0043.audio/index.json`). Do it only from a local deploy that has the clips (section 7), where you turn Narration on and let one or two clips play. | "Narration never voices a player's private reasoning. By default it reads only public events. Wolf-chat and highlight clips exist only if they were generated with `--select`, and the viewer plays them only in Narrator view, never in villager view." |
| 2:00 | Replay, lie label | On the timeline, click the red **B** at event 49 (tooltip "Bussing: Hugo, event 49"). The pop-up under the table reads "bussing, Hugo, self tag, 100% confidence: Hugo (p5), a wolf, accused fellow wolf Iris (p2)." Then click the vote markers at events 69 to 78 and the elimination cross at event 79. | "We label a lie by comparing the claim with the engine's truth. The raw log is never edited." |
| 2:20 | Leaderboard | Open the page. Show win rate by model. Scroll to **Global rankings**: the **Detectors** tab is open (best wolf detectors, as villagers), then click **Liars** (best liars, as wolves). Both read Neon and refresh every 45 seconds. They are off when the URL has `?base=` or `?index=`. | Point at gemini-flash, the top row: "Gemini Flash won 65% of its seats over 100 ten-player games, 57% to 73% at 95% confidence, and it's first on OpenSkill." For the Global rankings: "These two tables count every finished game on the site, visitor games included." They show finished, non-scripted games only, with model names and no player handles. Models with fewer than 5 games are listed last, unranked. On 2026-10-11 both tabs had rows: Gemini Flash first on Detectors (suspicion gap 0.27, Flash-Lite 0.26) and first on Liars (self-tag lie success 67%, n=1,066). |
| 2:45 | Research | Open the deception matrix. Leave it on **Self-tagged**; the Jev toggle has no labels in this batch. | "Rows are the liar model, columns are the listener. Each cell is how often the listener's vote followed the lie, against how often it followed a true claim." Read out: "When gpt-oss-120b lied, deepseek-v3.1 voted the way the lie wanted 26% of the time over 69 votes, against 81% after a true claim. Most cells sit below their baseline. All 13 cells above baseline involve gpt-5-mini or gpt-5-nano, as liar or listener, and those two played only 12 games each, so that's a small sample." (Hover a cell for its baseline and 95% CI.) |
| 3:15 | Model profile | From the leaderboard, click a model name. The tables on the profile use the raw ids (`gemini-flash`, `llama4-maverick`), not the short names. | "One page per model: how it does as wolf and as villager, how often it lies, how well its suspicion scores find the wolves." |
| 3:30 | Spot the wolf | Back in a replay, switch View to Villager (in Narrator view the roles are already on the plates). Click **Spot the wolf** in the bottom right, mark up to two seats, then step forward. | "You get the same public information a villager had. Can you beat the models?" |
| 4:00 | Live mode | Open a game with a ● Live badge on the Games page. Needs the Neon deploy variables. **There was no running game on 2026-10-11 at 03:40 UTC, and none of the 13 visitor games in Neon had finished.** Plan on the 60-second backup video. | "Games stream into Neon while they run. The page asks for anything newer every two seconds." |
| 4:30 | Play | Click Play in the navigation bar to show the mode picker (Normal, Watch, Advanced). **Don't click Normal or Watch, and don't open `?page=play`, `?page=play&mode=basic` or `?page=play&mode=watch`, unless you mean to start a game.** Those pages start a real game as soon as the bot check passes (about a second), with no Start button. It spends money and counts against the limits (5 games per IP per day, 1 active per IP, and the daily budget; `/api/limits` showed 18 to 20 games left on 2026-10-11). To show the setup without starting anything, open Advanced (`?page=play&mode=advanced`): Seats, Werewolves, Seer and Doctor switches, Model selection, Your seat, per-seat models, and a **Start Simulation** button you don't press. The first visit can show "Waking the server" for up to a minute. | "Visitors can take a seat at the table. There's a daily spend cap and a per-visitor limit." |

If you are running long, drop in this order: narration, model profile, play.

Do not claim anything from Live mode or Play that you did not see work during the pre-demo checklist.

## 3. Track scripts

Each script is about 60 seconds spoken. Each says what to click, what to say, and where the evidence lives. Say only the numbers that are filled in.

### Hugging Face: Best Use of Open Source AI

**Evidence:** HF Dataset with the game logs and labels: https://huggingface.co/datasets/polter-dev/societysim-games (public, 100 games of `mixed_v2_10p`, 20,512 events, 1,951 labels). HF Space with the viewer: `polter-dev/societysim` (a static Space, running on 2026-10-11), open it at https://polter-dev-societysim.static.hf.space (the plain `polter-dev-societysim.hf.space` address returns 404). It is deployed from that dataset by the Deploy workflow (#294); Play shows "(offline)" there. Model list in `configs/models.yaml` and the README.

**Click path**
1. Space (or society-sim.us, `?page=games`), game index. Point at the model names.
2. Open the demo game in Narrator view. Click a wolf's seat to show reasoning next to speech.
3. Leaderboard page.
4. Switch tabs to the Dataset page. Show the card and the files.

**Say**

> SocietySim is a social deduction benchmark for language models. Players sit down to a game of Werewolf. Most of them are open models served through Hugging Face Inference Providers: Qwen3 235B, Llama 4 Maverick, Gemma 4, gpt-oss-120b and DeepSeek V3.1. Gemini and GPT fill the other seats. A deterministic engine owns the rules, so a model can lie to the others but can't bend a rule. Every turn, each model writes private reasoning, a suspicion score for every other player, and one public move. After the game we label each lie and check whether it worked. Every game is published as a Hugging Face Dataset, and the replay viewer and leaderboard run on a Space. In our batch of 100 ten-player games, Gemini Flash won most often as a wolf.

**Numbers**
- Open models at the table: 5 of the 9 in `configs/mixed_v1.yaml`, all through HF Inference Providers.
- Smoke batch (#34): 10 of 10 games finished, about $0.02 per game.
- `mixed_v2_10p`: 100 ten-player games, 0 unfinished, $11.02 total. Villagers won 62, wolves 38.
- Top of the leaderboard: gemini-flash, 65% win rate (95% CI 57% to 73%), OpenSkill ordinal 33.01, 68% as wolf over 28 wolf seats. Next: llama4-maverick 61% (52% to 68%), gemini-flash-lite 59% (51% to 67%).
- Lie success (self-tag, exploratory): 1,129 lies. 57% of votes went the way a lie implied, against a 59% no-lie base rate. gemini-flash's lies moved 45% of the suspicions they targeted (n=943), the highest.

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
- The AMD model is not in `mixed_v2_10p`, so its report has no AMD speed row. Use the two lines above.
- Don't call AMD faster or slower than the API providers. The setups differ.

**If it breaks:** viewer replay of the AMD game from the laptop, plus the #114 and #253 comments.

### Databricks: Best Use of Databricks

**Evidence:** Databricks workspace: catalog `workspace.societysim` (`games`, `events`, `labels`, `batch_metrics`, `metrics_*`, `deception_pairs`), the AI/BI dashboard (id `01f1c4a8de811c60854d375219a83fbb`, republished on all four batches for #294), the MLflow experiment (`387213714723263`; the id comes from Marcus's 2026-10-11 00:25 UTC comment on #42 and I couldn't open the workspace without a login, so `TODO(verify)` when you open it; that comment lists runs for `mixed_v1`, `selfplay_baseline` and `selfplay_amd`, so check there is a `mixed_v2_10p` run before saying "one run per batch"). Code in `notebooks/databricks/` and `scripts/databricks_ingest.py`.

**Click path**
1. Catalog Explorer: `workspace.societysim` and its tables.
2. Open the notebook `metrics.py`, scroll to the parity cell, show it passing.
3. Open the AI/BI dashboard: win rate by model and role, lie success, detection, then the deception view.
4. Experiments page: one MLflow run per batch.

**Say**

> We want to know which models lie and whose lies work, across every game we've played. One script loads every game into Delta tables: games, events, labels and a per-batch metrics table. A notebook ports our whole metrics layer to Spark SQL, and its last cell checks every value against our Python metrics and fails on any mismatch, so the dashboard and the leaderboard can't drift apart. The AI/BI dashboard sits on those tables: win rate by model and role, which lies swayed votes, how well villagers spotted wolves, and who fools whom. Each experiment batch is logged as an MLflow run. Gemini Flash had the highest lie success rate: its lies moved 45 percent of the suspicions they targeted.

**Numbers**
- Live on real data (#294): 158 games from `mixed_v1`, `selfplay_baseline`, `selfplay_amd` and `mixed_v2_10p`, 26,760 events, 2,486 labels, 2,790 batch_metrics rows, 158 deception pairs. The `metrics.py` notebook (with the parity cell) and `mlflow_batches` both ran to SUCCESS on the workspace.
- Parity checks (#127, local PySpark): 1,050 on the fixture, 2,730 on a 25-game scripted batch, 0 mismatches.
- Top lie success (`mixed_v2_10p`, self-tag): gemini-flash, 45% of targeted suspicions moved (n=943).
- Detection AUC (`mixed_v2_10p`): gemini-flash 0.77, gemini-flash-lite 0.75, deepseek-v3.1 0.73, llama4-maverick 0.72, gemma4-26b 0.70, qwen3-235b 0.69, gpt-oss-120b 0.65, gpt-5-mini 0.61, gpt-5-nano 0.55 (the two GPT-5 models played only 12 games).

**If it breaks:** screenshots of the dashboard and the parity cell, plus `uv run python scripts/databricks_ingest.py --fixture --dry-run` in a terminal.

### Neon: Best Use of Neon

**Evidence:** Neon console SQL editor; `neon/schema.sql`; the live site reading the Data API; the `leaderboard`, `leaderboard_by_batch`, `lies_by_model`, `global_detectors` and `global_liars` views (the last two feed the Global rankings tabs on the Leaderboard page, #344 and #366).

Set up before you walk in: the Neon console open on the SQL editor with the queries below pasted in, and a terminal in the repo with `NEON_DATABASE_URL` set (for the optional live stream and for `sync-neon`). Everything the site does goes through the Data API as the anonymous role; the console runs as the owner, so it can read `events` directly and the site can't.

**Click path**
1. Console, SQL editor: `select status, count(*) from games group by status;` On 2026-10-11 at 03:40 UTC this gave finished 158, failed 27, running 2. The failed rows are partial games (interrupted batch runs and visitor games cut off when the player left or the play server restarted). No leaderboard counts them. Say "158 finished games".
2. `select model, seat_games, wins, win_rate from leaderboard_by_batch where batch_id = 'mixed_v2_10p' order by win_rate desc;` Top three: gemini-flash 91 of 139 (0.655), llama4-maverick 85 of 140 (0.607), gemini-flash-lite 82 of 138 (0.594). These are the same numbers as the website's leaderboard, so it works to say "SQL and the site agree". Don't use plain `select * from leaderboard;` for that line: it pools every batch (gemini-flash 116 of 189, 0.614) and won't match the 65%.
3. One game in order: `select seq, type, actor, event ->> 'target' as target from events where game_id = 'mixed_v2_10p-g0043' order by seq limit 20;` `target` lives inside the jsonb `event` column, not in its own column. The first rows are `game_start`, `phase_start`, then `wolf_chat` and `wolf_proposal` from p2 (p2 proposes p0). That is the wolf channel, so it shows what the anonymous role can't see.
4. The privacy line. In the editor, compare `select count(*) from events;` with `select count(*) from events_public;` (27,696 on 2026-10-11). Or, from a terminal, show the browser's own view:
   ```bash
   AUTH=https://ep-orange-river-b5l9yq6a.neonauth.c-7.us-east-2.aws.neon.tech/neondb/auth/token/anonymous
   API=https://ep-orange-river-b5l9yq6a.apirest.c-7.us-east-2.aws.neon.tech/neondb/rest/v1
   TOKEN=$(curl -s $AUTH | python -c "import sys,json;print(json.load(sys.stdin)['token'])")
   curl -s -o /dev/null -w "events %{http_code}\n" -H "Authorization: Bearer $TOKEN" "$API/events?limit=1"
   curl -s -o /dev/null -w "events_public %{http_code}\n" -H "Authorization: Bearer $TOKEN" "$API/events_public?limit=1"
   ```
   Run on 2026-10-11 it printed `events 403` and `events_public 200`; `labels` is 403 too, and a write to `games` is refused (403). These URLs are the public ones the site ships; the token is a short-lived anonymous one.
5. On society-sim.us open the Leaderboard. The big table above is the published batch JSON (static). The **Global rankings** tabs underneath are Neon: the page asks `global_detectors` and `global_liars` through the Data API and refreshes every 45 seconds. The Games page also merges in the Neon `games` table, which is where visitor games and the ● Live badge come from.
6. Optional, paid, only if you rehearsed it: stream a game live. `uv run societysim play --config configs/selfplay_smoke.yaml --agents llm --neon` plays one 7-seat gemma4-26b self-play game (about two cents; my estimate from gemma4-26b's $0.0024 per game in the 100-game report) and writes each event to Neon as it happens. Find it with `select game_id, status from games order by started_at desc limit 3;`, then re-run `select seq, type, actor from events where game_id = '<id>' order by seq desc limit 5;` while it plays. The game stays in Neon as batch `selfplay_smoke` and will count in the pooled `leaderboard` and in Global rankings, so remove it afterwards with `uv run societysim sync-neon --delete <game_id>`. If you don't want an extra row on the public rankings, skip this step and use the backup video.
7. Backfill: `uv run societysim sync-neon runs/<batch>` prints `synced N games, N events, N labels`. Show that line, or a screenshot of it. Don't run it on stage against the live database.

**Say**

> Every game we run is a stream of events: speeches, accusations, night kills, votes. Neon Postgres holds all of them. Each game is a row in a games table, marked running, finished or failed. Each event is a JSONB row keyed by game and sequence number, so a schema change needs no migration. Labels sit in their own table, and leaderboard views compute win rates in SQL. Games stream into Neon while they run, and sync-neon backfills a batch you played without streaming. The website reads it directly: a short-lived anonymous token gets a read-only role that can select from a few views and tables, and nothing else. It can't read the raw events or labels at all. For a game still in progress, the views give it public events only, with private reasoning and suspicion scores stripped, and labels appear only once the game has finished, so a spectator can't see secrets. The page asks for new events every two seconds. Right now Neon holds 158 finished games and about 28 thousand events.

**Numbers and facts**
- Privacy (#323, checked on the live Data API 2026-10-11): `events` and `labels` return 403 to the anonymous role; it reads `events_public` and `labels_public`. A running game's events come through with `private_reasoning` and `suspicion` set to null, and its labels are hidden until it finishes. Public events of a running game are visible, so "hidden until it ends" is wrong for the events themselves.
- Visitor play uses Neon for the spend ledger and for streaming public events. Only public events reach Neon while a game runs; the full log is backfilled after it ends.
- Labels are synced (#294): `labels_public` returned 2,486 rows on 2026-10-11, `lies_by_model` returns rows, and `global_liars` has a non-null self-tag lie success (gemini-flash 0.669) through the Data API.
- Counts (2026-10-11 03:40 UTC): 187 games in `games` (158 finished, 27 failed, 2 running), 2,486 labels, 27,696 rows in `events_public`. The four batch syncs wrote 27,714 events (`mixed_v2_10p` 20,512, `mixed_v1` 4,598, `selfplay_baseline` 2,542, `selfplay_amd` 62); the 18-row gap is private events of failed partial games that `events_public` doesn't show. The game count grows whenever a visitor starts a game, so say "158 finished", not a total.
- Speed: from a browser, `leaderboard` answered in about 0.1 s and `global_liars` in about 0.3 s.

**If it breaks:** the static site (reads `index.json` from the site root, no Neon; Global rankings and live badges disappear) and the SQL queries in the console, or their screenshots.

### Gemini

Gemini is in the lineup and the Devpost draft lists the Gemini prize. No ticket exists for it, so this script is thin on purpose.

**Evidence:** `configs/models.yaml` (`gemini-flash-lite`, `gemini-flash`, through a Google AI Studio key); the leaderboard and model profile pages; the batch reports.

**Click path**
1. `configs/models.yaml`: the two Gemini entries.
2. Leaderboard, then the model profile for `gemini-flash` and `gemini-flash-lite`.
3. Research page: find the Gemini rows and columns in the deception matrix.

**Say**

> Gemini plays at the same table as the open models, as a wolf and as a villager, in both Flash and Flash-Lite. We rate every model the same way: win rate, lie success, and how well its suspicion scores find the wolves. In 100 ten-player games, Gemini Flash came first on win rate, skill rating, lie success and wolf detection, and Flash-Lite was third on skill rating.

**Numbers**
- 7-player self-play baseline (`selfplay_baseline`, 45 games, 5 per model; checked against `runs/selfplay_baseline/report/report.md` on Seth's laptop and the #35 comment): `gemini-flash` had the highest detection AUC, 0.91, ahead of `deepseek-v3.1` (0.85) and `llama4-maverick` (0.81). Call it a 7-player self-play result with five games per model. The report is local, not published.
- Neon detectors/liars views, read from the live site on 2026-10-11: `gemini-flash` is the top detector (suspicion gap 0.27, 146 village games), just ahead of `gemini-flash-lite` (0.26, 147 games), so don't call the gap large; `gemini-flash` is also the top liar (suspicion received 31%, lowest of the ranked models; self-tag lie success 67%, n=1,066, behind only gpt-5-mini's 75% on 10 wolf games). The earlier note that Flash-Lite led the detectors is out of date. They are on the Leaderboard page under Global rankings (Detectors and Liars tabs).
- Honest caveat: `gemini-flash` calls were slow (45 to 389 s per call in the preflight, #35) and the free-tier rate limit is low. If asked, say so.
- `mixed_v2_10p` (10 players, 100 games): gemini-flash 65% win rate (57% to 73%), OpenSkill 33.01 (first), detection AUC 0.77 (first), lies moved 45% of targeted suspicions (n=943, first). gemini-flash-lite 59% (51% to 67%), OpenSkill 22.30 (third), AUC 0.75, 39% (n=1,204). Median latency 3.7 s and 1.5 s per call in this batch.

**If it breaks:** model profile screenshots.

### Figma: Best Design

**Evidence:** the Figma file "Werewolf - Game Viewer" (https://www.figma.com/design/eWOVhSAOmFM0jycNu3TTPd/Werewolf---Game-Viewer), with the T4.0 flow wireframes and tokens on the frame at `?node-id=54-161` (#22 is closed and `docs/design/README.md` records the link). `docs/design/` on `main` holds `tokens.css`, `tokens.json`, `interaction-spec.md` and `contrast.md`. The accessibility tests are in `viewer/src/a11y.test.ts`.

**Click path**
1. Games index (`?page=games`), then open a game.
2. Without the mouse: Tab to the timeline, arrow through a few events, press End, then Home.
3. Switch the View select to Narrator. Click a seat for the inspector.
4. Click a lie marker on the timeline.
5. Switch back to Villager. Roles, reasoning and lie markers disappear until a villager could know them.
6. Open the Figma file (https://www.figma.com/design/eWOVhSAOmFM0jycNu3TTPd/Werewolf---Game-Viewer?node-id=54-161), the T4.0 frames 01 to 06.

**Say**

> We built the viewer for someone who has never played Werewolf. The flow is short: pick a game, watch it, click any player to see what they were thinking, and jump to the moments where someone lied. A toggle switches between the narrator's view, which shows every role, and a villager's view, which hides anything a villager couldn't know yet, so you can play along. The timeline has markers for every kill, vote and lie, and the whole thing works from the keyboard: arrows to step, Home and End to jump, Space to play. Tests run axe on the page in both views, and they check that the narrator view's secrets never reach the villager view. Reduced-motion mode turns animations off.

Don't say "all colors, type and motion come from one token file, and tests check their contrast." PR #121 was closed without merging, `viewer/src/tokens.css` doesn't exist on `main`, and `docs/design/README.md` says the app doesn't use the Figma tokens yet (a subset is mirrored in `viewer/src/controls.css`, and Cinzel and Inter are not loaded). Say the tokens are exported to `docs/design/` and leave it there.

**Numbers:** contrast was checked by hand in a browser (jsdom has no layout, so the axe color-contrast rule is off in tests). Don't say "fully WCAG compliant".

**If it breaks:** local viewer, the backup video, Figma screenshots.

### Adobe: Prettiest Hack (only if that room is still on)

Short version of the earlier script. PR #121 (table lighting, role flip, drawn arrows) was closed without merging; the illustrated viewer came from the later work (PR #288). On the live site on 2026-10-11 I saw the illustrated round table, the speaking seat highlighted, lie pop-ups just below the table, and straight (not curved) accusation and vote arrows. I did not see a day-to-night lighting change or a role card flipping on death (a dead seat just greys out with a cross). Drop those two claims, and the phone line, unless you see them.

> The replay is an illustrated round table. By day it is lit from the upper left, and at night the light shifts to moonlight and a candle glow. When a player dies, their role card flips face up. Accusations and votes draw in as curved arrows, arrowhead last. The speaking seat glows, and lie labels pop up just below. On a phone the whole table scales down together.

Click path: demo game with `?mode=narrator`, start at night 1, play into day 1, pause on a vote, jump to a death. Don't narrow the window to phone width: the game screen is built for laptop size (#465) and mobile is out of scope. Backup: screenshots on the `screenshots/issue-33` branch and the backup video.

## 4. Backup plan

Decide the fallback before you walk in, not in front of a judge.

| What fails | Fallback |
|---|---|
| society-sim.us is down or slow | Use the HF Space (https://polter-dev-societysim.static.hf.space) or the GitHub Pages mirror (https://polter-dev.github.io/societysim); both loaded the 100-game index on 2026-10-11. Neither has Neon data or Play. Then the local viewer. |
| Venue wifi is down | Local viewer on the laptop with the demo game loaded through `?log=<url>` or "Open a log file" (`?page=replay`). Run `cd viewer && npm ci && npm run dev` the night before. Phone hotspot is the next step. |
| Live mode has no running game | Play the 60-second backup video. Don't start a paid game on stage. |
| Play server | The live site's Play calls the Render service `societysim-play` (https://societysim-play.onrender.com). On 2026-10-11 at 03:36 UTC `/api/limits` returned 200 with `play_enabled` true, `remaining_today` 18 and seven allowed models. The HF Space `polter-dev/societysim-play` is still PAUSED (HF flagged it as abusive, appeal sent), so don't use it. Check `curl https://societysim-play.onrender.com/api/limits` before the demo. If it fails, skip the Play step and say visitor play is "built and rate limited, hosting is moving". Don't claim it is live. A restart of the Render service kills games in progress (several of the 13 visitor games in Neon ended at the same second, which looks like restarts, and all 13 show `failed`), so don't redeploy it near the demo. |
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

- [ ] Run the site smoke check (#362, #370, #381): `uv run --extra smoke python scripts/site_smoke.py --strict-play` against https://society-sim.us (run `uv run --extra smoke python -m playwright install chromium` once first). It checks the landing page, the mode picker, Advanced setup, the game list, a replay, a step deep link, the leaderboard, Research, a model profile, New game, Play and Spot the wolf. `--strict-play` fails unless the play start screen is up; without it an offline or coming-soon Play page still passes. Note any failure and fix or avoid that page. **Warning:** its Play check loads `?page=play`, and since #479 that page starts a real game on its own once the bot check passes, so the smoke run is no longer read-only. It costs a few cents and uses one of your 5 games per IP for the day. Run it once, not on a loop, and expect a `visitor-...` game to appear in Neon. (Fixing the script to use `?page=play&mode=advanced` is a separate ticket, not part of this file.)
- [ ] `uv run societysim doctor` (paid: makes a tiny structured call to every model in `configs/models.yaml`, so cost is a few cents; read the output for any model with no working mode). To check one model: `--model 'gemini-*'`.
- [ ] Warm the HF Space: open it and click through the index, a replay and the leaderboard until it's awake. A sleeping Space takes a minute to start. If the Space is paused or flagged, use the Cloudflare site and say nothing about the Space.
- [ ] Check the play server: `curl https://societysim-play.onrender.com/api/limits`. `remaining_today` above 0 and `client_ip_hint` showing your own masked IP mean it's healthy. Venue wifi often shares one IP, and the limit is 5 games and 1 active game per IP per day, so don't start games in the room.
- [ ] Check the live site loads Neon data: the Leaderboard shows rows and the Games page lists games. If both fall back to static data, the Neon deploy variables are missing; say "static data" and don't claim live.
- [ ] Open the demo game in Narrator view; confirm the lie pop-up and the vote it moved are at the seq numbers written above.
- [ ] Narration: the live site has no `.audio/` for the demo game (checked 2026-10-11), so the step is already dropped. Only restore it if you deploy locally with the clips (section 7) and hear them play.
- [ ] Databricks: open the dashboard, confirm it renders and the parity cell is green.
- [ ] Neon console: open the SQL editor with the queries from the Neon script pasted in (the status count, `leaderboard_by_batch`, the event query for `mixed_v2_10p-g0043`, the `events` vs `events_public` counts). Run each once so the first answer isn't a cold start. Don't run `sync-neon`, `--delete` or the live-stream command before you have decided to.
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
- Each call times out after 120 seconds by default; set `timeout_s` on a provider in `configs/models.yaml` to change it (#181). The `huggingface` provider sets 180 seconds (#346), and the HF-router `hosted_vllm` models count under it.
- Failed and empty replies are never written to the response cache (#188).
- Deploy a real batch: `bash scripts/deploy-space.sh --out /tmp/site --force runs/<batch>`, then `npx wrangler login` once and `npx wrangler pages deploy /tmp/site --project-name societysim --branch main`. Or set the `DEPLOY_SOURCE` repo variable to `hf:<owner/name>[:<batch_id>]` and rerun `Deploy`. Live Neon games on the site need the repo variables `VITE_NEON_DATA_API_URL` and `VITE_NEON_AUTH_URL` (README, deploy section).
- Visitor play kill switch: set the Space variable `PLAY_ENABLED=0` and restart (README, "Visitor play").

## 8. Open items for #38

- `TODO(batch)` items are filled from `mixed_v2_10p` (#294). Narration audio for the demo game is not on the live site: the CI deploy has no `.audio` folders, so a local deploy (above) is needed to serve them.
- `TODO(verify)`: one left, the Databricks MLflow experiment id (needs a workspace login).
- Whether the Space appeal was accepted. As of 2026-10-11 the play Space was still PAUSED and the live site plays through Render, so this no longer blocks the demo.
- Who presents Gemini, and whether the Adobe room is on.
- Figma access: the link from #22 is recorded above; open it in a private window to check the room's laptop can view it. PR #121 was closed unmerged, so the one-token-file line stays out of the Figma script.
- The AMD hourly rate for imputed $/game.
- Play server host: Render (checked 2026-10-11). Open decision: whether to leave Play out of the live demo, since none of the 13 visitor games recorded in Neon has finished and the pre-demo smoke check starts a real game.
- Rehearsal, backup video, and the room split: the ticket's acceptance criteria that only humans can close.
