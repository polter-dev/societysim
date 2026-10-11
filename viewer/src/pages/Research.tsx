import { useEffect, useId, useState, type KeyboardEvent } from "react";
import { DECEPTION_SOURCES, DEFAULT_MIN_N, parseDeceptionMatrix, type Ci, type DeceptionSource, type DeceptionMatrix as Matrix } from "../data/deception";
import { modelName } from "../data/modelNames";
import { hasLies, parseLieTable, parseRoleSkill, sortRows, type LieTable, type RoleSkillTable, type SkillRow } from "../data/researchTabs";
import { fetchJson } from "../data/staticSource";
import { DeceptionMatrix } from "../components/DeceptionMatrix/DeceptionMatrix";
import "./Leaderboard.css"; // the tabs and tables below reuse its global-leaderboard__* classes

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

const TABS = [
  { id: "matrix", label: "Who fools whom" },
  { id: "lies", label: "Lie success" },
  { id: "skill", label: "Doctor & Seer" },
] as const;
export type ResearchTab = (typeof TABS)[number]["id"];

const SOURCE_LABELS: Record<DeceptionSource, string> = { self_tag: "Self-tagged", jev: "Jev" };
const ci = (c: Ci | null) => (c ? `${pct(c[0])} to ${pct(c[1])}` : "n/a");
/** A rate with the number it is over, like the matrix cells. */
const rateN = (rate: number | null, n: number) => `${pct(rate)} (n=${n})`;

