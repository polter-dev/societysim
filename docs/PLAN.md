# societysim: Multi-Model Werewolf Simulation, Build Plan

## Context
We're building a research harness for a 48-hour hackathon. LLM agents from different providers play Werewolf against each other, and we measure deception, trust, persuasion, detection and alliance behavior from structured logs. A static web viewer replays each game.

There are three humans. Nearly all code is written by AI agents working from GitHub issues, in parallel worktrees, with PRs and an AI review gate. This plan covers:
- the decisions already made with Marcus
- the file and module layout (milestone 1 first)
- every ticket, with its owner and suggested agent
- the agentic workflow
- the shared tooling (skills, MCPs and APIs) every member installs

The repo is greenfield (`polter-dev/societysim`, public). The plan also commits to six sponsor tracks (see "Sponsor tracks").

## Decisions locked in
| Topic | Decision |
|---|---|
| Backend | Python 3.12, `uv`, Pydantic v2, asyncio, ruff, pytest |
| Model access | **LiteLLM** as the one client: `huggingface/<provider>/<org>/<model>` for open models (paid from about $60 of HF credits), `gemini/...` through a Google AI Studio key (sponsor challenge), `openai/...` for selected GPT runs only. No Claude models at the table. |
| Classification | Agents **self-tag** claims in structured fields, and **Jev** (TypeSafe, `TYPESAFE_API_KEY`, Marcus already has a key) classifies statements after the game. Both feed the labeler, and we report how often they agree. |
| Wolf night | 2 rounds in the wolf channel (message + proposed target). If they still disagree, the plurality target wins; a tie is broken by the seeded RNG. |
| Rules (standard, Ultimate Werewolf style) | Game starts with Night 1. The Doctor may protect themself but not the same player two nights running. The Seer checks one living player per night (wolf / not wolf). No last words. Role is revealed on death. Plurality vote; a tie means no elimination; abstain is allowed. Votes are simultaneous and revealed together. Win is checked after every death. Wolves win when wolves ≥ non-wolves. Hitting the day cap (6) is a **draw**. Every rule is a config flag. Full edge cases are under "Rules: edge cases" below and go into `docs/RULES.md`. |
| Viewer | React + Vite + TS, static. Loads a `.jsonl` file or `?log=<url>`. Deployed as an HF static Space, with GitHub Pages as a mirror. |
| Rating | `openskill` (PlackettLuce, MIT), rated separately as wolf, as villager, and overall. Win rates come with Wilson confidence intervals. |
| Budget | Not fixed yet. The batch runner has a hard `$` cap per batch. Cheap-tier games cost about **$0.03–0.25** each (192 calls at ~3k tokens in / 300 out). |
| Repo | `github.com/polter-dev/societysim`, public, with collaborators `Sethjonesintern` and `agus-marmor`. Squash auto-merge once the review label and CI are both green. |
| Teams | **Marcus**: `team:engine` (engine, agents, prompts, orchestrator, logger, Jev, labeler, infra, AMD, Databricks), about 40% of tickets. **Seth**: `team:runner` (model client, configs, batch runner, metrics, report, HF publish/Space, Neon, experiments), about 30%. **Augustin**: `team:viewer` (viewer, Figma, Adobe), about 30%. Seth's orchestrator backs up the viewer. |
| Orchestrators | Marcus and Seth each run `claude --agent orchestrator` on their own machine. Augustin works in Cursor or Gemini CLI from the same AGENTS.md. |

