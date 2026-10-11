import { useState, type CSSProperties } from "react";
import {
  DECEPTION_SOURCES,
  DEFAULT_MIN_N,
  belowThreshold,
  cell,
  sortAxes,
  type DeceptionMatrix as Matrix,
  type DeceptionSource,
} from "../../data/deception";
import { modelName } from "../../data/modelNames";
import { ModelAvatar, useModelLabel } from "../ModelAvatar/ModelAvatar";

export interface DeceptionMatrixProps {
  matrix: Matrix;
  /** Cells with fewer voters than this are greyed. */
  minN?: number;
  initialSource?: DeceptionSource;
}

const pct = (x: number) => `${(x * 100).toFixed(0)}%`;
const SOURCE_LABELS: Record<DeceptionSource, string> = { self_tag: "Self-tagged", jev: "Jev" };

/** Liar (rows) x listener (columns) heatmap table, shaded by vote-follow rate (styled in controls.css). */
export function DeceptionMatrix({ matrix, minN = DEFAULT_MIN_N, initialSource = "self_tag" }: DeceptionMatrixProps) {
  const [source, setSource] = useState<DeceptionSource>(initialSource);
  const { liars, listeners } = sortAxes(matrix, source);
  const label = useModelLabel();
  const model = (m: string, size: number) => (
    <span className="deception-model" title={label(m)}>
      <ModelAvatar model={m} label={modelName(m)} size={size} />
      <span className="deception-name">{modelName(m)}</span>
    </span>
  );

  return (
    <section className="deception-matrix" data-exploratory={matrix.exploratory}>
      <div role="group" aria-label="Label source" className="deception-sources">
        {DECEPTION_SOURCES.map((s) => (
          <button key={s} type="button" className="deception-sources__option" aria-pressed={s === source} onClick={() => setSource(s)}>
            {SOURCE_LABELS[s]}
          </button>
        ))}
      </div>
      {liars.length === 0 ? (
        <p>No lies heard for this label source.</p>
      ) : (
        <div className="deception-scroll">
        <table>
          <caption>
            Vote-follow rate: how often a listener voted the way a lie implied (n = voters; cells under {minN} are hatched and unshaded).
            {matrix.exploratory ? " Exploratory." : ""}
          </caption>
          <thead>
            <tr>
              <th scope="col">Liar \ Listener</th>
              {listeners.map((l) => (
                <th key={l} scope="col">
                  {model(l, 32)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {liars.map((liar) => (
              <tr key={liar}>
                <th scope="row">{model(liar, 28)}</th>
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
                      style={grey || c.vote_follow_rate === null ? undefined : ({ "--rate": c.vote_follow_rate } as CSSProperties)}
                    >
                      {c.vote_follow_rate === null ? "n/a" : pct(c.vote_follow_rate)} <small>(n={c.voters})</small>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
        </div>
      )}
    </section>
  );
}
