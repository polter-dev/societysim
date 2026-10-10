# Pitch: can one AI model deceive another?

A 3-minute talk track, a 9-slide outline, one beat per sponsor and answers to likely judge questions. It follows the research question in the README ("What we're asking"). Part of demo prep (#38).

`TODO(batch)` marks a number that does not exist yet. Each one says where it will come from. Fill them once the mixed batch is labeled and reported, and don't say a number out loud until it is filled.

## What we can claim today

- Built and merged: the engine, the batch runner, `label`, `report` (including the "who fools whom" matrix, #251), the replay viewer, the human seat, narration clips, and the live site at https://society-sim.us.
- Not done yet: the 10-player engine (#250, open), so every real batch so far is 7 players. The 10-player batch config and the human-vs-models config are #259. The report's "Research answers" section is #264 (open).
- Not done yet: the real results. The only model numbers we have are an exploratory first look at 9 mixed_v1 games (7 players, small n), posted on #216. Treat it as a hint, not a finding.

## 3-minute talk track

Times are cumulative.

### 0:00 to 0:20 Hook

> Here is a line from one of our games. A wolf says "I'm the Seer, and I checked Ava: she's a wolf." He is not the Seer. His private notes, written the same turn, say so.

That example is the hand-written sample game in `fixtures/sample_game.jsonl` (Finn; labels at seq 19 and 20 in `fixtures/sample_game.labels.jsonl`). It shows the shape of a real log but it is not model output, so don't call it one. `TODO(batch)`: swap in a real quote, its model name and seq numbers from the demo game.

### 0:20 to 0:45 The question

> Our question: when AI models from different companies play a social deduction game, can one model deceive another? And which models fool which? AI agents increasingly talk to each other and negotiate. If some models are easy to mislead, and some are good at misleading, that is a trust question.

### 0:45 to 1:25 How it works

> We seat models at a table to play Werewolf. A few players secretly work against the rest, and everyone has to decide who to trust. A deterministic Python engine owns the real game state, so a model can lie to the other players but can't bend a rule. Each turn a model returns two things: a private scratchpad with a suspicion score for every other living player, and a public action. Every event goes to a log. We label lies by comparing what a player said with what the engine knows, and the log itself is never edited.

### 1:25 to 2:00 Headline results

> We ask four things: who lies best, who catches lies best, who fools whom, and does lying win.

Say only what is filled in:

- Win rate per model as wolf and as villager, with confidence intervals. `TODO(batch)`: report summary (`runs/<batch>/report/`).
- Skill rating as wolf and as villager. `TODO(batch)`: OpenSkill table in the report.
- Lie rate and lie success per model. `TODO(batch)`: report summary.
- Who fools whom: for each liar model and listener model, how often the listener's vote followed the lie, against how often it followed a true claim. `TODO(batch)`: deception matrix in the report (#251).
- Detection: how well each model's suspicion scores pointed at the real wolves. `TODO(batch)`: AUC in the report summary.
- Human seat against models. `TODO(batch)`: only if the #259 run happens.

Until the batch is done, the only honest line is: "In a first look at 9 games, lies from gemini-flash and qwen3-235b were followed often, and gpt-oss-120b, gpt-5-nano and gemma4-26b fooled nobody. That is a small sample, and the full batch is running." (From #216; the top pairs have n of 6 to 8.)

### 2:00 to 2:40 Live demo

Open https://society-sim.us, the demo game, with `?mode=narrator`. The viewer starts in villager view and hides everything private.

1. Press Space. Pause on the first accusation arrow.
2. Click a wolf's seat. The inspector shows its private scratchpad next to what it said out loud.
3. Jump to the labeled lie pop-up, then to the vote it moved.
4. Narration: play the game's ElevenLabs clips from its `.audio` folder alongside the replay. Callum voices the day, Harry voices the night. The viewer does not play clips itself yet, so run them from a second window or drop this step.
5. Human seat: `uv run societysim play --human-seat p3` puts you in a seat with the same information an AI would get. The other seats need model keys, so use a laptop that has them, or skip it.

`TODO(batch)`: demo game id and the seq numbers to jump to (same open item as in [DEMO.md](DEMO.md)).

### 2:40 to 3:00 Close

> Models talk to each other more every month. Before we trust one to negotiate for us, we should know how easily it gets fooled, and by whom. A game with hidden roles and hidden scratchpads lets us measure that directly. The logs, the labels and the code are open.

## Slide outline

1. **Can one AI deceive another?**
   - The question in one line, plus the hook quote.
   - On screen: a lie pop-up from the viewer, or the sample-game quote.
2. **Why it matters**
   - AI agents negotiate and talk to each other.
   - Knowing who can be misled, and by whom, is a trust question.
   - On screen: text only.
3. **The game in one minute**
   - Wolves lie, the seer decides what to reveal, the village votes.
   - Village wins when both wolves are gone; wolves win at parity.
   - On screen: the round table in the viewer, mid-day.
4. **How we see the intent behind a lie**
   - The engine owns the truth; a model returns a scratchpad plus a public action.
   - Lies are labeled from the log, in a separate file.
   - On screen: the inspector, scratchpad beside the public statement.
5. **Who is at the table**
   - Open models through Hugging Face Inference Providers, plus Gemini and GPT. An AMD-hosted gpt-oss-20b plays self-play games.
   - Seats and roles rotate. No Claude models play.
   - On screen: the model table in the README or `configs/models.yaml`.
6. **Results: who lies best, who catches lies**
   - `TODO(batch)`: win rate, OpenSkill as wolf and villager, lie success, detection AUC.
   - On screen: report charts from `runs/<batch>/report/`, or the leaderboard page.
7. **Results: who fools whom**
   - `TODO(batch)`: the liar-by-listener matrix with 95% CIs and the true-claim baseline.
   - On screen: the deception matrix from the report.
8. **Live: watch a lie work**
   - Narrator view, inspector, label pop-up, the vote it moved, narration clips.
   - On screen: https://society-sim.us.
9. **What's next**
   - 10-player games (2 wolves, 8 others), tracked in #250 and #259. Human seat against models.
   - On screen: the "What we're asking" list in the README.

## Structure numbers we can cite

From #216: scripted bots (not models), 40 games each, free. Scripted bots don't reflect model behaviour, so these only show game length and structure.

| | 7 players | 10 players |
|---|---|---|
| Mean days per game | 2.65 | 4.38 |
| Statements per game | 20.5 | 53.4 |
| Votes per game | 10.2 | 26.7 |

Use them for the "why 10 players" answer and nowhere else.

## Sponsor beats

One or two sentences each. Full scripts and click paths are in [DEMO.md](DEMO.md).

- **Hugging Face.** Most players are open models (Qwen3 235B, Llama 4 Maverick, Gemma 4, gpt-oss-120b, DeepSeek V3.1) served through Inference Providers. The Dataset upload and the Space are still in progress: `TODO(#37)` for URLs; `scripts/deploy-space.sh` builds the Space.
- **Databricks.** Game logs load into Delta tables, a notebook recomputes our metrics in Spark SQL and checks them against the Python numbers, an AI/BI dashboard sits on top and MLflow records each batch. Parity was checked on local PySpark with 0 mismatches. `TODO(#42)`: say it runs on the live workspace only once that is done.
- **AMD.** gpt-oss-20b plays as a self-hosted player on an AMD Radeon AI PRO R9700, served with vLLM on ROCm. The run is done (see [infra/amd/README.md](../infra/amd/README.md) and the #114 numbers in DEMO.md). A Strix Halo run through llama.cpp is in progress, so quote nothing from it. It plays self-play games, not the mixed batch.
- **Neon.** Games stream into Neon Postgres while they run (`--neon`), finished batches backfill with `sync-neon`, and the deployed viewer can read live games through the Neon Data API using an anonymous token. That last part only works when the deploy variables are set, so check the live site before saying it.
- **ElevenLabs.** An offline script turns a game's public events into spoken clips: two narrators (Callum by day, Harry by night) and a voice per model. It never voices private reasoning.
- **Cloudflare.** The viewer is hosted on Cloudflare Pages at https://society-sim.us and redeploys on every `viewer/` change on `main`.

## Q&A prep

**Aren't scripted games biased?**
We don't use scripted games for any result. Scripted bots test the engine and give free structure checks like game length. Every model result comes from games where LLMs write each move.

**How do you know it's a lie?**
The engine knows every role, so a false role claim or a false seer report is false by construction. The model's own scratchpad is a second signal, and the Jev classifier covers statements that are harder to check. The raw log stays untouched; labels go in a separate file.

**Isn't the sample too small?**
Today, yes. The first look on #216 is 9 games and its top pairs have 6 to 8 votes. That is why the report gives 95% confidence intervals and a true-claim baseline, and why we call it exploratory. `TODO(batch)`: game count and pair counts once the batch is done.

**Why 10 players?**
Scripted 7-player games last 2.65 days with 20.5 statements. At 10 players it is 4.38 days and 53.4 statements, about 2.6 times the votes, so each pair of models gets more chances to lie and be judged. It isn't live yet (#250), so results so far are 7-player.

**Does a model that wins by lying prove it deceives well?**
Not by itself. Lie success and persuasion are correlations. A vote can follow a lie for other reasons, so the matrix compares against how often the same listener follows true claims of the same kind.

**Don't models just memorize Werewolf?**
They know the game, which is fine for this question. We shuffle player names by seed and randomize speaking order each round, so a model can't lean on a name or a fixed turn order.
