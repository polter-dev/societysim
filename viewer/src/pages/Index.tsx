import { useEffect, useState } from "react";
import { ModelAvatar } from "../components/ModelAvatar/ModelAvatar";
import { modelName } from "../data/modelNames";
import { MalformedIndexError } from "../data/staticSource";
import type { GameSource, GameSummary } from "../data/source";
import "./Games.css";

interface ListProps {
  games: GameSummary[];
  href: (id: string) => string;
  /** Winners are spoilers; they stay hidden until the viewer asks. */
  showWinners?: boolean;
}

/** "18 games, 2 live" for the panel heading. */
export function gamesCount(games: GameSummary[]): string {
  const live = games.filter((g) => g.live).length;
  return `${games.length} ${games.length === 1 ? "game" : "games"}${live ? `, ${live} live` : ""}`;
}

export function filterGames(games: GameSummary[], query: string, status: string): GameSummary[] {
  const search = query.trim().toLowerCase();
  return games.filter(g => (status === 'all' || (status === 'live' ? g.live : !g.live)) &&
    (!search || [g.id, ...(g.models ?? []).flatMap(m => [m, modelName(m)])].some(value => value.toLowerCase().includes(search))));
}

/** Winner cell text plus a data-winner value the stylesheet colours by. */
function winnerOf(g: GameSummary, showWinners: boolean): [text: string, kind: string] {
  if (g.live) return ["playing", "live"];
  if (!showWinners) return ["hidden", "hidden"];
  if (g.winner === undefined) return ["n/a", "unknown"];
  return g.winner === null ? ["none", "none"] : [g.winner, g.winner];
}

export function GameList({ games, href, showWinners = false }: ListProps) {
  if (games.length === 0) return <p className="games-status">No games in this index.</p>;
  return (
    <table className="games-table">
      <caption className="visually-hidden">Games</caption>
      <thead>
        <tr>
          <th scope="col">Game</th>
          <th scope="col">Models</th>
          <th scope="col">Days</th>
          <th scope="col">Winner</th>
        </tr>
      </thead>
      <tbody>
        {games.map((g) => {
          const [winner, kind] = winnerOf(g, showWinners);
          return (
            <tr key={g.id}>
              <th scope="row">
                <a href={href(g.id)}>{g.id}</a>
                {g.live && <span className="live-badge"> ● Live</span>}
              </th>
              <td>
                {g.models?.length ? (
                  <details className="games-lineup"><summary>{g.models.length} {g.models.length === 1 ? 'model' : 'models'} · {g.models.slice(0, 2).map(modelName).join(', ')}{g.models.length > 2 ? '…' : ''}</summary><ul className="games-models">
                    {g.models.map((m) => (
                      <li key={m} title={m}>
                        <ModelAvatar model={m} label={modelName(m)} size={20} />
                        {modelName(m)}
                      </li>
                    ))}
                  </ul></details>
                ) : (
                  "n/a"
                )}
              </td>
              <td>{g.days ?? "—"}</td>
              <td>
                <span className="games-winner" data-winner={kind}>
                  {winner}
                </span>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

/** Message for a failed index load; the raw error stays in `detail` (the tooltip). */
export function indexError(err: Error): { text: string; detail: string } {
  const text = err instanceof MalformedIndexError ? "The game index is not in the expected format." : `Could not load the game index: ${err.message}`;
  return { text, detail: err.message };
}

/** Lists the games a source knows about; each links into the replay. */
export default function IndexPage({ source, href }: { source: GameSource; href: (id: string) => string }) {
  const [games, setGames] = useState<GameSummary[] | null>(null);
  const [error, setError] = useState<{ text: string; detail: string } | null>(null);
  const [showWinners, setShowWinners] = useState(false);
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('all');
  const visible = games ? filterGames(games, query, status) : [];

  useEffect(() => {
    source.listGames().then(setGames, (err: Error) => {
      console.error(err);
      setError(indexError(err));
    });
  }, [source]);

  return (
    <section className="games-page" aria-labelledby="games-heading">
      <header className="games-head">
        <h2 id="games-heading">{games ? gamesCount(games) : "Games"}</h2>
        {games && games.length > 0 && (
          <label>
            <input type="checkbox" checked={showWinners} onChange={(e) => setShowWinners(e.target.checked)} /> Show
            winners (spoilers)
          </label>
        )}
      </header>
      {games && <div className="games-filters">
        <label>Search games or models<input type="search" value={query} onChange={e => setQuery(e.target.value)} placeholder="Game ID or model name" /></label>
        <label>Status<select value={status} onChange={e => setStatus(e.target.value)}><option value="all">All games</option><option value="live">Live games</option><option value="recorded">Recorded games</option></select></label>
        <p role="status">{visible.length} of {games.length} games</p>
        {(query || status !== 'all') && <button onClick={() => { setQuery(''); setStatus('all'); }}>Clear filters</button>}
      </div>}
      {error ? (
        <p role="alert" title={error.detail}>{error.text}</p>
      ) : !games ? (
        <p className="games-status">Loading games...</p>
      ) : (
        visible.length ? <div className="games-scroll"><GameList games={visible} href={href} showWinners={showWinners} /></div> : <p className="games-status">{games.length ? 'No games match these filters.' : 'No games in this index.'}</p>
      )}
    </section>
  );
}
