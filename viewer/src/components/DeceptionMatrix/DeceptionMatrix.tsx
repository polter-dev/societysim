import { useState } from "react";
import {
  DECEPTION_SOURCES,
  DEFAULT_MIN_N,
  belowThreshold,
  cell,
  sortAxes,
  type DeceptionMatrix as Matrix,
  type DeceptionSource,
} from "../../data/deception";

export interface DeceptionMatrixProps {
  matrix: Matrix;
  /** Cells with fewer voters than this are greyed. */
  minN?: number;
  initialSource?: DeceptionSource;
}

const pct = (x: number) => `${(x * 100).toFixed(0)}%`;

/** Unstyled liar (rows) x listener (columns) heatmap table. Style via the data attributes. */
export function DeceptionMatrix({ matrix, minN = DEFAULT_MIN_N, initialSource = "self_tag" }: DeceptionMatrixProps) {
  const [source, setSource] = useState<DeceptionSource>(initialSource);
  const { liars, listeners } = sortAxes(matrix, source);

  return (
    <section className="deception-matrix" data-exploratory={matrix.exploratory}>
      <div role="group" aria-label="Label source">
        {DECEPTION_SOURCES.map((s) => (
          <button key={s} type="button" aria-pressed={s === source} onClick={() => setSource(s)}>
            {s}
          </button>
        ))}
      </div>
      {liars.length === 0 ? (
        <p>No lies heard for this label source.</p>
      ) : (
        <table>
          <caption>
            Vote-follow rate: how often a listener voted the way a lie implied (n = voters, greyed under {minN}).
            {matrix.exploratory ? " Exploratory." : ""}
          </caption>
          <thead>
            <tr>
              <th scope="col">Liar \ Listener</th>
              {listeners.map((l) => (
                <th key={l} scope="col">
                  {l}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {liars.map((liar) => (
              <tr key={liar}>
                <th scope="row">{liar}</th>
                {listeners.map((listener) => {
                  const c = cell(matrix, source, liar, listener);
                  if (!c) return <td key={listener} data-empty="true" />;
                  const grey = belowThreshold(c, minN);
                  const ci = c.vote_follow_ci ? `${pct(c.vote_follow_ci[0])} to ${pct(c.vote_follow_ci[1])}` : "n/a";
                  const base = c.baseline_rate === null ? "n/a" : pct(c.baseline_rate);
                  return (
                    <td
                      key={listener}
                      className={grey ? "below-threshold" : undefined}
                      data-below-threshold={grey}
                      data-rate={c.vote_follow_rate ?? undefined}
                      title={`95% CI ${ci}; baseline ${base}`}
                      style={grey ? { opacity: 0.4 } : undefined}
                    >
                      {c.vote_follow_rate === null ? "n/a" : pct(c.vote_follow_rate)} (n={c.voters})
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