function MatrixPanel({ matrix, matrixError }: { matrix: Matrix | null; matrixError?: string }) {
  // The box holds what was typed (it can be empty mid-edit); the matrix keeps the last whole number >= 1.
  const [text, setText] = useState(String(DEFAULT_MIN_N));
  const [minN, setMinN] = useState(DEFAULT_MIN_N);
  return (
    <>
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

function Table({ caption, head, rows }: { caption: string; head: string[]; rows: string[][] }) {
  return (
    <div className="global-leaderboard__scroll">
      <table className="global-leaderboard__table">
        <caption>{caption}</caption>
        <thead>
          <tr>
            {head.map((h) => (
              <th key={h} scope="col">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r[0]}>
              {r.map((c, i) =>
                i === 0 ? (
                  <th key={i} scope="row">
                    {c}
                  </th>
                ) : (
                  <td key={i}>{c}</td>
                ),
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function LiesPanel({ lies }: { lies: LieTable }) {
  const hasJev = hasLies(lies, "jev");
  const [picked, setPicked] = useState<DeceptionSource>("self_tag");
  const source = hasJev ? picked : "self_tag";
  const rows = sortRows(
    lies[source],
    (r) => r.comparisons,
    (r) => r.share_moved,
  );
  return (
    <>
      <h2>Lie success</h2>
      {hasJev ? (
        <div role="group" aria-label="Label source" className="deception-sources">
          {DECEPTION_SOURCES.map((s) => (
            <button key={s} type="button" className="deception-sources__option" aria-pressed={s === source} onClick={() => setPicked(s)}>
              {SOURCE_LABELS[s]}
            </button>
          ))}
        </div>
      ) : (
        <p className="deception-note">Lie labels in this batch are self-tags. Jev labels haven&apos;t been run on it yet.</p>
      )}
      <Table
        caption={`Per model, over every seat it played. Exploratory: this reads correlation, not cause. Models with fewer than ${DEFAULT_MIN_N} suspicion comparisons are listed last.`}
        head={["Model", "Lies", "Lie rate", "Suspicion moved", "Votes followed", "Vote base rate"]}
        rows={rows.map((r) => [
          modelName(r.model),
          String(r.lies),
          pct(r.lie_rate),
          rateN(r.share_moved, r.comparisons),
          rateN(r.share_followed, r.voters),
          rateN(r.base_rate, r.base_voters),
        ])}
      />
    </>
  );
}

function SkillTable({ role, rows }: { role: "doctor" | "seer"; rows: SkillRow[] }) {
  const doctor = role === "doctor";
  if (rows.length === 0) return <p>No {doctor ? "Doctor" : "Seer"} decisions in this batch.</p>;
  const sorted = sortRows(
    rows,
    (r) => r.n,
    (r) => r.rate,
  );
  return (
    <Table
      caption={`${doctor ? "A protect that stopped the night kill" : "A check that landed on a wolf"}, against the rate a random pick would have had. Models with fewer than ${DEFAULT_MIN_N} ${doctor ? "nights" : "checks"} are listed last.`}
      head={["Model", doctor ? "Nights" : "Checks", doctor ? "Saves" : "Wolves found", ...(doctor ? ["Self-protects"] : []), doctor ? "Save rate" : "Hit rate", "95% CI", "Chance"]}
      rows={sorted.map((r) => [modelName(r.model), String(r.n), String(r.hits), ...(doctor ? [String(r.selfProtects)] : []), rateN(r.rate, r.n), ci(r.ci), pct(r.chance)])}
    />
  );
}

function SkillPanel({ skill }: { skill: RoleSkillTable }) {
  return (
    <>
      <h2>Doctor &amp; Seer</h2>
      <p>
        Chance is what a random pick would have scored: one over the living players for the Doctor, living wolves over living others for the Seer. It ignores what the player knew, so read it as a
        yardstick. Engine fallback picks are not counted.
      </p>
      <h3>Doctor</h3>
      <SkillTable role="doctor" rows={skill.doctor} />
      <h3>Seer</h3>
      <SkillTable role="seer" rows={skill.seer} />
    </>
  );
}

export interface ResearchViewProps {
  summary: Summary | null;
  /** Null when the batch has no deception matrix; `matrixError` says why a present one failed to parse. */
  matrix: Matrix | null;
  matrixError?: string;
  /** Null (or left out) hides that tab: an older metrics.json has no such data. */
  lies?: LieTable | null;
  roleSkill?: RoleSkillTable | null;
  initialTab?: ResearchTab;
}

export function ResearchView({ summary, matrix, matrixError, lies = null, roleSkill = null, initialTab = "matrix" }: ResearchViewProps) {
  const tabs = TABS.filter((t) => t.id === "matrix" || (t.id === "lies" ? lies : roleSkill));
  const [picked, setPicked] = useState<ResearchTab>(initialTab);
  const tab = tabs.some((t) => t.id === picked) ? picked : "matrix";
  const id = useId();
  const tabId = (t: ResearchTab) => `${id}-${t}`;
  // WAI-ARIA tabs: one tab stop; the arrow keys, Home and End move and select.
  const onKeyDown = (e: KeyboardEvent) => {
    const i = tabs.findIndex((t) => t.id === tab);
    const n = tabs.length;
    const next = ({ ArrowRight: (i + 1) % n, ArrowLeft: (i - 1 + n) % n, Home: 0, End: n - 1 } as Record<string, number>)[e.key];
    if (next === undefined) return;
    e.preventDefault();
    setPicked(tabs[next].id);
    document.getElementById(tabId(tabs[next].id))?.focus();
  };
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
      <div role="tablist" aria-label="Research" className="global-leaderboard__tablist" onKeyDown={onKeyDown}>
        {tabs.map((t) => (
          <button
            key={t.id}
            id={tabId(t.id)}
            type="button"
            role="tab"
            className="global-leaderboard__tab"
            aria-selected={t.id === tab}
            aria-controls={`${id}-panel`}
            tabIndex={t.id === tab ? 0 : -1}
            onClick={() => setPicked(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div role="tabpanel" id={`${id}-panel`} aria-labelledby={tabId(tab)}>
        {tab === "matrix" && <MatrixPanel matrix={matrix} matrixError={matrixError} />}
        {tab === "lies" && lies && <LiesPanel lies={lies} />}
        {tab === "skill" && roleSkill && <SkillPanel skill={roleSkill} />}
      </div>
    </>
  );
}

/** Loads metrics.json and shows the headlines and the tabs. */
export default function ResearchPage({ metricsUrl }: { metricsUrl: string }) {
  const [data, setData] = useState<ResearchViewProps | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    fetchJson<{ summary?: Summary; deception_matrix?: unknown; models?: unknown; role_skill?: unknown }>(metricsUrl).then(
      (m) => {
        const extra = { summary: m.summary ?? null, lies: parseLieTable(m.models), roleSkill: parseRoleSkill(m.role_skill) };
        if (m.deception_matrix == null) return setData({ ...extra, matrix: null });
        try {
          setData({ ...extra, matrix: parseDeceptionMatrix(m.deception_matrix) });
        } catch (err) {
          setData({ ...extra, matrix: null, matrixError: err instanceof Error ? err.message : String(err) });
        }
      },
      (err: Error) => setError(err.message),
    );
  }, [metricsUrl]);
  if (error) return <p role="alert">Could not load the research report: {error}</p>;
  if (!data) return <p>Loading research report...</p>;
  return <ResearchView {...data} />;
}
