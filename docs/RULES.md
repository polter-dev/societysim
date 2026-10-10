# Game rules (v1)

Standard rules, close to Ultimate Werewolf. Every rule here is a config flag; these are the defaults. The engine (`src/societysim/engine/`) must follow this file exactly. Changing a rule means changing this file in the same PR.

## Setup and flow

- 7–9 players. Default roles: 2 Werewolves, 1 Seer, 1 Doctor, the rest Villagers.
- The game starts with Night 1, then alternates Day and Night.
- **Night:** the wolves agree on one kill in a private wolf channel (2 rounds of message + proposed target; if they still disagree, the plurality target wins and a tie is broken by the seeded RNG). The Seer inspects one player and learns wolf / not wolf. The Doctor protects one player; if that is the wolves' target, they survive.
- **Day:** night results are announced (who died; their role is revealed). Then 2 discussion rounds; every living player makes one public statement (at most 80 words) per round, in a seeded random order. Then everyone votes for a player or abstains, all at once. Plurality is eliminated and their role is revealed.
- The Doctor may protect themself, but not the same player two nights in a row.
- No last words.
- Day cap: 6 days.

## Role counts
- 7 players: 2 wolves, 1 Seer, 1 Doctor, 3 Villagers.
- 8 players: 4 Villagers. 9 players: 5 Villagers.
- A 3-wolf setup is available through config.

## Win checks and draws
- Win checks run after night resolution and after each elimination.
- Villagers win iff living wolves = 0. Wolves win iff living wolves ≥ living non-wolves, which can happen right after a night kill, before discussion.
- If neither side has won after the day-`max_days` vote, the game is a **draw**.
- Metrics report win/draw/loss. OpenSkill treats a draw as a tie.

## Night
- The Seer and Doctor act only while alive. A dead one gets no call and produces no event.
- The Seer's result is the target's role at check time, even if the target dies that night.
- The Doctor may protect any living player, wolves included, subject to the no-repeat rule. A save that matches no kill is logged as `doctor_save` with `meta.saved=false`.
- Wolf proposals must name a living non-wolf.
  - An invalid proposal gets one retry, then falls back to a random legal target.
  - The wolves always kill; there is no wolf abstain.
  - A lone surviving wolf skips the chat rounds and only proposes.

## Votes
Votes name another living player; self-votes are illegal.

- The tally counts only non-abstain votes. If everyone abstains or the top is tied, the result is `no_elimination`.
- `meta.tally` is always logged.

## Dead players
- Dead players make no calls and receive no observations.
- Public history lists them as dead, with their revealed role.

## Suspicion
- Suspicion is the actor's honest P(wolf) for **every other living player**: not self, not the dead.
- Wolves report it too, but detection metrics use only non-wolf actors.

