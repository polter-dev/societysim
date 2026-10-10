# Log schema (v1)

Each game writes `runs/<batch_id>/<game_id>.jsonl`: one event per line, never edited after the game. Lie labels go in `<game_id>.labels.jsonl` next to it.

The source of truth is `src/societysim/logging/events.py`. `scripts/gen-schema.sh` turns it into `schema/events.schema.json` and `viewer/src/types/events.ts`; don't edit those two by hand. Changing the contract needs its own ticket, a `SCHEMA_VERSION` bump and regenerated files in the same PR.

`fixtures/sample_game.jsonl` is a hand-written 7-player game with every event type, and `fixtures/sample_game.labels.jsonl` holds exactly the lies planted in it. Build and test against those.

## Event

Every line has all of these keys. Empty ones are `null`.

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | `2` | Readers also accept `1`: same layout, but `meta.usage` has no `cached` key. |
| `game_id` | string | |
| `seq` | int | 0-based line index. Unique and increasing; the viewer's scrubber keys on it. |
| `t` | int | Engine step. 0 at `game_start`, +1 at every `phase_start`. |
| `day` | int | 0 for `game_start`. Night N and day N share `day = N`; the game starts with night 1. |
| `phase` | `setup \| night \| day \| vote` | `day` is the discussion; `vote` is the vote and its result. Timeline markers group by `(day, phase)`. |
| `type` | EventType | See below. |
| `visibility` | `public \| wolves \| seer \| doctor \| narrator` | Who could see the event in-game. `narrator` means no player. |
| `actor` | seat id or null | Who acted. |
| `target` | seat id or null | Who it was aimed at. |
| `public_text` | string or null | What players or the narrator saw. |
| `private_reasoning` | string or null | The actor's private `thoughts`. Never shown to other players. |
| `suspicion` | list or null | `[{player, p}]`: the actor's P(wolf) for every other living player, at the time of this event. |
| `meta` | object | Type-specific keys, below. Keys with no value are left out. |

`actor` and `target` are always seat ids, `p0`…`p8`. Names, models, colors and roles live in `game_start.meta.seats`, so nothing ever parses a name. Wall-clock time is `meta.ts` (ISO 8601, UTC), not `t`. Runs that must be byte-identical (scripted agents) may leave `ts` out.

### Event types

| Type | Phase | Visibility | actor → target | meta |
|---|---|---|---|---|
| `game_start` | setup | narrator | – | `seed`, `seats: {p0: {name, model, color, role}, …}` |
| `phase_start` | any | public | – | `public_text` is a heading like "Night 2" |
| `wolf_chat` | night | wolves | wolf → – | `round`, `usage`; `public_text` is the message |
| `wolf_proposal` | night | wolves | wolf → proposed victim | `round` |
| `night_kill` | night | wolves | – → victim | `method: consensus \| plurality \| tiebreak`, `tally` of final proposals |
| `seer_check` | night | seer | Seer → checked player | `result: wolf \| not_wolf`, `usage` |
| `doctor_save` | night | doctor | Doctor → protected player | `saved`, `usage` |
| `death_reveal` | night | public | – → dead player | `role` (when roles are revealed) |
| `statement` | day | public | speaker → – | `round`, `usage`, `truncated`, `fallback` |
| `accusation` | day | public | speaker → accused | `statement_seq` |
| `role_claim` | day | public | speaker → – | `role` (claimed), `statement_seq` |
| `seer_claim` | day | public | speaker → claimed checked player | `result`, `night`, `statement_seq` |
| `vote` | vote | public | voter → choice (null = abstain) | `usage`, `fallback` |
| `elimination` | vote | public | – → eliminated player | `tally`, `role` |
| `no_elimination` | vote | public | – | `tally`, `reason: tie \| all_abstain` |
| `invalid_output` | any | narrator | player | `action`, `attempt`, `error`, `raw` |
| `fallback_action` | any | narrator | player → fallback target | `action`, `reason`, `usage` |
| `game_end` | any | public | – | `winner: wolves \| villagers \| draw`, `reason`, `roles: {p0: role, …}` |

### Conventions

