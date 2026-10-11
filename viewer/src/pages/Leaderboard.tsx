import { useEffect, useState } from "react";
import { GlobalLeaderboard } from "../components/GlobalLeaderboard/GlobalLeaderboard";
import { useGlobalBoard } from "../data/globalBoard";
import { fetchJson } from "../data/staticSource";

/** leaderboard.json from `societysim report` (shape documented in analysis/report.py). */
export interface Rating {
  mu: number;
  sigma: number;
  ordinal: number;
}

export interface BoardRow {
  model: string;
  /** Distinct games; null from Neon, whose view counts seats only. */
  games: number | null;
  /** Seats filled, summed over games. */
  seats?: number;
  win_rate: number | null;
  win_ci: [number, number] | null;
  wolf_win_rate: number | null;
  village_win_rate: number | null;
  openskill: Rating | null;
  detection_auc: number | null;
  detection_brier: number | null;
  invalid_output_rate: number | null;
  cost_usd_per_game: number | null;
  lie_rate: Record<string, number | null>;
}

export interface Board {
  schema: number;
  games: number;
  label_sources: string[];
  wolf_side_advantage: { overall: Rating } | null;
  models: BoardRow[];
}

/** The part of metrics.json this page reads: OpenSkill per side, `{model: rating}`. */
export interface BySide {
  wolf?: Record<string, Rating>;
  village?: Record<string, Rating>;
}

const pct = (x: number | null | undefined) => (x == null ? "n/a" : `${Math.round(x * 100)}%`);
const num = (x: number | null | undefined) => (x == null ? "n/a" : x.toFixed(2));

/** `profileHref(model)` links each model name to its profile page; omit it for plain names. */
export function LeaderboardTable({
  board,
  bySide = {},
  profileHref,
}: {
  board: Board;
  bySide?: BySide;
  profileHref?: (model: string) => string;
}) {
  const side = board.wolf_side_advantage?.overall;
  return (
    <>
      <p>
        {board.games} games.{" "}
        {board.models.some((r) => r.openskill)
          ? "Ranked by OpenSkill ordinal (mu minus three sigma). Win rates carry a Wilson 95% interval; small batches are noisy."
          : "Ranked by win rate over all finished games; small samples are noisy."}
        {side && ` Wolf-side advantage: mu ${num(side.mu)} (0 means the sides are even).`}
      </p>
      <table>
        <caption>Leaderboard</caption>
        <thead>
          <tr>
            <th scope="col">Rank</th>
            <th scope="col">Model</th>
            <th scope="col">Games</th>
            <th scope="col">Seats</th>
            <th scope="col">Win rate (95% CI)</th>
            <th scope="col">Wolf win</th>
            <th scope="col">Village win</th>
            <th scope="col">OpenSkill</th>
            <th scope="col">OpenSkill as wolf</th>
            <th scope="col">OpenSkill as village</th>
            <th scope="col">Detection AUC</th>
          </tr>
        </thead>
        <tbody>
          {board.models.map((r, i) => (
            <tr key={r.model}>
              <td>{i + 1}</td>
              <th scope="row">{profileHref ? <a href={profileHref(r.model)}>{r.model}</a> : r.model}</th>
              <td>{r.games ?? "n/a"}</td>
              <td>{r.seats ?? "n/a"}</td>
              <td>
                {pct(r.win_rate)}
                {r.win_ci && ` (${pct(r.win_ci[0])} to ${pct(r.win_ci[1])})`}
              </td>
              <td>{pct(r.wolf_win_rate)}</td>
              <td>{pct(r.village_win_rate)}</td>
              <td>{num(r.openskill?.ordinal)}</td>
              <td>{num(bySide.wolf?.[r.model]?.ordinal)}</td>
              <td>{num(bySide.village?.[r.model]?.ordinal)}</td>
              <td>{num(r.detection_auc)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

/**
 * Best detectors and best liars across every finished game, visitor games included (#344, #366).
 * Reads the Neon views global_detectors / global_liars, which only count finished games. With no
 * Neon vars there is no static copy of this board, so it says so and makes no request.
 */
export function GlobalSection({ enabled }: { enabled: boolean }) {
  return (
    <section aria-labelledby="global-rankings" className="global-rankings">
      <h2 id="global-rankings">Global rankings</h2>
      {enabled ? (
        <LiveGlobalBoard />
      ) : (
        <p>The global rankings need live data, which this build doesn't have. The model table above is from the published batch.</p>
      )}
    </section>
  );
}

// Separate component so the polling hook only runs when Neon is configured.
function LiveGlobalBoard() {
  const { board, error } = useGlobalBoard();
  return <GlobalLeaderboard board={board} error={error} />;
}

/**
 * Loads leaderboard.json, plus metrics.json for OpenSkill by side when it is there.
 * `loadBoard` (the Neon leaderboard view) is tried first; the static files are the fallback.
 */
export default function LeaderboardPage({
  leaderboardUrl,
  metricsUrl,
  loadBoard,
  profileHref,
  globalBoard = false,
}: {
  leaderboardUrl: string;
  metricsUrl: string;
  loadBoard?: () => Promise<Board>;
  profileHref?: (model: string) => string;
  /** True when the build has the Neon URLs; mounts the global detectors/liars board. */
  globalBoard?: boolean;
}) {
  const [board, setBoard] = useState<Board | null>(null);
  const [bySide, setBySide] = useState<BySide>({});
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fromFiles = () => {
      fetchJson<Board>(leaderboardUrl).then(setBoard, (err: Error) => setError(err.message));
      fetchJson<{ openskill?: BySide }>(metricsUrl).then(
        (m) => setBySide(m.openskill ?? {}),
        () => {}, // optional: the by-side columns show n/a
      );
    };
    if (loadBoard) loadBoard().then(setBoard, fromFiles);
    else fromFiles();
  }, [leaderboardUrl, metricsUrl, loadBoard]);

  return (
    <>
      {error ? (
        <p role="alert">Could not load the leaderboard: {error}</p>
      ) : !board ? (
        <p>Loading leaderboard...</p>
      ) : (
        <LeaderboardTable board={board} bySide={bySide} profileHref={profileHref} />
      )}
      <GlobalSection enabled={globalBoard} />
    </>
  );
}
