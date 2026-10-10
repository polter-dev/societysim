import { Fragment, useEffect, useState } from "react";
import { DECEPTION_SOURCES, DEFAULT_MIN_N, belowThreshold } from "../../data/deception";
import { type ModelProfile as Profile, type PairRow, avatarFor, loadModelProfile } from "../../data/models";

const pct = (x: number | null | undefined) => (x == null ? "n/a" : `${Math.round(x * 100)}%`);
const num = (x: number | null | undefined) => (x == null ? "n/a" : x.toFixed(2));
const secs = (ms: number | null) => (ms == null ? "n/a" : `${(ms / 1000).toFixed(1)} s`);

function Pairs({ rows, other, caption, minN }: { rows: PairRow[]; other: string; caption: string; minN: number }) {
  if (rows.length === 0) return <p>{caption}: none.</p>;
  return (
    <table>
      <caption>{caption}</caption>
      <thead>
        <tr>
          <th scope="col">{other}</th>
          <th scope="col">Voted as the lie implied (95% CI)</th>
          <th scope="col">Votes</th>
          <th scope="col">Listener baseline</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.model} data-below-threshold={belowThreshold(r, minN) || undefined}>
            <th scope="row">{r.model}</th>
            <td>
              {pct(r.vote_follow_rate)}
              {r.vote_follow_ci && ` (${pct(r.vote_follow_ci[0])} to ${pct(r.vote_follow_ci[1])})`}
            </td>
            <td>{r.voters}</td>
            <td>{pct(r.baseline_rate)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export interface ModelProfileProps {
  profile: Profile;
  /** Pair rows with fewer votes than this are marked with data-below-threshold. */
  minN?: number;
}

/** Unstyled model profile. Style via the class names and data attributes. */
export function ModelProfile({ profile: p, minN = DEFAULT_MIN_N }: ModelProfileProps) {
  const avatar = avatarFor(p.model);
  return (
    <article className="model-profile" data-model={p.model}>
      <header>
        {avatar ? (
          <img className="model-avatar" src={avatar} alt="" />
        ) : (
          <span className="model-avatar" data-placeholder="true" aria-hidden="true">
            {p.model.charAt(0).toUpperCase()}
          </span>
        )}
        <h2>{p.model}</h2>
      </header>
      <dl>
        <dt>Games</dt>
        <dd>
          {p.games ?? "n/a"} ({p.seats} {p.seats === 1 ? "seat" : "seats"})
        </dd>
        <dt>Win rate (95% CI)</dt>
        <dd>
          {pct(p.win_rate)}
          {p.win_ci && ` (${pct(p.win_ci[0])} to ${pct(p.win_ci[1])})`}
        </dd>
        <dt>Wolf win rate</dt>
        <dd>{pct(p.wolf_win_rate)}</dd>
        <dt>Village win rate</dt>
        <dd>{pct(p.village_win_rate)}</dd>
        <dt>OpenSkill ordinal</dt>
        <dd>{num(p.openskill?.ordinal)}</dd>
        {DECEPTION_SOURCES.map((s) => (
          <Fragment key={s}>
            <dt>Lie rate ({s})</dt>
            <dd>{pct(p.lie_rate[s])}</dd>
          </Fragment>
        ))}
        <dt>Statements</dt>
        <dd>{p.statements}</dd>
        <dt>Detection AUC (village seats)</dt>
        <dd>
          {num(p.detection.auc)} (Brier {num(p.detection.brier)}, n={p.detection.n})
        </dd>
        <dt>Median latency</dt>
        <dd>{secs(p.median_latency_ms)}</dd>
        <dt>Narration voice</dt>
        <dd data-voice-id={p.voice.voice_id}>
          {p.voice.voice_id}
          {p.voice.fallback && " (default voice)"}
        </dd>
      </dl>
      <section className="model-deception" data-exploratory="true">
        <h3>Who it fools, who fools it (exploratory)</h3>
        <p>
          How often a listener's next vote went the way a lie implied, next to that listener's rate after truthful
          claims. Rows under {minN} votes are thin.
        </p>
        {DECEPTION_SOURCES.map((s) => (
          <div key={s} data-source={s}>
            <h4>{s}</h4>
            <Pairs rows={p.fools[s]} other="Listener" caption={`Who ${p.model} fools (${s})`} minN={minN} />
            <Pairs rows={p.fooled_by[s]} other="Liar" caption={`Who fools ${p.model} (${s})`} minN={minN} />
          </div>
        ))}
      </section>
    </article>
  );
}

/** Loads metrics.json and shows one model's profile. */
export default function ModelProfilePage({ metricsUrl, model }: { metricsUrl: string; model: string }) {
  const [state, setState] = useState<{ profile?: Profile | null; error?: string }>({});

  useEffect(() => {
    loadModelProfile(metricsUrl, model).then(
      (profile) => setState({ profile }),
      (err: Error) => setState({ error: err.message }),
    );
  }, [metricsUrl, model]);

  if (state.error) return <p role="alert">Could not load the profile: {state.error}</p>;
  if (state.profile === undefined) return <p>Loading profile...</p>;
  if (state.profile === null) return <p>No profile for {model} in this batch.</p>;
  return <ModelProfile profile={state.profile} />;
}