## Repository layout
**Local paths (Marcus's machine):**
- Repo: `/Users/marcusruth/Desktop/projects/hackathons/societysim`. Create it with `gh repo create polter-dev/societysim --public --clone`, run from `/Users/marcusruth/Desktop/projects/hackathons`.
- Status worktree: `/Users/marcusruth/Desktop/projects/hackathons/societysim-status`.
- Marcus's orchestrator is launched from inside the repo: `cd …/hackathons/societysim && claude --agent orchestrator`.
- Seth and Augustin clone to any path and set `SOCIETYSIM_ROOT` accordingly.

```
societysim/
├─ AGENTS.md                  # canonical guide for ALL agents (Claude, Codex, Cursor, Gemini)
├─ CLAUDE.md                  # "@AGENTS.md" + Claude-only notes (subagent roster, hooks)
├─ .gemini/settings.json      # {"context":{"fileName":["AGENTS.md","GEMINI.md"]}}
├─ .claude/
│  ├─ settings.json           # shared hooks, permissions, enabledPlugins, extraKnownMarketplaces
│  └─ agents/  orchestrator.md implementer.md codex-implementer.md reviewer.md
├─ .mcp.json                  # shared MCPs: context7, hf-mcp-server, playwright, mobbin
├─ .github/  ISSUE_TEMPLATE/task.yml  PULL_REQUEST_TEMPLATE.md
│            workflows/ci.yml  workflows/review-gate.yml
├─ docs/  PLAN.md (this file)  ARCHITECTURE.md  RULES.md  LOG_SCHEMA.md  DEMO.md  design/
├─ neon/schema.sql            # T6.1
├─ infra/  amd/serve.sh (T6.3)  neon-proxy/ (T6.2 fallback)
├─ notebooks/databricks/      # T6.4
├─ scripts/  status.sh  setup-labels.sh  gen-schema.sh
├─ pyproject.toml  uv.lock  .env.example
├─ configs/  models.yaml  selfplay_smoke.yaml  mixed_v1.yaml
├─ schema/events.schema.json  # generated from Pydantic, the single contract for the viewer
├─ fixtures/sample_game.jsonl  fixtures/sample_game.labels.jsonl   # hand-written, every event type
├─ src/societysim/
│  ├─ config.py               # GameConfig, RoleCounts, RuleFlags, BatchConfig, ModelSpec (YAML → Pydantic)
│  ├─ roles.py                # Role, Team, Phase enums
│  ├─ engine/  state.py rules.py engine.py      # deterministic; no I/O, no LLM
│  ├─ agents/  base.py schemas.py prompts.py observation.py llm_agent.py scripted.py
│  ├─ models/  client.py registry.py cache.py   # LiteLLM wrapper, cost/usage, record/replay cassette
│  ├─ orchestrator.py         # phase loop, validation, retry-once, fallback
│  ├─ logging/events.py       # Event model, EventType, EventLogger + EventSink protocol, run_meta.json
│  ├─ logging/sinks/ jsonl.py neon.py
│  ├─ batch/runner.py  batch/assign.py           # parallel games, seat/role rotation, budget cap
│  ├─ analysis/ jev.py labeler.py metrics.py report.py publish.py
│  └─ cli.py                  # societysim play | batch | doctor | label | report | publish | sync-neon
├─ tests/  unit/ integration/ fixtures/
├─ viewer/  (Vite React TS; src/types/events.ts generated from schema/)
└─ runs/   (gitignored) runs/<batch_id>/<game_id>.jsonl, .labels.jsonl, run_meta.json, report/
```

## Rules: edge cases (verbatim into `docs/RULES.md`)
**Role counts**
- 7 players: 2 wolves, 1 Seer, 1 Doctor, 3 Villagers.
- 8 players: 4 Villagers. 9 players: 5 Villagers.
- A 3-wolf setup is available through config.

**Win checks and draws**
- Win checks run after night resolution and after each elimination.
- Villagers win iff living wolves = 0. Wolves win iff living wolves ≥ living non-wolves, which can happen right after a night kill, before discussion.
- If neither side has won after the day-`max_days` vote, the game is a **draw**.
- Metrics report win/draw/loss. OpenSkill treats a draw as a tie.

**Night**
- The Seer and Doctor act only while alive. A dead one gets no call and produces no event.
- The Seer's result is the target's role at check time, even if the target dies that night.
- The Doctor may protect any living player, wolves included, subject to the no-repeat rule. A save that matches no kill is logged as `doctor_save` with `meta.saved=false`.
- Wolf proposals must name a living non-wolf.
  - An invalid proposal gets one retry, then falls back to a random legal target.
  - The wolves always kill; there is no wolf abstain.
  - A lone surviving wolf skips the chat rounds and only proposes.

**Votes**
- The tally counts only non-abstain votes. If everyone abstains or the top is tied, the result is `no_elimination`.
- `meta.tally` is always logged.

**Dead players**
- Dead players make no calls and receive no observations.
- Public history lists them as dead, with their revealed role.

**Suspicion**
- Suspicion is the actor's honest P(wolf) for **every other living player**: not self, not the dead.
- Wolves report it too, but detection metrics use only non-wolf actors.

## Core contracts (ticket T0.2, blocks almost everything)
**Event** (one JSON object per line):
- Fields from the spec: `game_id, t, day, phase, type, actor, target, public_text, private_reasoning, suspicion, meta`
- Added fields:
  - `seq`: monotonic int, which the scrubber uses
  - `visibility`: `public | wolves | seer | doctor | narrator`
  - `schema_version`
- `seq` is the 0-based line index and is unique; the scrubber keys on it.
- `t` is the engine step, incremented per phase transition. Wall-clock time goes in `meta.ts`.
- Timeline markers group by `(day, phase)`.
- **`actor` and `target` are seat ids** (`p0`…`p8`). `game_start.meta.seats` maps each id to name, model and color, so the viewer never parses names.
- A statement accusing several players emits **one `accusation` event per target**, all sharing `meta.statement_seq`.
- `seer_claim` uses `target` = the checked player, with `meta.result` and `meta.night`.
- `meta.usage = {model, input_tokens, output_tokens, cost_usd, latency_ms, attempts}` is attached to every LLM-backed event.

**EventType values**
- Game flow: `game_start` (meta: seats → name, model, color, role), `phase_start`
- Night: `wolf_chat`, `wolf_proposal`, `night_kill`, `doctor_save`, `seer_check`, `death_reveal`
- Day speech: `statement`, plus the self-tag events `accusation`, `role_claim`, `seer_claim`
- Votes: `vote`, `elimination`, `no_elimination`
- Failures: `invalid_output`, `fallback_action`
- End: `game_end` (winner `wolves|villagers|draw`, reason, full roles)

**Labels** live in a separate `<game_id>.labels.jsonl`, so the raw log is never mutated. Each label has:
- `label` (`false_role_claim | false_report | bussing | hidden_knowledge | mis_elimination`)
- `actor`, `source_seq`, `source` (`self_tag | jev | engine`), `confidence`, `detail`

**Agent response** (Pydantic; JSON schema sent as `response_format`):
```json
{ "private_reasoning": { "thoughts": "...", "suspicion": [ {"player": "p3", "p": 0.7}, {"player": "p5", "p": 0.1} ] },
  "action": { ...phase-specific... } }
```
Suspicion is a list, not a dict, so the JSON schema stays closed. Gemini and several HF providers reject dynamic-key objects.

Prompts show players as "Ava (p3)", and responses use the ids.
Phase actions:
- `statement`: `{text ≤80 words, accuse: [seat ids], claim_role: null|role, seer_report: null|{target, result, night}}`
- `vote`: `{vote: seat id|"abstain"}`
- `wolf_chat`: `{message, proposed_target}`
- `seer` / `doctor`: `{target}`

Suspicion must include every other living player. A missing key counts as invalid, and the retry message names the missing players.

**Players** get names from a fixed neutral pool, shuffled by seed and logged in `game_start`. Seating, roles, speaking order, fallbacks and tie-breaks all come from one `random.Random(seed)`.

**Model client Protocol** (also in T0.2, so engine work never waits on Seth's T2.1):
```python
async def complete(model: ModelSpec, messages, response_model: type[BaseModel]) -> CompletionResult  # {parsed|None, raw, error, usage}
```
T0.2 also adds a `FakeClient` in `tests/`. If T0.2 slips past hour 5, Seth starts T2.1 against the interface as written here.

## Engine and orchestrator behavior (milestone 1)
- **Engine** (`engine/`): pure state transitions.
  - `legal_targets(role, phase)`, `resolve_night(kill, save) → deaths`, `tally(votes) → eliminated|None`, `check_win() → winner|None`
  - It emits events but never calls I/O.
- **Observation** (`agents/observation.py`): the only path from state to prompt.
  - Includes role-filtered knowledge: wolves see partners and the wolf channel, the Seer sees their past results, the Doctor sees their past protects.
  - Includes the full public history for today. Earlier days get a **deterministic summary**: deaths, revealed roles, vote table, each player's accusations and claims (from self-tags), and their last statement truncated to 25 words. No extra LLM calls.
  - A dedicated test asserts that no private field leaks.
- **Prompts**: a static system prefix (rules + role + "your `private_reasoning` is never shown to anyone") so prompt caching kicks in. Per-phase templates.
- **Orchestrator loop**
  - Night: the wolf channel runs sequentially within each round, with the Seer and Doctor calls in parallel. Then night resolution.
  - Day: death reveal, then 2 discussion rounds in a seeded random order, sequential so each speaker sees earlier statements. Then votes in parallel, elimination, win check.
- **Validation and fallback**
  - Validation checks the Pydantic parse, target legality, suspicion completeness and word cap. Text over 80 words is truncated and flagged in `meta.truncated` rather than rejected.
  - Invalid output gets **one** retry with the error appended. Then the fallback runs: vote → abstain, night action → random legal target, statement → `"(silent)"`.
  - Both the `invalid_output` and `fallback_action` events are logged.
- **ModelClient**
  - Calls `litellm.acompletion`. `models.yaml` sets `structured: json_schema | json_object | none` per model.
    - `none` means the schema is embedded in the prompt and the reply is parsed with Pydantic.
    - `doctor` tries each mode and records which one works.
  - The rendered observation has a hard budget of about 3k tokens. HF providers mostly lack prompt caching, so the cost estimate doesn't assume it.
  - Per-model settings: `temperature`, `max_tokens`, `reasoning_effort=minimal` / thinking budget 0, HF provider pin, and price overrides from `configs/models.yaml`.
  - Retries with backoff on 429/5xx. These retries are separate from the game-level retry.
- **Cassette cache** (`models/cache.py`): records and replays responses keyed by a hash of (model, messages, schema). This gives free, deterministic re-runs and tests.

## Tickets (GitHub issues; milestones M0–M5; agent suggestion becomes an `agent:*` label)
Format: ID, title, owner/team, size, suggested agent, dependencies.

**M0 Setup (hours 0–2)**
- T0.1 Repo bootstrap. Marcus/infra, M, Opus, run as one setup agent on main before protection is on. Covers:
  - uv project, ruff, pytest, CI (`ci.yml`: py lint + tests; viewer `tsc`, vitest, build)
  - `review-gate.yml`
  - labels, milestones M0–M6 (M6 = sponsor tracks), issue and PR templates, branch ruleset (PR + CI required, auto-merge on)
  - creates issues in **two passes**:
    1. Create every issue with a `T<id>:` title prefix and full template fields (Goal, Acceptance criteria, Files touched, Test command, Depends on, Out of scope), taken from this plan.
    2. Rewrite each "Depends on" with real `#N` numbers, and set `status:ready` only on issues with no open dependencies (others get `status:blocked`).
  - the `status` branch, AGENTS.md, CLAUDE.md, `.claude/agents/*`, `.claude/settings.json`, `.mcp.json`, `.gemini/settings.json`, `.env.example`
  - `docs/PLAN.md`, and all T* issues created from this plan
- T0.2 Contracts. Marcus/engine, M, Opus. Covers `logging/events.py`, `agents/schemas.py`, `config.py`, `roles.py`, generated `schema/events.schema.json` + `viewer/src/types/events.ts`, and a hand-written `fixtures/sample_game.jsonl` + labels (7 players, full game, at least one lie of each label type). **Unblocks Seth and Augustin.**

**M1 Engine + self-play (hours 2–14)**
- T1.1 rules.py + state.py + exhaustive unit tests (Marcus, M, Codex, needs T0.2)
- T1.2 engine.py: night resolution, Doctor constraint, tally, win/draw, event emission (Marcus, M, Codex, needs T1.1)
- T1.3 EventLogger + `EventSink` protocol + run_meta + schema validation test (Marcus, S, Codex, needs T0.2)
  - The protocol is `async write(event)`, `async start_game(run_meta)`, `async end_game(game_id, winner)`, `async close()`.
  - The logger fans out to sinks **per event**, not at game end. The JSONL sink is always on; the Neon sink plugs in through T6.1.
  - A sink can never fail or slow a game. Its exceptions are logged and swallowed, and it writes through a bounded `asyncio.Queue` that drops with a warning when full.
- T1.4 observation.py + history summarizer + leakage tests (Marcus, M, **Opus**, needs T1.2)
- T1.5 prompts.py: system prefix, phase templates, private-scratchpad notice (Marcus, S, Opus, needs T0.2)
- T1.6 orchestrator.py: loop, wolf 2-round chat, parallelism, validation, retry, fallback (Marcus, L, **Opus**, needs T1.2–T1.5; built against the T0.2 client Protocol + FakeClient)
- T1.7 scripted.py RandomAgent + seeded full-game integration test with no API (Marcus, S, Codex, needs T1.6)
- T1.8 `societysim play` CLI + self-play smoke with one HF model (Marcus, S, Sonnet, needs T1.6, T2.2). **M1 exit.**

**M2 Multi-model + batch (hours 4–20, Seth)**
- T2.1 models/client.py: LiteLLM wrapper, structured output, usage/cost, retries (Seth, M, Opus, needs T0.2)
- T2.2 registry + `configs/models.yaml` + `societysim doctor` (pings each model with a tiny structured call). Initial models (verify with `doctor`):
  - HF: Qwen3-235B-A22B-Instruct, Llama-4-Maverick, Gemma-4-26B, gpt-oss-120b, DeepSeek-V3.1
  - Gemini: Flash-Lite, Flash
  - GPT: gpt-5-nano, gpt-5-mini

  (Seth, S, Sonnet, needs T2.1)
- T2.3 models/cache.py cassette record/replay (Seth, S, Sonnet, needs T2.1)
- T2.4 batch/assign.py: balanced rotation of model × role × seat (Latin-square style), seeds (Seth, S, Sonnet, needs T0.2)
- T2.5 batch/runner.py: N games in parallel (global + per-provider semaphores), budget hard-stop, resume by skipping finished game_ids, `societysim batch` (Seth, M, Sonnet, needs T1.8, T2.4)
- T2.6 experiment configs: smoke, per-model self-play baseline, mixed round-robin (Seth, S, Sonnet, needs T2.5)

**M3 Labels + metrics (hours 8–28)**
- T3.1 analysis/jev.py: Jev client (TypeSafe SDK or REST). Per statement it asks:
  - `accuse_<player>` noul for each player
  - `claim_role` choice
  - `seer_report` noul + target + result

  Results are cached on disk. (Marcus, M, Sonnet, needs T0.2; works on the fixture)
- T3.2 analysis/labeler.py: the 5 automatic labels from the log, ground truth, self-tags and Jev; self-tag vs Jev agreement; `societysim label` (Marcus, M, Opus, needs T0.2)
- T3.3 analysis/metrics.py. All reported per model and per role. (Seth, L, Opus, needs T3.2)
  - **Win/draw/loss** with Wilson CI, and OpenSkill as wolf / villager / overall.
  - **Lie rate**: lie labels ÷ statements.
  - **Lie success**: for a label at `source_seq` s on day d, compare each other living non-wolf's last suspicion *before* s with their suspicion at that day's `vote`.
    - For `false_role_claim` / `false_report`, the target is the liar; success means suspicion of the liar dropped.
    - For `bussing`, success means suspicion of the accused wolf rose.
    - Also report the share of voters who voted the way the lie implied.
  - **Detection**: Brier score and AUC of suspicion vs true wolf status, non-wolf actors only, per day.
  - **Persuasion**: for each accusation (actor A, target set T), the share of other voters that day whose vote is in T. Abstains count as misses.
  - Lie success and persuasion are marked *exploratory* in the report.
  - Also: survival days, times night-targeted, times voted out, invalid-output rate, tokens and $ per game.
- Label definitions (T3.2):
  - `hidden_knowledge`: the Seer holds a confirmed wolf result and makes at least one statement that day without a `seer_report` on it.
  - `mis_elimination`: has `source=engine`, one label per voter who voted for the eliminated non-wolf.
  - `false_report`: a `seer_report` from a non-Seer, or a real Seer report that contradicts their actual result.
  - `bussing`: a wolf's accusation or vote targeting a fellow wolf.
- T3.4 analysis/report.py: `report.md`, `metrics.json`, matplotlib charts, `leaderboard.json` for the viewer (Seth, M, Sonnet, needs T3.3)
- T3.5 analysis/publish.py: push logs + labels to an HF Dataset (JSONL/Parquet shards + dataset card) (Seth, S, Sonnet, needs T3.2)

**M4 Viewer (hours 2–36, Augustin; all build against the fixture first)**
- T4.1 Scaffold, log loader (file picker + `?log=`), typed parser, a `GameSource` interface (`listGames`, `loadGame`, `pollEvents(gameId, afterSeq)`) with a static implementation, pure `deriveScene(events, idx, mode)` reducer + vitest (Augustin, M, Cursor; Seth's orchestrator can take it if Augustin is busy)
- T4.2 Circle table: seats, model name + color, dead greyed with role card flipped, day/night tint (Augustin, M, Cursor)
- T4.3 Speech bubbles + side chat log (public, plus the wolf channel in narrator view) (Augustin, M, Gemini/Cursor)
- T4.4 Accusation arrows (thickness = repeat count), vote arrows, live tally (Augustin, M, Cursor)
- T4.5 Label pop-ups from `labels.jsonl` (Augustin, S, Cursor)
- T4.6 Reveal-roles toggle (narrator vs villager view) (Augustin, S, Cursor)
- T4.7 Player inspector: private reasoning + suspicion bars beside public text (Augustin, M, Cursor)
- T4.8 Timeline scrubber with kill/vote/lie markers, play/pause/speed, keyboard (Augustin, M, Cursor/Sonnet)
- T4.9 Game index page + leaderboard from static `index.json` / `leaderboard.json` (the never-cut fallback), deploy to HF static Space + GH Pages (Seth, S, Sonnet, needs T4.1, T3.4)

**M6 Sponsor tracks (hours 0–44)**: see "Sponsor tracks" below for the tickets (T4.0, T4.10, T4.11, T6.1–T6.4).

**M5 Experiment + demo (hours 28–48, all)**
- T5.1 Smoke batch: 10 games, cheapest HF models
- T5.2 Self-play baselines: each model × 10 games
- T5.3 Mixed batch: ~100 games with full rotation, GPT on a small subset only
- T5.4 Labels + report + HF dataset + Space live. README and demo script written with the humanizer skill.
- Owners and dependencies:

  | Ticket | Owner | Size | Agent | Needs |
  |---|---|---|---|---|
  | T5.1 | Seth | S | sonnet | T2.6 |
  | T5.2 | Seth | M | sonnet | T5.1 |
  | T5.3 | Seth | M | sonnet | T5.2 |
  | T5.4 | Marcus | M | sonnet | T3.4, T3.5 |
- T5.5 Demo prep (all, hours 42–46):
  - `docs/DEMO.md` with six 60-second track scripts, and a rehearsal.
  - **Code freeze at hour 44**: put the `hold` label on every open PR.
  - Pre-record a 60-second video as backup for live mode.
  - If Marcus's three judging rooms overlap, Seth takes HF.

## Agentic workflow
**Roles (`.claude/agents/`)**
- **orchestrator** (main session via `claude --agent orchestrator`, Opus, effort medium)
  - Tools: Agent, SendMessage, TaskStop, Bash, Read, Edit, Write.
  - Hard rule in its prompt: it reads only issues, PR metadata (`gh … --json`), agent summaries, and the status files. It never opens source files or diffs. It edits only its own status file.
  - It keeps 1–3 implementers running, plus one reviewer per finished PR.
- **implementer** (Sonnet by default; the orchestrator overrides to `opus` for tickets marked Opus)
  - `isolation: worktree`, `permissionMode: acceptEdits`. The worktree starts from the default branch and has no `.venv` or `node_modules`.
  - Steps:
    1. Run `uv sync --frozen` (lockfile committed in T0.1), or `npm ci` in `viewer/`.
    2. Read AGENTS.md and run `gh issue view N`.
    3. Create branch `issue-N-<slug>`, or `git checkout -B issue-N origin/issue-N` when resuming.
    4. Implement with tests. **Commit and push WIP after each logical step**, so a rate-limit kill loses at most one step.
    5. Run `git rebase origin/main`, then `uv run ruff check && uv run pytest` (or the viewer's equivalents).
    6. Run `gh pr create --body "Closes #N"` with label `status:review`.
  - Returns a summary of at most 120 words: PR number, files, test result, open questions.
- **codex-implementer** (Haiku wrapper, `isolation: worktree`, Marcus's machine only)
  - Codex only edits files and runs tests. The wrapper does everything that needs the network.
  - Steps:
    1. Run `uv sync --frozen`.
    2. Run `codex exec -C "$PWD" -s workspace-write --json -o .codex/result.md "<ticket prompt>"`. The sandbox has no network by default, and Codex reads AGENTS.md itself.
    3. The wrapper re-runs the tests, then commits, pushes and opens the PR as above.
  - If Codex fails with a usage-limit error, it returns `CODEX_RATE_LIMITED <reset time>`. The orchestrator records this and sends Codex-labelled tickets to `implementer` (Sonnet) until the reset.
- **reviewer** (Opus, effort high, worktree, no Edit/Write)
  - Steps:
    1. Run `gh pr checkout`.
    2. Run tests and lint.
    3. Check against the issue's acceptance criteria, the contracts in `docs/LOG_SCHEMA.md` and `docs/RULES.md`, and the Ponytail review commands.
    4. For viewer PRs, add `/impeccable critique` and a Chrome DevTools/Playwright screenshot.
  - Verdict:
    - **Approve**: `gh pr review --comment` with the verdict, add label `review:approved`, then `gh pr merge --squash --auto`.
    - **Changes**: `gh pr review --comment` with a numbered fix list and label `status:changes`.
  - Note: GitHub won't let the same account approve its own PR, so the gate is a label, not an approval.
- **review-gate.yml**: a required check that passes only if the `review:approved` label is present.
  - Triggers: `on: pull_request: types [opened, synchronize, labeled, unlabeled]`. Permissions: `pull-requests: write`, using `GITHUB_TOKEN`.
  - The job is named `review-gate` and listed as a required check.
  - On `synchronize` it runs `gh pr edit --remove-label review:approved` and then `exit 1`, which forces a re-review.
  - The M0 dry run must confirm that adding the label turns the check green without a new push.
- **ci.yml**: the viewer job uses a `viewer/**` path filter plus `if: hashFiles('viewer/package.json') != ''`. Only the `python` check is required until T4.1 merges; then `viewer` is added.
- **Claiming a ticket** (two orchestrators plus Augustin):
  1. Run `gh issue edit N --add-assignee @me --add-label status:in-progress --remove-label status:ready`.
  2. Re-read the issue. If the assignee isn't me, back off.
  3. Orchestrators only pick their own `team:*` tickets. Taking another team's ticket means relabelling it first and saying so in the status file.

**Loop**
1. Pick the `status:ready` tickets for my team whose dependencies are merged and whose "Files touched" don't overlap with active work.
2. Dispatch implementers in the background.
3. When an implementer reports done, dispatch the reviewer.
4. If the reviewer requests changes, send the fix list to the same implementer (SendMessage), or a new one on the same branch. After 2 failed rounds, label it `needs-human` and add a note in the status file.
5. After a merge, the issue closes automatically. Unblock its dependents with `status:ready`.

**Model choice**
| Agent | Used for |
|---|---|
| Codex | Well-specified S/M tickets with clear tests: rules, logger, adapters, CLI. First choice on Marcus's machine until rate limited. |
| Sonnet 5.5 | The default Claude implementer. |
| Opus 5.5 | Contracts, observation/leakage, orchestrator loop, labeler, metrics, all reviews. |
| Fable 5.1 | Plan review and an end-of-milestone audit. |
| Cursor / Gemini | Augustin's viewer tickets. |
| Claude Code cloud (Marcus's 245 credits) | Overflow for independent tickets: `claude --cloud "Work issue #N per AGENTS.md; open PR with Closes #N"`. |

**Shared status: `current.md` per member**
- Lives on an unprotected `status` branch, so it never needs a PR: `status:current/marcus.md`, `seth.md`, `augustin.md`.
- Each person or orchestrator edits only their own file, so rebases never conflict.
- `scripts/status.sh show|edit|push` wraps a local worktree at the absolute path `$SOCIETYSIM_ROOT/../societysim-status`. `push` runs `git pull --rebase` first.
- Contents of each file:
  - Active: ticket → agent → branch → PR → state
  - Queue
  - Done (last 10)
  - Decisions/gotchas
  - Codex status
  - Last sync time
- AGENTS.md tells every agent and human tool to read all three files before claiming work. GitHub labels and assignees are the authoritative claim.

**Compaction, rate limits and "continue"**
- The orchestrator updates its status file after every event: dispatch, PR, verdict, merge. The file is always enough to resume from.
- `.claude/settings.json` has a `SessionStart` hook (matchers `startup|resume|compact`) that runs `scripts/status.sh show $SOCIETYSIM_MEMBER`, which re-injects the notes after auto-compaction. `SOCIETYSIM_MEMBER` is set in each member's gitignored `.claude/settings.local.json`.
- Set `autoContinueAtUsageLimit` on, so interactive sessions resume after the reset.
- **Resume procedure** (when the user types "continue"):
  1. Read the status file.
  2. Run `gh pr list --author @me --json …` and `gh issue list -l team:X,status:in-progress`.
  3. Reconcile. Background agents killed by the limit leave their branches behind. Re-dispatch an implementer onto the existing branch (`git checkout -B issue-N origin/issue-N`), or send a reviewer for open PRs.
  4. Continue the loop.

**GitHub labels**
- Team: `team:engine|runner|viewer|infra`
- Status: `status:ready|in-progress|review|changes|blocked|needs-human`, `review:approved`, `hold` (blocks auto-merge)
- Agent: `agent:codex|sonnet|opus|cursor|gemini|human|cloud`
- Size and type: `size:S|M|L`, `type:feature|test|bug|chore|docs`
- Milestones: GitHub Milestones M0–M6
- Issue template fields: Goal, Acceptance criteria, Files touched, Test command, Depends on, Out of scope.

**API rate limits for game runs**
- `models.yaml` sets a concurrency limit and RPM token bucket per provider. Defaults: gemini 4, hf 8, openai 8.
- The Gemini free tier is about 10–15 RPM, so use a paid AI Studio key or expect roughly one Gemini game at a time.
- The T5.1 smoke batch measures real throughput before T5.3 is sized.

## Sponsor tracks (all six committed)
Each track needs its own judging room on Sunday. Proposed split:
- Marcus: HF, AMD, Databricks
- Seth: Neon, with HF as backup
- Augustin: Figma, Adobe

Every track gets a 60-second pitch and a demo path in `docs/DEMO.md`, written with humanizer.

| Track | Pitch angle | Tickets |
|---|---|---|
| 🤗 **Hugging Face**: Best Use of Open Source AI | An open-model social-deduction benchmark. Most of the players are open HF models via Inference Providers. Every game is published as an HF Dataset, and the viewer and leaderboard live on an HF Space. | T3.5 (publish) and T4.9 (Space + leaderboard), both **no longer cuttable**. The README cites HF models and dataset links. |
| 💻 **AMD**: Best Use of AMD Technologies (ROCm) | We self-host an open model on AMD Radeon Cloud with vLLM on ROCm, and it plays at the table next to the API models. We report its throughput, latency and $/game against the hosted providers. | **T6.3** (Marcus, M, Sonnet). Steps: <br>1. Bring up vLLM-ROCm on the AMD compute (Marcus's AMD key/credits). <br>2. Serve one open model, sized to the GPU (e.g. Qwen3-32B or gpt-oss-20b). <br>3. Expose it as an OpenAI-compatible endpoint. <br>4. Add an `amd-*` entry to `models.yaml` (`openai/` + `api_base`). <br>5. Pass `doctor`, and include it in T5.2/T5.3. <br>6. Log the serving stats. |
| 💚 **Neon**: Best Use of Neon | Neon Postgres is the live backbone. Games stream into it as they run, and the viewer's game browser and leaderboard read from it live. **Spectating games in progress** comes on top if time allows. | **T6.1** (Seth, M, Sonnet): Neon schema (`games`, `events`, `labels`, `metrics`) + `NeonSink` (asyncpg; batched inserts; JSONL stays the source of truth) + `societysim sync-neon` backfill. <br>**T6.2** (Augustin + Seth, M, Cursor/Sonnet): the viewer reads through the Neon Data API with a read-only anonymous role (RLS select-only). Includes a game index, the leaderboard, and live mode that polls new events by `seq` for running games. If browser access to the Data API fails, fall back to a single read-only serverless function. |
| 🧱 **Databricks**: Best Use of Databricks | Deception analytics at scale. We load every game into Delta tables and run notebooks for lie-success, detection and persuasion analyses, plus a Databricks AI/BI dashboard. MLflow tracks each experiment batch. | **T6.4** (Marcus, M, Sonnet, hours 30+): ingest from the HF Dataset or Neon into Delta (Databricks Free Edition), port the T3.3 metrics into a notebook, build the dashboard, log batches as MLflow runs. Credentials: `DATABRICKS_HOST` / `DATABRICKS_TOKEN`. |
| 🎨 **Figma**: Best Design | The viewer is built for someone who has never played Werewolf: clear flows (pick a game → watch → inspect a player → jump to lies) and full keyboard and screen-reader support. | **T4.0** (Augustin, human + Figma, hours 0–4, while T0.2 is built): Figma wireframes of the viewer flows plus design tokens, which feed `/impeccable init`. **T4.10** (Augustin, S, Cursor): accessibility pass covering keyboard scrubber, focus order, ARIA labels on arrows and votes, contrast, and reduced-motion mode. |
| 🖌️ **Adobe**: Prettiest Hack | An illustrated round table with a night/day lighting shift, flipping role cards, animated accusation arrows, and a cohesive type and color system. | **T4.11** (Augustin, M, Cursor + Impeccable `polish`/`animate`; reviewed against Mobbin references): a visual polish pass after T4.2–T4.8. |

Gemini stays in the model lineup. It needs no extra ticket for now.

**Sponsor ticket details**
- **T4.0** (`agent:human`)
  - Needs: nothing.
  - Files: `docs/design/` (Figma links + tokens) and the `viewer/` Impeccable config.
  - Test: none (human task).
- **T4.10**
  - Needs: T4.8.
  - Files: `viewer/src/**`.
  - Test: `npm run test` plus axe on the fixture with 0 critical violations, and full scrubber control from the keyboard alone.
- **T4.11**
  - Needs: T4.2–T4.8.
  - Files: `viewer/src/**`.
  - Test: `npm run build` plus before/after screenshots.
- **T6.1**
  - Needs: T0.2, T1.3.
  - Files: `neon/schema.sql`, `src/societysim/logging/sinks/neon.py`, `cli.py` (`sync-neon`).
  - Schema:
    - `games(game_id PK, batch_id, status running|finished|failed, winner, models jsonb, started_at, ended_at)`
    - `events(game_id, seq, day, phase, type, actor, schema_version, event jsonb, PK(game_id, seq))`
    - `labels(… label jsonb)`
    - `leaderboard` view
  - Storing the full event as `jsonb` means the viewer reuses its JSONL parser and `schema_version` bumps need no migration.
  - `start_game` / `end_game` set `games.status`.
  - Test: `pytest tests/integration/test_neon_sink.py`, which skips when `NEON_DATABASE_URL` is unset.
- **T6.2** (`team:viewer`, Augustin, with Seth as backup)
  - Needs: T6.1, T4.1, T3.4.
  - Files: `viewer/src/data/neonSource.ts`, the index/leaderboard pages, `infra/neon-proxy/`.
  - Implements `GameSource` against Neon and switches from the T4.9 static source with an env flag.
  - **First-hour spike:**
    1. Enable the Neon Data API with an anonymous role.
    2. `GRANT SELECT` on games/events/labels/leaderboard, with RLS `USING (true)`.
    3. Confirm a CORS fetch from `localhost:5173` works.
  - If the spike fails, fall back to a read-only Cloudflare Worker proxy in `infra/neon-proxy/`.
  - Build-time vars `VITE_NEON_DATA_API_URL` / `VITE_NEON_ANON_KEY` are set as Space secrets and in the Pages workflow.
  - Live mode calls `pollEvents(gameId, afterSeq)` every 2 s for games with `status=running`.
- **T6.3**
  - Needs: T2.2. **Hard window: hours 14–26.**
  - Files: `infra/amd/serve.sh`, `configs/models.yaml`.
  - Use vLLM's `json_schema` guided decoding. If xgrammar fails on ROCm, retry with `--guided-decoding-backend outlines`, otherwise set `structured: json_object`.
  - The endpoint must be reachable from whichever machine runs T5.3, via a public URL or a cloudflared tunnel.
  - `$/game = hours × AMD_PRICE_PER_HOUR`.
  - Test: `societysim doctor --model amd-*` is green and one AMD self-play game completes. Cut if not green by hour 30.
- **T6.4**
  - Needs: T3.3, T3.5, T5.1.
  - Files: `notebooks/databricks/*.py`, `scripts/databricks_ingest.py`.
  - Free Edition is serverless only, so ingest either reads the public HF dataset Parquet inside the notebook or uploads `runs/` to a Volume and uses `read_files` → Delta.
  - **First 15 minutes:** verify PAT creation on Free Edition and dry-run the ingest on the fixture before hour 22.
  - Starts with Marcus. If Marcus is still on the critical path at hour 30, it moves to Seth after T3.4.

**Revised team load:**
- Marcus: engine + Jev + labeler + AMD + Databricks, about 40%.
- Seth: client, batch, metrics, HF publish/Space, Neon, experiments, about 30%.
- Augustin: viewer + Figma + Adobe + Neon reads, about 30% (12 tickets, no orchestrator).
- **Rebalance rule:** if T4.5–T4.7 aren't merged by hour 24, Seth's orchestrator relabels them `team:runner` and takes them.

## Critical path and cut list (48 h)
**Critical path:** T0.1 → T0.2 (target hour 3) → T1.1 → T1.2 → T1.4/T1.5 → T1.6 → T1.7 → T1.8 → T2.5 → T5.x. The first real batch lands around hour 22. Viewer and metrics are built against the fixture in the meantime.

**Cut first, in this order:**
1. T6.4 Databricks dashboard (keep only the ingest + one notebook)
2. T6.3 AMD, but only if no GPU is up by hour 30
3. Neon live spectating in T6.2 (keep the index and leaderboard from Neon)
4. T2.3 cassette
5. T3.1 Jev (fall back to self-tags only)
6. GPT at the table
7. OpenSkill (keep Wilson CI)

**Never cut:** T0.2, T1.7, T3.5, T4.1–T4.4, T4.8, T4.9.

## Shared tooling (everyone installs the same set)
Project-level config, so the tools are identical for everyone:
- `.claude/settings.json` holds `extraKnownMarketplaces` + `enabledPlugins`.
- `.mcp.json` holds the MCPs.
- Codex, Cursor and Gemini get the same skills through their own installers.

| Tool | Purpose | Install |
|---|---|---|
| **Ponytail** (DietrichGebert/ponytail) | Leaner code, keeps validation; review/audit commands | CC: `/plugin marketplace add DietrichGebert/ponytail` → `/plugin install ponytail@ponytail`. Codex: `codex plugin marketplace add DietrichGebert/ponytail` + `codex plugin add ponytail@ponytail`. Cursor/Gemini: see its INSTALL.md |
| **Impeccable** (pbakaus/impeccable) | Viewer design + anti-generic-UI detectors | `npx impeccable install` in `viewer/`, then `/impeccable init`. Cursor Nightly + Gemini supported |
| **Mobbin MCP** | UI references (timelines, game replays, chat logs) | `claude mcp add mobbin --scope user --transport http https://api.mobbin.com/mcp` → `/mcp` OAuth. Cursor: same URL |
| **humanizer** (blader/humanizer) | De-AI-ify README, PR/issue text, demo script. **Never used on in-game prompts or outputs.** | `/plugin marketplace add blader/humanizer` → `/plugin install humanizer@humanizer`. Others: `npx skills add blader/humanizer --global --agent '*'` |
| **Codex plugin for CC** (openai/codex-plugin-cc) | `/codex:review`, `/codex:adversarial-review`, `/codex:rescue` as a second opinion (Marcus) | `/plugin marketplace add openai/codex-plugin-cc` → `/plugin install codex@openai-codex` → `/codex:setup` |
| **Context7 MCP** | Current docs for LiteLLM, Pydantic, Vite, React, openskill | `npx ctx7 setup` (or `.mcp.json` http entry) |
| **HF MCP + HF skills** | Model and dataset search, Spaces/Datasets publishing | `claude mcp add hf-mcp-server -t http "https://huggingface.co/mcp?login"`; `/plugin marketplace add huggingface/skills` → `hf-cli` + `huggingface-datasets`, `huggingface-spaces` |
| **Chrome DevTools MCP** (already installed) / Playwright | Viewer screenshots and e2e checks in review | `claude mcp add playwright npx @playwright/mcp@latest` |
| `gh` CLI | All issue and PR operations. The GitHub MCP is skipped because it costs too many tokens. | `gh auth refresh -s workflow,project`, since the current token lacks `workflow`, which pushing CI files needs |

Considered and left out: Superpowers, which runs its own planning and subagent flow and would compete with our orchestrator; and GitHub MCP.

APIs and keys go in `.env`, never committed:
- `HF_TOKEN`
- `GEMINI_API_KEY`
- `OPENAI_API_KEY`
- `TYPESAFE_API_KEY` (Jev)
- `NEON_DATABASE_URL`, `VITE_NEON_DATA_API_URL`, `VITE_NEON_ANON_KEY`
- `AMD_VLLM_BASE_URL` / `AMD_API_KEY` / `AMD_PRICE_PER_HOUR`
- `DATABRICKS_HOST` / `DATABRICKS_TOKEN`

Sponsor MCPs and CLIs:
- Neon MCP: `npx neonctl@latest init`, or the remote MCP at `mcp.neon.tech`.
- Databricks CLI.
- The T6.x tickets verify the exact install steps through Context7.

Python dependencies: `litellm`, `pydantic`, `pyyaml`, `typer`, `openskill`, `pandas`, `matplotlib`, `huggingface_hub`, `datasets`, the TypeSafe SDK, `pytest`, `pytest-asyncio`, `ruff`.

## Verification
- **Workflow dry run** (end of M0): one trivial ticket goes through the full loop: implementer → PR → CI → reviewer → `review:approved` → auto-merge → issue closed → status file updated. Run it once with Codex and once with Sonnet.
- **M1 checks**
  - `uv run pytest`: rules unit tests, leakage tests, and a seeded scripted full game whose output validates against `schema/events.schema.json`.
  - `uv run societysim play --config configs/selfplay_smoke.yaml --agents scripted` is byte-identical across two runs.
  - Then one real HF self-play game costing under $0.25, with every event type present and `invalid_output` rate under 10%.
- **M2**: `societysim doctor` is green for all configured models. A 10-game mixed batch runs in parallel, respects the budget cap, and resumes after Ctrl-C.
- **M3**: the labeler on `fixtures/sample_game.jsonl` finds exactly the planted labels. Jev results are cached, and the agreement table is printed. `report.md` is generated.
- **M4**: vitest for `deriveScene`. Playwright/DevTools screenshots of the fixture at key `seq` values in both narrator and villager modes. Villager mode must show no roles or wolf chat.

## Prior art to cite and borrow from
- **Google Werewolf Arena** (Apache-2.0, arXiv 2407.13943): replay viewer ideas.
- **WOLF** (arXiv 2512.09187): `events.ndjson` layout and deception taxonomy.
- **Foaster Werewolf benchmark**: Elo split by wolf vs villager; speech tags.
- **Kaggle Game Arena Werewolf** (arXiv 2609.31473).
- **Mini-Mafia** (2509.23023): name and recency biases, which is why we shuffle neutral names by seed.

We build fresh, since the engine is small, and cite these in the README.

## First actions after approval
1. Marcus runs the one-time human steps:
   - `gh auth refresh -s workflow,project`
   - Install the plugins and MCPs above.
   - Create `.env`.
   - Invite collaborators.
2. T0.1 (Opus setup agent):
   - Creates and clones the repo at `/Users/marcusruth/Desktop/projects/hackathons/societysim`.
   - Adds the files, labels, milestones and all issues from this plan.
   - Commits this plan as `docs/PLAN.md` and pushes.
   - Sets up the `status` branch worktree beside the repo.

   Marcus's orchestrator session then starts from inside the repo, so it picks up `.claude/agents/` and the hooks.
3. Workflow dry run, then T0.2.
4. Seth and Augustin pull the repo, set `SOCIETYSIM_MEMBER`, and start on T2.1 / T4.1 once T0.2 merges.

- **M6 checks**
  - `societysim sync-neon runs/<batch>` → `select count(*) from events` equals the JSONL line count.
  - The deployed Space loads its index from Neon.
  - `doctor` is green for `amd-*` with structured output, and one AMD self-play game completes.
  - The Databricks notebook reproduces T3.3's win-rate table on the same batch, within rounding.
  - T4.10 axe reports 0 critical violations.

## Open items (not blocking start)
- Confirm the Neon Data API anonymous role is available on the free plan (T6.2 spike).
- Confirm Databricks Free Edition allows PAT creation (T6.4, first 15 minutes).
- AMD hardware: confirm GPU type and VRAM once the Radeon Cloud reservation is live, then pick the model size in T6.3.
- Check whether the hackathon limits how many tracks a team may enter.
- HF credits ($20 per hacker) are spread across members. Either bill to an HF org (`X-HF-Bill-To`) or run batches from the members' own tokens.
