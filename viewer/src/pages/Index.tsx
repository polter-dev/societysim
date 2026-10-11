import { useEffect, useState } from "react";
import { ModelAvatar } from "../components/ModelAvatar/ModelAvatar";
import { modelName } from "../data/modelNames";
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
                  <ul className="games-models">
                    {g.models.map((m) => (
                      <li key={m} title={m}>
                        <ModelAvatar model={m} label={modelName(m)} size={20} />
                        {modelName(m)}
                      </li>
                    ))}
                  </ul>
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

/** Lists the games a source knows about; each links into the replay. */
export default function IndexPage({ source, href }: { source: GameSource; href: (id: string) => string }) {
  const [games, setGames] = useState<GameSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showWinners, setShowWinners] = useState(false);

  useEffect(() => {
    source.listGames().then(setGames, (err: Error) => setError(err.message));
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
      {error ? (
        <p role="alert">Could not load the game index: {error}</p>
      ) : !games ? (
        <p className="games-status">Loading games...</p>
      ) : (
        <GameList games={games} href={href} showWinners={showWinners} />
      )}
    </section>
  );
}
