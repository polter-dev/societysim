import { useEffect, useState } from "react";
import { fetchJson } from "../data/staticSource";

/** leaderboard.json from `societysim report` (shape documented in analysis/report.py). */
export interface Rating {
  mu: number;
  sigma: number;
  ordinal: number;
}

export interface BoardRow {
  model: string;
  games: number;
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

export function LeaderboardTable({ board, bySide = {} }: { board: Board; bySide?: BySide }) {
  const side = board.wolf_side_advantage?.overall;
  return (
    <>
      <p>
        {board.games} games. Ranked by OpenSkill ordinal (mu minus three sigma). Win rates carry a Wilson 95% interval;
        small batches are noisy.
        {side && ` Wolf-side advantage: mu ${num(side.mu)} (0 means the sides are even).`}
      </p>
      <table>
        <caption>Leaderboard</caption>
        <thead>
          <tr>
            <th scope="col">Rank</th>
            <th scope="col">Model</th>
            <th scope="col">Games</th>
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
              <th scope="row">{r.model}</th>
              <td>{r.games}</td>
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

/** Loads leaderboard.json, plus metrics.json for OpenSkill by side when it is there. */
export default function LeaderboardPage({ leaderboardUrl, metricsUrl }: { leaderboardUrl: string; metricsUrl: string }) {
  const [board, setBoard] = useState<Board | null>(null);
  const [bySide, setBySide] = useState<BySide>({});
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchJson<Board>(leaderboardUrl).then(setBoard, (err: Error) => setError(err.message));
    fetchJson<{ openskill?: BySide }>(metricsUrl).then(
      (m) => setBySide(m.openskill ?? {}),
      () => {}, // optional: the by-side columns show n/a
    );
  }, [leaderboardUrl, metricsUrl]);

  if (error) return <p role="alert">Could not load the leaderboard: {error}</p>;
  if (!board) return <p>Loading leaderboard...</p>;
  return <LeaderboardTable board={board} bySide={bySide} />;
}
