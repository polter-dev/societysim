import { useEffect, useState } from "react";
import { DEFAULT_MIN_N, parseDeceptionMatrix, type DeceptionMatrix as Matrix } from "../data/deception";
import { modelName } from "../data/modelNames";
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

const fin = (x: unknown): x is number => typeof x === "number" && Number.isFinite(x);
const pct = (x: number | null | undefined) => (fin(x) ? `${Math.round(x * 100)}%` : "n/a");
const fixed = (x: number | null | undefined) => (fin(x) ? x.toFixed(2) : "n/a");

/** [label, value] pairs for the report headline strip. */
export function headlines(s: Summary): [string, string][] {
  return [
    ["Games", `${fin(s.games) ? s.games : "n/a"}${fin(s.unfinished_games) && s.unfinished_games ? ` (${s.unfinished_games} unfinished)` : ""}`],
    ["Wolf wins", pct(s.wolf_win_share)],
    ["Village wins", pct(s.village_win_share)],
    ["Top OpenSkill", s.top_openskill ? `${modelName(s.top_openskill.model)} (${fixed(s.top_openskill.ordinal)})` : "n/a"],
    ["Best wolf", s.top_wolf_win ? `${modelName(s.top_wolf_win.model)} (${pct(s.top_wolf_win.win_rate)})` : "n/a"],
    ["Cost", fin(s.cost_usd) ? `$${s.cost_usd.toFixed(2)}` : "n/a"],
  ];
}

/** `matrix` is null when the batch has none; `matrixError` says why a present one failed to parse. */
export function ResearchView({ summary, matrix, matrixError }: { summary: Summary | null; matrix: Matrix | null; matrixError?: string }) {
  // The box holds what was typed (it can be empty mid-edit); the matrix keeps the last whole number >= 1.
  const [text, setText] = useState(String(DEFAULT_MIN_N));
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
      {matrixError ? (
        <p role="alert">Could not read the deception matrix: {matrixError}</p>
      ) : !matrix ? (
        <p>Matrix unavailable: no deception data in this batch.</p>
      ) : (
        <>
          <label>
            Minimum voters per cell{" "}
            <input
              type="number"
              min={1}
              step={1}
              value={text}
              onChange={(e) => {
                setText(e.target.value);
                const n = Number(e.target.value);
                if (e.target.value !== "" && Number.isInteger(n) && n >= 1) setMinN(n);
              }}
              onBlur={() => setText(String(minN))}
            />
          </label>
          <DeceptionMatrix matrix={matrix} minN={minN} />
        </>
      )}
    </>
  );
}

/** Loads metrics.json and shows the headlines and the deception matrix. */
export default function ResearchPage({ metricsUrl }: { metricsUrl: string }) {
  const [data, setData] = useState<{ summary: Summary | null; matrix: Matrix | null; matrixError?: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    fetchJson<{ summary?: Summary; deception_matrix?: unknown }>(metricsUrl).then(
      (m) => {
        const summary = m.summary ?? null;
        if (m.deception_matrix == null) return setData({ summary, matrix: null });
        try {
          setData({ summary, matrix: parseDeceptionMatrix(m.deception_matrix) });
        } catch (err) {
          setData({ summary, matrix: null, matrixError: err instanceof Error ? err.message : String(err) });
        }
      },
      (err: Error) => setError(err.message),
    );
  }, [metricsUrl]);
  if (error) return <p role="alert">Could not load the research report: {error}</p>;
  if (!data) return <p>Loading research report...</p>;
  return <ResearchView {...data} />;
}
