import { useEffect, useState } from "react";
import type { GameSource, GameSummary } from "../data/source";

interface ListProps {
  games: GameSummary[];
  href: (id: string) => string;
  /** Winners are spoilers; they stay hidden until the viewer asks. */
  showWinners?: boolean;
}

export function GameList({ games, href, showWinners = false }: ListProps) {
  if (games.length === 0) return <p>No games in this index.</p>;
  return (
    <table>
      <caption>Games</caption>
      <thead>
        <tr>
          <th scope="col">Game</th>
          <th scope="col">Models</th>
          <th scope="col">Days</th>
          <th scope="col">Winner</th>
        </tr>
      </thead>
      <tbody>
        {games.map((g) => (
          <tr key={g.id}>
            <th scope="row">
              <a href={href(g.id)}>{g.id}</a>
              {g.live && <span className="live-badge"> ● Live</span>}
            </th>
            <td>{g.models?.join(", ") ?? "?"}</td>
            <td>{g.days ?? "n/a"}</td>
            <td>{g.live ? "playing" : showWinners ? (g.winner === undefined ? "n/a" : (g.winner ?? "none")) : "hidden"}</td>
          </tr>
        ))}
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

  if (error) return <p role="alert">Could not load the game index: {error}</p>;
  if (!games) return <p>Loading games...</p>;
  return (
    <>
      <label>
        <input type="checkbox" checked={showWinners} onChange={(e) => setShowWinners(e.target.checked)} /> Show winners
        (spoilers)
      </label>
      <GameList games={games} href={href} showWinners={showWinners} />
    </>
  );
}
