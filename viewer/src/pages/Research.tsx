import { useEffect, useState } from "react";
import { DEFAULT_MIN_N, parseDeceptionMatrix, type DeceptionMatrix as Matrix } from "../data/deception";
import { fetchJson } from "../data/staticSource";
import { DeceptionMatrix } from "../components/DeceptionMatrix/DeceptionMatrix";

/** The part of metrics.json `summary` the headlines read. */
export interface Summary {
  games: number;
  unfinished_games: number;
  cost_usd: number;
  wolf_win_share: number | null;
  village_win_share: number | null;
  top_openskill: { model: string; ordinal: number } | null;
  top_wolf_win: { model: string; win_rate: number; seats: number } | null;
}

const pct = (x: number | null) => (x == null ? "n/a" : `${Math.round(x * 100)}%`);

/** [label, value] pairs for the report headline strip. */
export function headlines(s: Summary): [string, string][] {
  return [
    ["Games", `${s.games}${s.unfinished_games ? ` (${s.unfinished_games} unfinished)` : ""}`],
    ["Wolf wins", pct(s.wolf_win_share)],
    ["Village wins", pct(s.village_win_share)],
    ["Top OpenSkill", s.top_openskill ? `${s.top_openskill.model} (${s.top_openskill.ordinal.toFixed(2)})` : "n/a"],
    ["Best wolf", s.top_wolf_win ? `${s.top_wolf_win.model} (${pct(s.top_wolf_win.win_rate)})` : "n/a"],
    ["Cost", `$${s.cost_usd.toFixed(2)}`],
  ];
}

export function ResearchView({ summary, matrix }: { summary: Summary | null; matrix: Matrix }) {
  const [minN, setMinN] = useState(DEFAULT_MIN_N);
  return (
    <>
      {summary && (
        <dl className="headlines">
          {headlines(summary).map(([k, v]) => (
            <div key={k}>
              <dt>{k}</dt>
              <dd>{v}</dd>
            </div>
          ))}
        </dl>
      )}
      <h2>Who fools whom</h2>
      <label>
        Minimum voters per cell{" "}
        <input type="number" min={1} value={minN} onChange={(e) => setMinN(Math.max(1, Math.trunc(Number(e.target.value)) || 1))} />
      </label>
      <DeceptionMatrix matrix={matrix} minN={minN} />
    </>
  );
}

/** Loads metrics.json and shows the headlines and the deception matrix. */
export default function ResearchPage({ metricsUrl }: { metricsUrl: string }) {
  const [data, setData] = useState<{ summary: Summary | null; matrix: Matrix } | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    fetchJson<{ summary?: Summary; deception_matrix?: unknown }>(metricsUrl).then(
      (m) => {
        try {
          setData({ summary: m.summary ?? null, matrix: parseDeceptionMatrix(m.deception_matrix) });
        } catch (err) {
          setError(err instanceof Error ? err.message : String(err));
        }
      },
      (err: Error) => setError(err.message),
    );
  }, [metricsUrl]);
  if (error) return <p role="alert">Could not load the research report: {error}</p>;
  if (!data) return <p>Loading research report...</p>;
  return <ResearchView {...data} />;
}
