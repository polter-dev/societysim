# Spot the wolf: port notes

Logic is headless and pure. Restyle `SpotWolf.tsx` freely; don't move logic into it.

- `guesses.ts`: pure state and scoring. `toggleMark`, `lockIfRevealed`, `buildResults`. Roles are only readable through `buildResults`/`revealedRoles`, which return null until `game_end` is in `events[0..idx]`.
- `useSpotWolf.ts`: hook (`{view, toggle}`) and pure `spotWolfView(events, idx, state)`. `SpotWolfView` is all the component sees; before the reveal it has no role info.
- `storage.ts`: localStorage per game (`societysim.spotwolf.v1.<gameId>`), never throws.
- `SpotWolf.tsx`: presentational, plain elements. Marks are buttons with `aria-pressed`. `SpotWolfPanel` is the hook plus component; mount it with `key={gameId}`.

Styling hooks: `.spotwolf`, `.spotwolf-seats`, `.spotwolf-results`. Mounted in `App.tsx` `Game` inside `<details className="replay-spotwolf">`; move it wherever the illustrated layout wants it.

Keep: no role words in `aria-label`/`title` before the reveal (App.test checks it), and no per-seat attributes the table tests count (`data-seat`).

Scoring: 10 per wolf found, minus 5 per wrong mark, floor 0. Models are scored the same way using their top-2 suspects from their last suspicion report; wolves are left out of the comparison.