- **Night order.** Each wolf turn logs `wolf_chat` then `wolf_proposal`. A lone wolf logs only `wolf_proposal`. Then `seer_check`, `night_kill` (the pack's choice), `doctor_save`, and finally `death_reveal`. A dead Seer or Doctor logs nothing.
- **Deaths.** Every death has exactly one event: `death_reveal` for night kills, `elimination` for votes. If nobody died in the night there is one `death_reveal` with `target: null`. `death_reveal` is logged at the end of the night, before the win check, so a night that ends the game still records its death. Players see it at the start of the next day.
- **Saves.** `night_kill` is who the wolves attacked. If `doctor_save` has the same target, `meta.saved` is true and nobody dies. A save that matches no kill has `meta.saved: false`.
- **Self-tags.** A statement's structured fields become events right after it, each with `meta.statement_seq` pointing at the statement: one `accusation` per accused player (several share the same `statement_seq`), then `role_claim`, then `seer_claim`. `seer_claim.target` is the player the speaker says they checked, with `meta.result` and `meta.night`.
- **Votes.** Votes are simultaneous, logged in seat order. `meta.tally` is always logged on `elimination` / `no_elimination` and counts only non-abstain votes (`{p2: 3, p0: 3}`).
- **Usage.** `meta.usage = {model, input_tokens, output_tokens, cost_usd, latency_ms, attempts, cached}` sits on the event that carries each model call's final answer: `wolf_chat` (or a lone wolf's `wolf_proposal`), `seer_check`, `doctor_save`, `statement`, `vote`. It covers every attempt of that call. `cached: true` means the reply was served from the cassette cache: the numbers are the original call's, but nothing was spent this time. So the game's real cost is the sum of `cost_usd` over usages with `cached` false (or missing, in version 1 logs). Derived events (`accusation`, `night_kill`, …) have none.
- **Failures.** Each rejected reply logs `invalid_output` (`attempt` 1, then 2 after the retry). If the retry also fails, `fallback_action` is logged with the call's usage, followed by the normal action event with `meta.fallback: true`: a `vote` with no target (abstain), a random legal target at night, or the statement `"(silent)"`.
- **Truncation.** A statement over the word cap is cut and gets `meta.truncated: true`.
- **Suspicion.** Only on events that carry an agent reply (`wolf_chat`, `wolf_proposal` for a lone wolf, `seer_check`, `doctor_save`, `statement`, `vote`). Wolves report it too; detection metrics use non-wolf actors only.

## Label

One per line in `<game_id>.labels.jsonl`, all keys always present.

| Field | Type | Meaning |
|---|---|---|
| `schema_version` | `2` | Readers also accept `1`: same layout, but `meta.usage` has no `cached` key. |
| `game_id` | string | |
| `label` | see below | |
| `actor` | seat id | Who lied or erred. Always equals the `actor` of the `source_seq` event. |
| `source_seq` | int | The most specific event the label is about (a `role_claim`, `seer_claim`, `accusation`, `statement` or `vote`). |
| `source` | `self_tag \| jev \| engine` | What detected it: the agent's own structured tags, the Jev classifier, or game facts alone. |
| `confidence` | 0–1 | 1.0 for `self_tag` and `engine`. |
| `detail` | string | One readable sentence. |

| Label | When | `source_seq` |
|---|---|---|
| `false_role_claim` | A player claims a role they don't have. | the `role_claim` |
| `false_report` | A non-Seer reports a check, or the Seer reports a result that contradicts their real one. | the `seer_claim` |
| `bussing` | A wolf accuses or votes for a fellow wolf. | the `accusation` or `vote` |
| `hidden_knowledge` | The Seer holds a wolf result and speaks that day without reporting it. One label per day, on the first such statement. | the `statement` |
| `mis_elimination` | A non-wolf is voted out. One label per voter who voted for them, wolves included (filter by role in metrics). Always `source: engine`. | the `vote` |

## Agent responses

Not part of the log, but listed here because the log mirrors them. Models answer with JSON matching `agents/schemas.py`:

```json
{"private_reasoning": {"thoughts": "...", "suspicion": [{"player": "p3", "p": 0.7}]},
 "action": {"...": "phase-specific"}}
```

| Call | Response model | `action` |
|---|---|---|
| statement | `StatementResponse` | `{text, accuse: [seat ids], claim_role: role or null, seer_report: {target, result, night} or null}` |
| vote | `VoteResponse` | `{vote: seat id or "abstain"}` |
| wolf chat | `WolfChatResponse` | `{message, proposed_target}` |
| Seer, Doctor, lone wolf | `TargetResponse` | `{target}` |

Responses use seat ids; prompts show players as "Ava (p3)". Suspicion is a list rather than a dict so the schema stays closed: Gemini and several HF providers reject objects with dynamic keys. For the same reason the response schemas have no `pattern`, `minimum` or `maximum`; ranges, legality and suspicion completeness are checked after parsing.
