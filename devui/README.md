# devui

A throwaway test page for checking the backend data in `docs/DATA_CONTRACT.md` without the real
viewer. Plain HTML and JS, no build step, no dependencies. Not part of `viewer/`.

It shows the game list, leaderboard, metrics summary and speed tables, and highlights. Click
a game to get a turn-by-turn table; the "narrator view" checkbox reveals private reasoning,
suspicion, non-public events and roles. Labels show inline. The contract check panel validates
each loaded file against the JSON Schemas in `schema/` (a small required-field and type check).
Neon mode runs the documented queries against a Data API URL and lists the rows.

## Open it locally

```bash
uv run societysim report runs/<batch_id>
scripts/deploy-space.sh --out /tmp/site runs/<batch_id>
python -m http.server -d /tmp/site 8000
# open http://localhost:8000/devui/
```

`--out` copies this page to `devui/` and the schemas to `devui/schema/`. The page reads
`../data/` by default.

## On the live site

It is served at `/devui/` next to the viewer (Cloudflare, the HF Space and Pages), for example
`https://society-sim.us/devui/`, reading the same staged `data/`.

## URL parameters

- `?base=<url>`: batch dir to read (default `../data/`). Needs CORS if it is another origin.
- `?schemas=<url>`: schema dir (default `schema/`).
- `?game=<game_id>`: open that game on load.
- `?neon=<data api url>&key=<anon key>`: Neon mode. Or fill in the fields. Nothing is stored.
