# Data contract: what the viewer reads

Contract version: 1

This is every file and endpoint the viewer reads, in one place. The viewer has two sources:

1. **Static files**: one batch dir, deployed as `data/` next to the viewer.
2. **Neon Data API** (PostgREST) at `VITE_NEON_DATA_API_URL`.

Each static JSON file has a JSON Schema under `schema/`, generated from Pydantic models by
`./scripts/gen-schema.sh`. `tests/contract/` runs the free scripted pipeline and validates every
file it produces against those schemas, and checks the Neon tables and views below against
`neon/schema.sql`. If this doc and the code disagree, the schema files win; fix the doc.

| Viewer type (today) | Backed by |
|---|---|
| `Event` (`viewer/src/types/events.ts`) | `schema/events.schema.json` (generated already) |
| `Label` (`viewer/src/data/labels.ts`, hand-copied) | `$defs.Label` in `schema/events.schema.json`; `events.ts` already exports a generated `Label` |
| `IndexEntry` (`viewer/src/data/staticSource.ts`) | `schema/index.schema.json` |
| `Board`, `BoardRow`, `Rating` (`viewer/src/pages/Leaderboard.tsx`) | `schema/leaderboard.schema.json` |
| `BySide` (`Leaderboard.tsx`) | `openskill` in `schema/metrics.schema.json` |
| `ModelProfile`, `PairRow` (`viewer/src/data/models.ts`) | `profiles` in `schema/metrics.schema.json` |
| narration index (not typed yet) | `schema/narration_index.schema.json` |
| highlights (not typed yet) | `schema/highlights.schema.json` |
| settings screen (not built yet) | `schema/game_setup.schema.json` and `models_catalog.json`, see [Game setup](#game-setup) |

The viewer types should match these schemas. They can be generated the same way `events.ts` is
(`json-schema-to-typescript`).

## Versioning

- Events and labels carry `schema_version` on every line (1, 2 or 3 today; the reader accepts all three. 3 allows seat `p9` for 10-player games).
- Static JSON files: one version for all of them, the **contract version** above. Each generated
  schema carries it as `"x-contract-version"`. `leaderboard.json` also carries it in-file as
  `schema`, which always equals the contract version (currently 1). The other files have no in-file version field: `index.json` and
  `highlights.json` are bare lists, and we don't add a field just to carry a number.
- Adding a field is not breaking. It still regenerates the schemas in the same PR (they reject
  unknown keys, so the contract tests fail until you do), and it updates this doc.
- Renaming, removing or retyping a field, or changing what a value means, is breaking. Per
  AGENTS.md hard rule 3 it needs its own ticket, a contract version bump, and regenerated schema
  files in the same PR. A bump changes all three together, whichever file broke:
  `CONTRACT_VERSION` in `scripts/gen-schema.sh`, the version line at the top of this doc, and
  `SCHEMA` in `analysis/report.py`. `tests/contract` fails if they disagree.
- Neon: tables and views are versioned with this doc. Renaming or dropping a column exposed to
  `anonymous`, or changing a view's columns or their order, is breaking in the same way.

## Conventions

- `actor`, `target`, seat keys and `speaker` are seat ids `p0`..`p9`, never names.
- Rates and shares are fractions 0..1, not percentages. Money is USD. Latency is milliseconds.
- `null` means "no data" (for example a wolf win rate for a model that never played wolf). It
  never means 0.
- Every key listed is always present unless the doc says it can be absent.
- Label sources are `self_tag` (the speaker tagged it), `jev` (a judge model found it) and
  `engine` (votes, `mis_elimination`). Lie numbers from `self_tag` and `jev` are always kept
  apart, never summed.

## Producing a batch

```bash
uv run societysim batch --config <cfg.yaml> [--agents llm]  # runs/<batch_id>/<game_id>.jsonl, run_meta
uv run societysim label runs/<batch_id> [--no-jev]          # <game_id>.labels.jsonl
uv run societysim report runs/<batch_id>                    # report/
uv run societysim highlights runs/<batch_id>                # highlights.json
uv run python -m societysim.analysis.narrate runs/<batch_id>/<game_id>.jsonl  # <game_id>.audio/ (free from the phrase bank)
uv run societysim catalog --out <dir>                       # models_catalog.json (deploy-space.sh runs this)
scripts/deploy-space.sh runs/<batch_id> <owner/space>       # deploy viewer + data/
scripts/deploy-space.sh --out <dir> runs/<batch_id>         # same layout, staged locally
```

## Deployed layout

`scripts/deploy-space.sh` copies the batch dir to `data/` next to the built viewer, leaving out
`.cache/` (raw judge responses) and `*.png` (charts for `report.md`). So the viewer sees:

```
data/
  <game_id>.jsonl              events, one per line
  <game_id>.labels.jsonl       labels (absent if `label` never ran)
  <game_id>.audio/index.json   narration index (absent unless narrated)
  <game_id>.audio/<sha1>.mp3
  highlights.json              at the batch root, not in report/
  report/index.json            the game list (legacy location; the deployed site serves it at the root, see below)
  report/leaderboard.json
  report/metrics.json
  report/report.md             human-facing, not part of the contract
  run_meta.json, <game_id>.run_meta.json, agreement.json   not part of the contract
```

The site root also has `models_catalog.json` (see [Game setup](#game-setup)), and
`devui/schema/` holds a copy of every `schema/*.schema.json`.

The viewer's default index URL is `./index.json`, at the site root. The deployed site (PR #287)
stages `index.json`, `leaderboard.json`, `metrics.json`, `<game_id>.jsonl` and
`<game_id>.labels.jsonl` at the root, and keeps `data/` too. `?base=<url>` points the viewer at
another http(s) data root. Every other static path is resolved from the index. A legacy
`report/index.json` still works: the viewer resolves its logs one level up.

## Static files

### Events: `<game_id>.jsonl`

- Path: `<batch>/<game_id>.jsonl`. The file stem is the game id.
- Produced by: `societysim batch` or `societysim play` (the orchestrator through `EventLogger`).
- Shape: one event per line, `schema/events.schema.json` (the `Event` model in
  `logging/events.py`). `schema_version` is 1, 2 or 3. Lines are in `seq` order from 0.
- The raw log is never edited (hard rule 5).

```json
{"schema_version":2,"game_id":"fixture-0001","seq":18,"t":2,"day":1,"phase":"day","type":"accusation","visibility":"public","actor":"p5","target":"p0","public_text":null,"private_reasoning":null,"suspicion":null,"meta":{"ts":"2026-10-10T12:00:36Z","statement_seq":17}}
```

### Labels: `<game_id>.labels.jsonl`

- Path: next to the log, `<batch>/<game_id>.labels.jsonl` (the log URL with `.jsonl` replaced
  by `.labels.jsonl`). Missing file means no labels, not an error.
- Produced by: `societysim label <batch>` (`--no-jev` for self-tag and engine labels only).
- Shape: one label per line, `$defs.Label` in `schema/events.schema.json`:
  `schema_version` (1|2|3), `game_id`, `label` (`false_role_claim` | `false_report` | `bussing` |
  `hidden_knowledge` | `mis_elimination`), `actor` (seat id), `source_seq` (seq of the labelled
  event), `source` (`self_tag` | `jev` | `engine`), `confidence` (0..1), `detail` (string).

```json
{"schema_version":2,"game_id":"fixture-0001","label":"hidden_knowledge","actor":"p0","source_seq":16,"source":"self_tag","confidence":1.0,"detail":"Ava checked Cleo (p2) as a wolf on night 1 and spoke on day 1 without reporting it."}
```

### Game index: `report/index.json`

- Path: `<batch>/report/index.json`, or `./index.json` at the deployed site root. Schema: `schema/index.schema.json`.
- Produced by: `societysim report <batch>` (`analysis/report.py`, `build_index`).
- Shape: a list, one entry per finished game, in file-name order. Unfinished logs are left out.

| Field | Type | Notes |
|---|---|---|
| `game_id` | string | |
| `file` | string | Log file name, relative to `index.json`'s directory in the deployed layout: resolve as `new URL(file, indexUrl)`. For a legacy `report/index.json` the log sits one level up: `new URL("../" + file, indexUrl)` |
| `seed` | integer | Game seed |
| `days` | integer | Last day reached |
| `winner` | `"wolves"` \| `"villagers"` \| `"draw"` \| null | |
| `seats` | object, seat id -> `{name: string, model: string, role: "wolf"\|"seer"\|"doctor"\|"villager"}` | Holds roles: don't show before the game is opened |

```json
[{"game_id": "c-g0000", "file": "c-g0000.jsonl", "seed": 2958207524, "days": 2, "winner": "wolves",
  "seats": {"p0": {"name": "Ben", "model": "a", "role": "villager"}, "p4": {"name": "Dev", "model": "a", "role": "wolf"}}}]
```

### Leaderboard: `report/leaderboard.json`

- Path: `<batch>/report/leaderboard.json`. Schema: `schema/leaderboard.schema.json`.
- Produced by: `societysim report <batch>` (`build_leaderboard`).

| Field | Type | Notes |
|---|---|---|
| `schema` | 1 | In-file contract version |
| `games` | integer | Finished games in the batch |
| `label_sources` | `["self_tag", "jev"]` | |
| `wolf_side_advantage` | object \| null | `{overall: Rating, sided: Rating}`: the wolf-side dummy player, one per OpenSkill pool. `{}` when no game had a winner. Compare `mu` with its prior of 0 |
| `models` | list of rows | Sorted by `openskill.ordinal`, best first; rows with no rating last |

`Rating` is `{mu: number, sigma: number, ordinal: number}` (ordinal = mu - 3 sigma).

Each row of `models`:

| Field | Type | Notes |
|---|---|---|
| `model` | string | Registry name |
| `games` | integer \| null | Distinct games the model played in |
| `seats` | integer | Seats it filled, summed over games. Win rate is counted over these |
| `win_rate` | number \| null | Per seat, all roles |
| `win_ci` | [number, number] \| null | Wilson 95% interval |
| `wolf_win_rate`, `village_win_rate` | number \| null | Null if it never played that side |
| `openskill` | Rating \| null | Overall pool |
| `detection_auc`, `detection_brier` | number \| null | Village seats: suspicion vs true wolf |
| `invalid_output_rate` | number \| null | Invalid outputs per model call attempt |
| `cost_usd_per_game` | number \| null | Per seat-game |
| `lie_rate` | `{self_tag: number\|null, jev: number\|null}` | Lies per statement. Null when that source has no labels in the batch |

```json
{"schema": 1, "games": 3, "label_sources": ["self_tag", "jev"],
 "wolf_side_advantage": {"overall": {"mu": 5.97, "sigma": 7.70, "ordinal": -17.12},
                         "sided": {"mu": 5.69, "sigma": 7.70, "ordinal": -17.42}},
 "models": [{"model": "gemini-flash", "games": 1, "seats": 1, "win_rate": 1.0, "win_ci": [0.21, 1.0],
             "wolf_win_rate": 1.0, "village_win_rate": null,
             "openskill": {"mu": 26.95, "sigma": 8.11, "ordinal": 2.64},
             "detection_auc": null, "detection_brier": null, "invalid_output_rate": 0.0,
             "cost_usd_per_game": 0.0096, "lie_rate": {"self_tag": 0.0, "jev": null}}]}
```

The viewer's `BoardRow` lacks `seats` and its `wolf_side_advantage` type lacks `sided`; both are
in the file and part of the contract.

### Metrics: `report/metrics.json`

- Path: `<batch>/report/metrics.json`. Schema: `schema/metrics.schema.json`.
- Produced by: `societysim report <batch>` (`analysis/metrics.py` `compute_metrics`, plus
  `summary`, `speed` and `profiles` from `report.py`).

Top level:

| Field | Type | Notes |
|---|---|---|
| `games`, `unfinished_games` | integer | |
| `label_sources` | `["self_tag", "jev"]` | |
| `exploratory` | `["lie_success", "persuasion"]` | Correlation, not cause |
| `models` | model -> group -> GroupStats | Groups: `all`, `wolf`, `village`, `seer`, `doctor`, `villager` (only those the model played). Human seats have no entry |
| `by_group` | group -> GroupStats | All models together |
| `openskill` | object | `overall`, `wolf`, `village`: model -> Rating. `wolf_side`: `{overall, sided}` Rating, same as `leaderboard.json`'s `wolf_side_advantage`. The viewer's `BySide` reads `wolf` and `village` |
| `deception_matrix` | object | Who fools whom, see below. Exploratory |
| `summary` | object | Headline numbers, see below |
| `speed` | model -> Speed | |
| `profiles` | model -> ModelProfile | One per leaderboard row, for the model profile page. See below |

GroupStats: `games`, `wins`, `draws`, `losses` (integers, per seat); `win_rate`, `win_ci`;
`statements` (integer); `lies` (`{self_tag: Lies, jev: Lies}`); `detection`
(`{n, brier, auc, by_day: {"<day>": {n, brier, auc}}}`, day keys are strings); `persuasion`
(`{exploratory: true, accusations, voters, hits, share}`); and per-seat averages that can be
null: `survival_days`, `night_targeted`, `voted_out`, `invalid_output_rate`, `tokens_per_game`,
`cost_usd_per_game`.

Lies: `lies` (integer), `by_type` (lie type -> count, only types seen), `lie_rate` (per
statement), `lie_success` (`{exploratory: true, comparisons, moved, voters, followed,
base_voters, base_followed, base_abstained, share_moved, share_followed, base_rate}`: counts are
integers, the last three are number | null).

`deception_matrix`: `{exploratory: true, pairs: {self_tag: Pairs, jev: Pairs}, baseline}`. Pairs
is liar model -> listener model -> Cell, only pairs where a lie was heard, the two sources kept
apart as everywhere else. A source with no labels (`jev` after `label --no-jev`) has `{}`. Keys
are model names; a human seat is its own `human` key. Each lie (`false_role_claim`,
`false_report`, `bussing`; not `hidden_knowledge`) counts once for every non-wolf seat alive
when it was told, other than the liar and the players the lie's vote rule names.

Cell: `heard`, `voters`, `followed`, `comparisons`, `moved`, `baseline_voters` (integers);
`vote_follow_rate`, `belief_shift_rate`, `baseline_rate` (number | null); `vote_follow_ci`,
`belief_shift_ci` (Wilson 95% `[lo, hi]` | null).
- `followed`: the listener's next vote that day went the way the lie implied (the `lie_success`
  rule: not the liar for a role claim; the reported player for a wolf report, neither the
  reported player nor the liar for a clear; the bussed wolf for bussing). Abstains never follow.
- `moved`: the listener's next suspicion update moved the lie's subject the lie's way: down on
  the liar for a role claim, up (wolf report) or down (clear) on the reported player for a seer
  report, up on the bussed wolf.
- `baseline_rate`: the listener's `baseline` rate for each kind of claim in this cell, weighted
  by the cell's votes of that kind; `baseline_voters` counts the votes that had one.

`baseline`: claim kind -> listener model -> `{heard, voters, followed, vote_follow_rate,
vote_follow_ci}`, read from truthful claims in the log, so it is shared by both sources. The
same listeners and next-vote rule as a lie, with the matching direction:
- `role_claim`: a player claimed their real role; followed = did not vote them. Compared with
  `false_role_claim` (and a Jev `false_report`, which has no logged seer claim).
- `seer_wolf` / `seer_clear`: the real seer reported a player's true side; followed as for a
  `false_report` with that result.
- `accusation`: a non-wolf accused only real wolves; followed = voted one of them. Compared
  with `bussing`.

`summary`: `games`, `unfinished_games`, `cost_usd`, `input_tokens`, `output_tokens`,
`wolf_wins`, `village_wins`, `draws`, `wolf_win_share`, `village_win_share` (number | null),
`lies` (source -> `{lies, statements, lie_rate, lie_success}`), `top_openskill`
(`{model, ordinal}` | null), `top_wolf_win` (`{model, win_rate, seats}` | null),
`top_share_moved` (source -> `{model, share_moved, comparisons}` | null), `min_comparisons`.

Speed: `calls`, `timed_calls` (integers), `median_latency_ms` (number | null),
`p90_latency_ms` (integer | null), `output_tokens_per_s`, `tokens_per_game`,
`cost_usd_per_game` (number | null), `self_hosted` (boolean: calls made and none cost anything).

`profiles` (added for #297; additive, so the contract version stays 1): one ModelProfile per
model in `leaderboard.json`, keyed by model name. Everything in it is also elsewhere in the
report; it is gathered here so the profile page needs one lookup.

| Field | Type | Notes |
|---|---|---|
| `model` | string | Same as the key |
| `games`, `seats`, `win_rate`, `win_ci`, `wolf_win_rate`, `village_win_rate`, `openskill`, `lie_rate` | | Copied from the model's `leaderboard.json` row, same types and nulls |
| `statements` | integer | Public statements, the lie-rate denominator |
| `detection` | `{n: integer, auc: number\|null, brier: number\|null}` | Village seats, as `models.<m>.village.detection` without `by_day`. `n` 0 and nulls when it never played village |
| `median_latency_ms` | number \| null | From `speed` |
| `voice` | `{voice_id: string, fallback: boolean}` | ElevenLabs voice for this model's clips, from `configs/voices.yaml`. `fallback` is true when the model has no entry and gets `default_model_voice` |
| `fools` | `{self_tag: [PairRow], jev: [PairRow]}` | Listeners who heard this model lie: its row of `deception_matrix.pairs`. `model` is the listener |
| `fooled_by` | `{self_tag: [PairRow], jev: [PairRow]}` | Liars this model heard: its column of `deception_matrix.pairs`. `model` is the liar |

PairRow is a deception Cell plus `model`. Lists are sorted by `vote_follow_rate`, highest first
(null last), then `voters` descending, then model name. A source with no labels gives `[]`. The
same exploratory caveat applies: show `voters` and the CI, and treat thin cells as noise.

There is no avatar field. Avatars are viewer assets; `avatarFor(model)` in
`viewer/src/data/models.ts` maps a model to its image and returns null (placeholder) until the
art is in the repo.

```json
{"games": 3, "unfinished_games": 0, "label_sources": ["self_tag", "jev"], "exploratory": ["lie_success", "persuasion"],
 "openskill": {"overall": {"a": {"mu": 25.0, "sigma": 7.95, "ordinal": 1.14}}, "wolf": {}, "village": {},
               "wolf_side": {"overall": {"mu": 5.97, "sigma": 7.70, "ordinal": -17.12}, "sided": {"mu": 5.69, "sigma": 7.70, "ordinal": -17.42}}},
 "speed": {"gemini-flash": {"calls": 12, "timed_calls": 12, "median_latency_ms": 1610.0, "p90_latency_ms": 2232,
                            "output_tokens_per_s": 142.4, "tokens_per_game": 42470.0, "cost_usd_per_game": 0.0096, "self_hosted": false}},
 "deception_matrix": {"exploratory": true,
   "pairs": {"jev": {}, "self_tag": {"gemini-flash": {"deepseek-v3.1": {"heard": 2, "voters": 2, "followed": 2, "vote_follow_rate": 1.0, "vote_follow_ci": [0.34, 1.0],
     "comparisons": 2, "moved": 2, "belief_shift_rate": 1.0, "belief_shift_ci": [0.34, 1.0], "baseline_rate": 0.25, "baseline_voters": 2}}}},
   "baseline": {"role_claim": {"deepseek-v3.1": {"heard": 2, "voters": 2, "followed": 1, "vote_follow_rate": 0.5, "vote_follow_ci": [0.09, 0.91]}}}},
 "profiles": {"gemini-flash": {"model": "gemini-flash", "games": 1, "seats": 1, "win_rate": 1.0, "win_ci": [0.21, 1.0],
   "wolf_win_rate": 1.0, "village_win_rate": null, "openskill": {"mu": 26.95, "sigma": 8.11, "ordinal": 2.64},
   "statements": 4, "lie_rate": {"self_tag": 0.5, "jev": null}, "detection": {"n": 0, "auc": null, "brier": null},
   "median_latency_ms": 1610.0, "voice": {"voice_id": "cgSgspJ2msm6clMCkdW9", "fallback": false},
   "fools": {"self_tag": [{"model": "deepseek-v3.1", "heard": 2, "voters": 2, "followed": 2, "vote_follow_rate": 1.0, "...": "..."}], "jev": []},
   "fooled_by": {"self_tag": [], "jev": []}}},
 "models": {"...": "..."}, "by_group": {"...": "..."}, "summary": {"...": "..."}}
```

### Highlights: `highlights.json`

- Path: `<batch>/highlights.json`, at the batch root (not in `report/`). Schema:
  `schema/highlights.schema.json`.
- Produced by: `societysim highlights <batch> [--top N]` (`analysis/highlights.py`).
- Shape: a list, best first (score, then game id, then first seq).

| Field | Type | Notes |
|---|---|---|
| `type` | `lie_moved_vote` \| `wolf_survived_vote` \| `doctor_save` \| `seer_led_elimination` | |
| `score` | number | Rounded to 2 places. Only for ordering |
| `game_id` | string | |
| `day` | integer | |
| `seq` | [integer, integer] | First and last event of the moment |
| `actor`, `target` | seat id \| null | |
| `actor_name`, `actor_model`, `target_name`, `target_model` | string \| null | |
| `description` | string | One sentence. Names roles, so it spoils the game |
| `link` | string | A viewer query string `?game=<id>&seq=<n>&mode=narrator`; prefix it with the viewer URL |

```json
[{"type": "doctor_save", "score": 3.0, "game_id": "c-g0001", "day": 2, "seq": [57, 57],
  "actor": "p1", "actor_name": "Cleo", "actor_model": "a", "target": "p4", "target_name": "Dev", "target_model": "b",
  "description": "Cleo (p1, doctor) saved Dev (p4) from the night kill.", "link": "?game=c-g0001&seq=57&mode=narrator"}]
```

### Narration: `<game_id>.audio/index.json` and its mp3s

- Path: `<batch>/<game_id>.audio/index.json`, and one mp3 per clip in the same dir. Schema:
  `schema/narration_index.schema.json`.
- Produced by: `uv run python -m societysim.analysis.narrate <batch>/<game_id>.jsonl
  [--select speech,wolf_chat,highlight]` (`--dry-run` prints the estimate and writes nothing).
  Absent for games nobody narrated: the viewer must work without it.
- Narrator lines are fixed templates, one after each public phase start, death reveal,
  elimination, no-elimination and game end. Every possible line is generated once into the
  phrase bank, `narration/bank/<sha1>.mp3` plus `narration/bank/index.json`
  (`narrate --build-bank`), and copied into the game's `.audio` dir, so narrating a game costs
  nothing. The viewer never reads the bank.
- Two narrator voices: `narrator` (Callum) for day phases, eliminations and the result,
  `narrator_night` (Harry) for night falling and the night's deaths. Player clips use a voice
  per model, from `configs/voices.yaml`.
- Categories: `narrator` (always), and only when selected: `speech` (public statements),
  `wolf_chat` (the wolf channel) and `highlight` (highlights.json descriptions, narrator voice).
  **`wolf_chat` and `highlight` carry hidden information: play them only in narrator mode.**
  `narrator` and `speech` are public and safe in villager mode. Private reasoning, night
  actions and the role list are never voiced.

| Field | Type | Notes |
|---|---|---|
| `game_id` | string | |
| `model_id`, `output_format` | string | ElevenLabs settings, e.g. `eleven_flash_v2_5`, `mp3_44100_64` |
| `voices` | object, `"narrator"`, `"narrator_night"` or seat id -> voice id | |
| `clips` | list of `{seq, category, speaker, voice, voice_id, text, file}` | In `seq` order. `seq` is the event the clip follows (a highlight follows its last event; it comes after that event's own clip). `category` is `narrator`, `highlight`, `speech` or `wolf_chat`. `speaker` is `"narrator"` or a seat id. `voice` is `"narrator"`, `"narrator_night"` or the speaker's model name. `file` is relative to the `.audio` dir: `<sha1(text + voice_id)>.mp3`, so two clips with the same text and voice share a file |

```json
{"game_id": "fixture-0001", "model_id": "eleven_flash_v2_5", "output_format": "mp3_44100_64",
 "voices": {"narrator": "N2lVS1w4EtoT3dr4eOWO", "narrator_night": "SOYHLrjzK2X1ezoPC6cr", "p0": "cjVigY5qzO86Huf0OWal"},
 "clips": [{"seq": 1, "category": "narrator", "speaker": "narrator", "voice": "narrator_night",
            "voice_id": "SOYHLrjzK2X1ezoPC6cr", "text": "Night 1. The village sleeps.",
            "file": "c27ca0c1984731b0e3587efc1a9b8bd6cef9e59e.mp3"}]}
```

Neon has no narration: in Neon mode there is no audio location. Audio exists only as static
files.

### Not part of the contract

- `run_meta.json` (batch) and `<game_id>.run_meta.json` (per game): runner bookkeeping (budget,
  spend, config, agent type). Not viewer-facing. If a viewer PR starts reading it, that PR adds
  it here with a schema.
- `agreement.json` (self-tag vs Jev agreement), `report/report.md`, the report PNGs and `.cache/`.

## Game setup

The settings screen has two modes. Both produce one **setup JSON**, a `GameSetup`
(`src/societysim/setup.py`, schema `schema/game_setup.schema.json`). The UI can show or
download the JSON for `uv run societysim play --setup <file.json>`, or send it to the game server
as `setup` in `POST /api/games` (see "Play API"). `play --setup` reads YAML too.

### Basic

`{"mode": "basic"}` is the default game: 10 seats, 2 wolves, 1 seer, 1 doctor, 6 villagers.
Models come from `default_lineup` in `models_catalog.json`, shuffled with the seed and repeated
to fill the seats. In Basic only `seed` and `human_seat` can change; setting any other field to
something other than its default is an error ("n_wolves is fixed in basic mode; use mode
'advanced'"). With a human seat it is 2 wolves among 1 human and 9 models.

```json
{"mode": "basic", "seed": 7, "human_seat": "p0"}
```

`uv run societysim play --preset basic [--human-seat p0] [--seed 7]` runs the same thing.

### Advanced

Every field is optional; a missing field keeps its Basic default.

| Field | Type | Limits and notes |
|---|---|---|
| `mode` | `"basic"` \| `"advanced"` | Default `"basic"` |
| `n_players` | integer | 7..10, default 10. The engine and RULES.md allow 7..10 today |
| `n_wolves` | integer | ≥ 1, and fewer than the villager side: at most `(n_players - 1) // 2` (3 for 7 or 8 players, 4 for 9 or 10) |
| `special_roles` | `{seer: bool, doctor: bool}` | Default both true. The rest of the seats are villagers |
| `seats` | list of `{seat, model}` or null | One entry per seat `p0..p{n_players-1}`, each once, any order. `model` is a catalog `name` or `"human"` (at most one). Can't be combined with `model_pool` |
| `model_pool` | list of model names or null | At least 1, no `"human"`. Null means `default_lineup`. Fills every seat but the human's |
| `rotation` | `"in_order"` \| `"shuffle"` | How `model_pool` fills the seats: `in_order` repeats the pool from p0, skipping the human seat; `shuffle` (default) shuffles the pool with the seed first. Ignored with `seats` |
| `human_seat` | seat id or null | A seat below `n_players`. With `seats`, put `"human"` on that seat instead (or on both) |
| `seed` | integer | Default 0. Same setup and seed, same game |

Model names must exist in `configs/models.yaml`. Unknown fields are rejected. Rule flags (days,
discussion rounds and so on) are not part of the setup; they keep their defaults.

```json
{"mode": "advanced", "n_players": 8, "n_wolves": 3,
 "special_roles": {"seer": true, "doctor": false},
 "model_pool": ["qwen3-235b", "gemma4-26b", "deepseek-v3.1"], "rotation": "in_order",
 "human_seat": "p5", "seed": 42}
```

```json
{"mode": "advanced", "n_players": 7,
 "seats": [{"seat": "p0", "model": "qwen3-235b"}, {"seat": "p1", "model": "human"},
           {"seat": "p2", "model": "gemma4-26b"}, {"seat": "p3", "model": "gpt-oss-120b"},
           {"seat": "p4", "model": "qwen3-235b"}, {"seat": "p5", "model": "gemini-flash"},
           {"seat": "p6", "model": "deepseek-v3.1"}]}
```

An invalid setup makes `play` print one `invalid setup: <field>: <reason>` line per error and
exit 1 before anything runs. The UI should check what the JSON Schema can express (types,
ranges, seat id pattern, unknown keys) and the cross-field rules above (wolves vs villagers,
seats cover every seat once, one human, `seats` xor `model_pool`, basic fields fixed).

### Models catalog: `models_catalog.json`

- Path: the site root (next to `index.html`), staged by `scripts/deploy-space.sh` (also with
  `--out`). Produced by `uv run societysim catalog --out <dir> [--check]`.
- Use it to build the model dropdowns. Grey out entries with `available: false` and show
  `unavailable_reason`.

| Field | Type | Notes |
|---|---|---|
| `human` | `"human"` | The model value for a human seat |
| `default_lineup` | list of model names | What Basic plays |
| `models` | list, in `models.yaml` order | One entry per model, below |
| `models[].name` | string | The value `seats` and `model_pool` take |
| `models[].display_name` | string | Upstream model id, e.g. `Qwen3-235B-A22B-Instruct-2507` |
| `models[].provider` | `"huggingface"` \| `"google"` \| `"openai"` \| `"amd"` \| `"other"` | Who serves it |
| `models[].sponsor` | `"huggingface"` \| `"amd"` \| null | Sponsor track tag |
| `models[].price_tier` | `"free"` \| `"low"` \| `"medium"` \| `"high"` \| `"unknown"` | USD for 1M input + 1M output tokens: free = priced at 0, low < $1.50, medium < $3, high ≥ $3, unknown = no price |
| `models[].structured` | `"json_schema"` \| `"json_object"` \| `"none"` | Structured output mode |
| `models[].structured_ok` | boolean | False when `structured` is `"none"` |
| `models[].color` | string \| null | Viewer color |
| `models[].available` | boolean | False for a self-hosted model whose base URL isn't set where the catalog was built, or (with `--check`) whose server doesn't serve it |
| `models[].unavailable_reason` | string \| null | Short reason; never holds a URL |

Availability is a snapshot from the machine that built the catalog, not live.

```json
{"human": "human", "default_lineup": ["qwen3-235b", "llama4-maverick", "gemma4-26b"],
 "models": [{"name": "amd-gpt-oss-20b", "display_name": "gpt-oss-20b", "provider": "amd",
   "sponsor": "amd", "price_tier": "free", "structured": "json_schema", "structured_ok": true,
   "color": "#ed1c24", "available": false,
   "unavailable_reason": "missing API base for amd-gpt-oss-20b; set AMD_VLLM_BASE_URL in .env"}]}
```

## Play API

A visitor starts a game and plays one seat against the models, or watches an all-AI game. The
server is `src/societysim/server/` (FastAPI, the `server` extra):
`uv run --extra server uvicorn --factory societysim.server.app:create_app`. Request and response
shapes are in `schema/play_api.schema.json` (`$defs`, plus `x-endpoints` mapping each endpoint
to them). All bodies are JSON and closed: an unknown key is a 422. Every error is
`{"error": <code>, "message": <text>}` (`$defs.Error`); see "Errors and limits" below.

| Endpoint | Auth | Request | Response |
|---|---|---|---|
| `POST /api/games` | none (Turnstile token in the body) | `StartGame` | `StartedGame` |
| `GET /api/games/{id}/me` | seat token | | `Me` |
| `POST /api/games/{id}/action` | seat token | `ActionBody` | `{"accepted": true}` |
| `GET /api/limits` | none | | `{"play_enabled": bool, "remaining_today": int}` |
| `GET /api/health` | none | | `{"ok": true}` |

### Starting a game

```json
{"setup": {"mode": "basic"}, "turnstile_token": "<token>", "watch_only": false}
```

- `setup` is a `GameSetup` (see "Game setup" above), default `{"mode": "basic"}`. The server
  always draws the seed itself, so a `seed` in the setup is ignored: a known seed would give away
  the role deal. Without `watch_only`, `human_seat` defaults to `p0`; with `seats`, one seat must
  have model `"human"`. A watch-only game must not have a human seat.
- `turnstile_token`: at most 2048 characters. Checked by the admission step (#281).
- Response: `{"game_id": "visitor-<16 hex>", "seat": "p0", "seat_token": "<43 chars>"}`;
  `seat` and `seat_token` are null for a watch-only game. The token is 256 random bits and the
  server keeps only its SHA-256, so it can't be shown again. Keep it for the rest of the game.
- Errors: 422 bad setup (the message names each problem), 400 for a model outside the allowlist,
  and the limits below (403, 429, 503).

The other seats are LLM agents from `configs/models.yaml`. The game streams to Neon like
`play --neon` and is logged to `runs/visitor/<game_id>.jsonl` with run_meta `agents: "llm"` and
the visitor's seat as model `"human"`, so it stays out of the rankings.

### Your seat: `GET /api/games/{id}/me`

Send `Authorization: Bearer <seat_token>`. 404 for an unknown game (ended games are kept about
an hour), 401 for a missing or wrong token (and for every token on a watch-only game).

| Field | Type | Notes |
|---|---|---|
| `game_id`, `seat`, `name` | string | Your seat and its player name |
| `status` | `"running"` \| `"finished"` \| `"forfeit"` \| `"failed"` | |
| `winner` | `"wolves"` \| `"villagers"` \| `"draw"` \| null | Set when finished |
| `role` | role | Your own role only |
| `alive` | boolean | |
| `day`, `phase` | integer, phase | The game clock |
| `observation` | string \| null | Exactly what a model in your seat is shown, from `agents/observation.py`: your private knowledge (wolf partners, Seer checks, Doctor protects), the dead with their revealed roles, and the public history. Other players' words are quoted. Null once you are dead |
| `pending_action` | object \| null | Set while the game waits for you |
| `pending_action.kind` | `statement` \| `vote` \| `wolf_chat` \| `lone_wolf` \| `seer` \| `doctor` | Which reply shape to send |
| `pending_action.targets` | list of seat ids | Legal choices (accuse targets for a statement); a vote may also be `"abstain"` |
| `pending_action.others` | list of seat ids | The players you may rate in `suspicion` |
| `pending_action.deadline` | number | Unix seconds |
| `players` | list of `{seat, name, alive}` | Public |

Nothing else is returned: no other seat's role, reasoning or suspicion. While a visitor game
runs, Neon gets only its public events, with `private_reasoning` and `suspicion` removed (so `seq`
has gaps and there is no `game_start`). Once the game reaches `game_end` the full log replaces
them. A forfeited or failed game keeps the public-only rows.

### Moving: `POST /api/games/{id}/action`

Same auth. The body is the reply shape a model sends for `pending_action.kind`
(`agents/schemas.py`): `StatementResponse`, `VoteResponse`, `WolfChatResponse` or
`TargetResponse` (for `lone_wolf`, `seer`, `doctor`).

```json
{"private_reasoning": {"thoughts": "", "suspicion": [{"player": "p3", "p": 0.8}]},
 "action": {"text": "Bea has dodged every question.", "accuse": ["p3"],
            "claim_role": null, "seer_report": null}}
```

- `suspicion` may leave players out; they default to 0.5 and the event gets
  `meta.suspicion_default: true`, as for the terminal human seat. `thoughts` is not logged.
- Statement `text` and wolf chat `message`: control and format characters are stripped, line
  breaks become spaces, and the result must be 1 to 280 characters.
- The orchestrator's own check runs before the move is accepted: legal target, suspicion only
  over `others`, a valid `seer_report`.
- Errors: 409 when no turn is pending for you (including a second move for the same turn), 422
  for a bad reply (the detail says why; the turn stays open), 413 for a body over 8 KB.

### Timeouts

Each turn waits 90 s. A turn that runs out falls back exactly like a model's invalid reply: two
`invalid_output` events (`error: "turn timed out"`), then `fallback_action` (abstain for a vote,
a random legal target at night, silence for a statement). The retry does not wait again. Three
timeouts in a row end the game as a forfeit: `status: "forfeit"`, the log stops without
`game_end`, run_meta gets `forfeit: true`, and Neon marks the game `failed`.

Visitor text reaches the models only through `observation.py`, as one line in double quotes it
can't close or break out of, like every other player's speech.

### Errors and limits

Every error body is `{"error": "<code>", "message": "<text>"}`. Map on `error`; `message` is
plain English the server wrote itself (never provider text, a stack trace or a key) and may change.

| `error` | Status | When |
|---|---|---|
| `invalid_request` | 400, 422 | Bad body or setup; 400 for a model visitors may not use |
| `unauthorized` | 401 | Missing or wrong seat token |
| `bot_check_failed` | 403 | Turnstile token missing, wrong, or Cloudflare unreachable |
| `forbidden` | 403 | Reserved |
| `game_not_found` | 404 | Unknown game (or path) |
| `not_your_turn` | 409 | No turn pending for your seat |
| `payload_too_large` | 413 | Body over 16 KB (8 KB for a move) |
| `rate_limited` | 429 | Too many requests from your IP; wait `Retry-After` seconds |
| `active_game_exists` | 429 | Your IP already has a game running |
| `too_many_games_today` | 429 | Your IP's games for this UTC day are used up; `Retry-After` is the time to midnight UTC |
| `at_capacity` | 503 | All game slots are busy |
| `budget_exhausted` | 503 | Today's spend cap is reached (or the spend ledger is unreachable) |
| `play_disabled` | 503 | Play is switched off |
| `internal_error` | 500 | A bug; nothing else is said |

Limits, all set by env on the server (defaults in brackets):

- `PLAY_RATE_PER_MIN` [120]: requests per minute per IP, token bucket. Poll `/me` at most once
  a second.
- `PLAY_MAX_ACTIVE_PER_IP` [1], `PLAY_MAX_GAMES_PER_IP_DAY` [5], `PLAY_MAX_CONCURRENT` [4]
  running games in all. Watch-only games count the same.
- `PLAY_MAX_GAME_MINUTES` [45]: a longer game is ended (`status: "failed"`).
- `PLAY_DAILY_BUDGET_USD` [2.00]: model spend per UTC day, booked per call in Neon
  (`visitor_spend`, not readable through the Data API). A game starts only if what is left
  covers its estimate (the batch runner's price prior). Each model call first holds its worst
  case (a token per prompt byte plus 2000, and the full output cap, at the model's price) and
  runs only if that fits; the hold then settles to the real cost. So spend never passes the
  cap. When a call doesn't fit, that AI turn is played by a scripted agent and the game
  finishes. A failed call's error is stored, sent to Neon and put in the retry prompt only as a
  category (`provider_error`, `timeout`, `invalid_output`), never the provider's text. `remaining_today` in
  `/api/limits` is how many default games that leaves.
- `PLAY_ALLOWED_MODELS` [qwen3-235b, llama4-maverick, gemma4-26b, gpt-oss-120b, deepseek-v3.1,
  gemini-flash-lite, gpt-5-nano]: Basic mode uses the default lineup minus anything not allowed.
  `PLAY_MAX_TOKENS` [2048] caps each call.
- `PLAY_ENABLED=0` closes play at once (`/api/limits` says `play_enabled: false`).
- `PLAY_TRUSTED_PROXIES` [none]: CIDRs whose `X-Forwarded-For` / `CF-Connecting-IP` are
  believed; `X-Forwarded-For` is read right to left, skipping trusted hops. Otherwise the
  socket peer is the client. On HF Spaces (`SPACE_ID` set) the server refuses to start
  without it, because every request arrives from HF's proxy, which reaches the container from a
  private address and appends the visitor's IP to `X-Forwarded-For`. Set
  `PLAY_TRUSTED_PROXIES=10.0.0.0/8,172.16.0.0/12,192.168.0.0/16`. If Cloudflare proxies the
  Space's hostname, add Cloudflare's published ranges too.
- CORS allows only `https://society-sim.us`, `https://www.society-sim.us`,
  `https://societysim.pages.dev`, `https://$SPACE_HOST`, and `http://localhost:5173` in dev
  mode, without credentials.
- The server will not start without `TURNSTILE_SECRET_KEY` and `NEON_DATABASE_URL` unless
  `PLAY_DEV_MODE=1` (local development only, which also skips Turnstile when its key is unset).

## Neon Data API

- Base URL: `VITE_NEON_DATA_API_URL` (PostgREST). Every request needs a JWT; without an
  `Authorization` header the API answers 400.
- Auth: `GET <VITE_NEON_AUTH_URL>/token/anonymous` returns `{"token": "<jwt>", "expires_at": "<ISO 8601>"}`
  for the read-only `anonymous` role. It is valid about 1 h. Send it as `Authorization: Bearer <token>`.
  Cache it and fetch a new one shortly before `expires_at`, or after a 400 or 401 (retry once). Never
  log it. `VITE_NEON_ANON_KEY` (a pinned JWT) is optional and not recommended: it expires.
- Env vars (public repo variables): `VITE_NEON_DATA_API_URL`, `VITE_NEON_AUTH_URL`, optional
  `VITE_NEON_ANON_KEY`. Never `NEON_DATABASE_URL`.
- Grants to `anonymous` (`neon/schema.sql`): `usage` on schema `public`; `select` on `games`,
  `events`, `labels`, `leaderboard`, `leaderboard_by_batch`, `lies_by_model`. No writes.
- Produced by: `NeonSink` during `societysim batch --neon` / `play --neon` (live, game by
  game), or `societysim sync-neon <batch>` (backfill). JSONL stays the source of truth.
- Types below are Postgres `information_schema.columns.data_type`. In JSON, `integer` and
  `bigint` arrive as numbers, `jsonb` as JSON, `timestamp with time zone` as an ISO 8601 string.
  `numeric` (`win_rate`) may arrive as a string such as `"0.571"`: read it with `Number()`.
- Column order below is the table's order. View columns and their order are fixed.

### Tables

`games`, one row per game:

<!-- neon:games -->
| Column | Type | Notes |
|---|---|---|
| `game_id` | text | Primary key |
| `batch_id` | text | Run dir name (`runs/<batch_id>`); nullable, but current code always sets it |
| `status` | text | `running` \| `finished` \| `failed` |
| `winner` | text | `wolves` \| `villagers` \| `draw` \| null |
| `models` | jsonb | Model name per seat, in seat order (`["a", "b", ...]`) |
| `started_at` | timestamp with time zone | |
| `ended_at` | timestamp with time zone | Null while running |
| `agents` | text | `scripted` \| `llm` \| null (rows from before #219). Scripted games are replayable but left out of the three views |

`events`, one row per event:

<!-- neon:events -->
| Column | Type | Notes |
|---|---|---|
| `game_id` | text | |
| `seq` | integer | Primary key with `game_id` |
| `day` | integer | |
| `phase` | text | |
| `type` | text | Event type |
| `actor` | text | Seat id or null |
| `schema_version` | integer | |
| `event` | jsonb | The full event, exactly the JSONL line (`schema/events.schema.json`) |

`labels`, one row per line of `<game_id>.labels.jsonl`:

<!-- neon:labels -->
| Column | Type | Notes |
|---|---|---|
| `game_id` | text | |
| `idx` | integer | Line index in the labels file; primary key with `game_id` |
| `label_type` | text | |
| `actor` | text | Seat id |
| `source_seq` | integer | |
| `source` | text | `self_tag` \| `jev` \| `engine` |
| `label` | jsonb | The full label line (`$defs.Label`) |

### Views

All three count finished games only, leave out `human` seats and leave out games with
`agents = 'scripted'` (null `agents` rows stay in).

`leaderboard`: per model, pooled over every batch. A seat wins if its team won; a draw is not a
win.

<!-- neon:leaderboard -->
| Column | Type | Notes |
|---|---|---|
| `model` | text | |
| `seat_games` | bigint | Seats played, summed over games (a model can fill several seats in one game). Not a game count: it matches `seats` in `leaderboard.json`, not `games` |
| `wins` | bigint | |
| `win_rate` | numeric | 3 decimals, 0..1 |

`leaderboard_by_batch`: the same per batch, with draws.

<!-- neon:leaderboard_by_batch -->
| Column | Type | Notes |
|---|---|---|
| `batch_id` | text | |
| `model` | text | |
| `seat_games` | bigint | |
| `wins` | bigint | |
| `draws` | bigint | |
| `win_rate` | numeric | 3 decimals, 0..1 |

`lies_by_model`: lie labels per liar model, from `self_tag` and `jev` only, for the four lie
types (`false_role_claim`, `false_report`, `bussing`, `hidden_knowledge`). Not split by game
status.

<!-- neon:lies_by_model -->
| Column | Type | Notes |
|---|---|---|
| `batch_id` | text | |
| `model` | text | |
| `source` | text | `self_tag` \| `jev`; never sum the two |
| `label_type` | text | |
| `lies` | bigint | |

### Requests the viewer makes

```
GET {base}/games?select=game_id,models&order=started_at.desc.nullslast      game list
GET {base}/games?select=game_id&status=eq.finished&or=(agents.is.null,agents.neq.scripted)   finished-game count
GET {base}/events?select=event&game_id=eq.<id>&order=seq                    a whole game
GET {base}/events?select=event&game_id=eq.<id>&seq=gt.<n>&order=seq         live poll after seq n
GET {base}/labels?select=label&game_id=eq.<id>&order=idx                    a game's labels
GET {base}/leaderboard?select=model,seat_games,win_rate&order=win_rate.desc.nullslast
GET {base}/leaderboard_by_batch?select=*&batch_id=eq.<batch_id>
GET {base}/lies_by_model?select=*&batch_id=eq.<batch_id>
```

The finished-game count needs the `or=(agents.is.null,agents.neq.scripted)` filter to count the
same games the views do. Without it, scripted games are counted while `leaderboard` leaves them
out, and the count won't match the rows.

Each returns a JSON array of row objects with the selected columns, for example
`[{"event": {...}}]` or `[{"model": "a", "seat_games": 14, "win_rate": "0.571"}]`. URL-encode
ids in filters (`encodeURIComponent`).

The Neon leaderboard is simpler than `leaderboard.json`: no OpenSkill, detection, lies or cost.
Those exist only in the static report files.
