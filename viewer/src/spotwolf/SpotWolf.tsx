import type { Event } from "../types/events";
import { useSpotWolf, type SpotWolfView } from "./useSpotWolf";

/**
 * Unstyled and presentational: renders a SpotWolfView, reports toggles. Plain elements and aria only,
 * so it can be restyled without touching the logic. Before the reveal it has no role information at all.
 */
export function SpotWolf({ view, onToggle }: { view: SpotWolfView; onToggle: (seat: string) => void }) {
  const { results } = view;
  return (
    <section aria-label="Suspect marks" className="spotwolf">
      <h2>Spot the wolf</h2>
      {!results && (
        <>
          <p>
            Mark who you suspect ({view.seats.filter((s) => s.marked).length} of {view.maxMarks}). Your marks lock when
            the game ends.
          </p>
          <ul className="spotwolf-seats">
            {view.seats.map((s) => (
              <li key={s.id}>
                <button
                  type="button"
                  aria-pressed={s.marked}
                  disabled={view.locked}
                  onClick={() => onToggle(s.id)}
                >
                  {s.name}
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
      {results && (
        <div className="spotwolf-results" role="status">
          <p>
            You scored {results.score.points} of {results.score.maxPoints}: {results.score.hits} found,{" "}
            {results.score.misses} wrong.
          </p>
          <p>Wolves: {results.wolves.map((w) => w.name).join(", ")}</p>
          <table>
            <caption>How the models did</caption>
            <thead>
              <tr>
                <th scope="col">Player</th>
                <th scope="col">Model</th>
                <th scope="col">Avg suspicion of wolves</th>
                <th scope="col">Avg suspicion of others</th>
                <th scope="col">Top picks</th>
                <th scope="col">Score</th>
              </tr>
            </thead>
            <tbody>
              {results.models.map((m) => (
                <tr key={m.seat}>
                  <th scope="row">{m.name}</th>
                  <td>{m.model}</td>
                  <td>{m.meanWolfP.toFixed(2)}</td>
                  <td>{m.meanInnocentP.toFixed(2)}</td>
                  <td>{m.topPicks.join(", ")}</td>
                  <td>
                    {m.score.points} of {m.score.maxPoints}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

/** Hook plus component, for dropping into the replay. Mount with key={gameId}. */
export function SpotWolfPanel({ events, idx }: { events: Event[]; idx: number }) {
  const { view, toggle } = useSpotWolf(events, idx);
  return <SpotWolf view={view} onToggle={toggle} />;
}
