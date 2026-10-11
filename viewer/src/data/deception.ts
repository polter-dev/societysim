// Pure helpers for metrics.json -> deception_matrix (docs/DATA_CONTRACT.md, schema/metrics.schema.json).

export type DeceptionSource = "self_tag" | "jev";
export const DECEPTION_SOURCES: readonly DeceptionSource[] = ["self_tag", "jev"];

export type Ci = [number, number];

export interface DeceptionCell {
  heard: number;
  voters: number;
  followed: number;
  vote_follow_rate: number | null;
  vote_follow_ci: Ci | null;
  comparisons: number;
  moved: number;
  belief_shift_rate: number | null;
  belief_shift_ci: Ci | null;
  baseline_rate: number | null;
  baseline_voters: number;
}

export interface DeceptionBaseline {
  heard: number;
  voters: number;
  followed: number;
  vote_follow_rate: number | null;
  vote_follow_ci: Ci | null;
}

/** liar model -> listener model -> cell */
export type Pairs = Record<string, Record<string, DeceptionCell>>;

export interface DeceptionMatrix {
  exploratory: boolean;
  pairs: Record<DeceptionSource, Pairs>;
  baseline: Record<string, Record<string, DeceptionBaseline>>;
}

/** Default minimum voters for a cell to count as evidence. */
export const DEFAULT_MIN_N = 5;

const INT_FIELDS = ["heard", "voters", "followed", "comparisons", "moved", "baseline_voters"] as const;
const NUM_FIELDS = ["vote_follow_rate", "belief_shift_rate", "baseline_rate"] as const;
const CI_FIELDS = ["vote_follow_ci", "belief_shift_ci"] as const;

function isObj(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function fail(path: string, why: string): never {
  throw new Error(`deception_matrix.${path}: ${why}`);
}

function checkNumOrNull(v: unknown, path: string) {
  if (v !== null && (typeof v !== "number" || !Number.isFinite(v))) fail(path, "expected number or null");
}

function checkCi(v: unknown, path: string) {
  if (v === null) return;
  if (!Array.isArray(v) || v.length !== 2 || v.some((x) => typeof x !== "number")) {
    fail(path, "expected [lo, hi] or null");
  }
}

function checkCell(v: unknown, path: string) {
  if (!isObj(v)) fail(path, "expected object");
  for (const f of INT_FIELDS) {
    if (!Number.isInteger(v[f])) fail(`${path}.${f}`, "expected integer");
  }
  for (const f of NUM_FIELDS) checkNumOrNull(v[f], `${path}.${f}`);
  for (const f of CI_FIELDS) checkCi(v[f], `${path}.${f}`);
}

function checkBaseline(v: unknown, path: string) {
  if (!isObj(v)) fail(path, "expected object");
  for (const f of ["heard", "voters", "followed"]) {
    if (!Number.isInteger(v[f])) fail(`${path}.${f}`, "expected integer");
  }
  checkNumOrNull(v.vote_follow_rate, `${path}.vote_follow_rate`);
  checkCi(v.vote_follow_ci, `${path}.vote_follow_ci`);
}

/** Validate `metrics.deception_matrix`. Throws an Error naming the bad path. */
export function parseDeceptionMatrix(raw: unknown): DeceptionMatrix {
  if (!isObj(raw)) fail("", "expected object");
  if (typeof raw.exploratory !== "boolean") fail("exploratory", "expected boolean");
  if (!isObj(raw.pairs)) fail("pairs", "expected object");
  for (const src of DECEPTION_SOURCES) {
    const bySrc = raw.pairs[src];
    if (!isObj(bySrc)) fail(`pairs.${src}`, "expected object");
    for (const [liar, row] of Object.entries(bySrc)) {
      if (!isObj(row)) fail(`pairs.${src}.${liar}`, "expected object");
      for (const [listener, c] of Object.entries(row)) checkCell(c, `pairs.${src}.${liar}.${listener}`);
    }
  }
  if (!isObj(raw.baseline)) fail("baseline", "expected object");
  for (const [kind, byModel] of Object.entries(raw.baseline)) {
    if (!isObj(byModel)) fail(`baseline.${kind}`, "expected object");
    for (const [m, b] of Object.entries(byModel)) checkBaseline(b, `baseline.${kind}.${m}`);
  }
  return raw as unknown as DeceptionMatrix;
}

/** True when the batch has at least one lie heard under this label source. */
export function hasSource(m: DeceptionMatrix, source: DeceptionSource): boolean {
  return Object.values(m.pairs[source]).some((row) => Object.keys(row).length > 0);
}

export function cell(
  m: DeceptionMatrix,
  source: DeceptionSource,
  liar: string,
  listener: string,
): DeceptionCell | undefined {
  return m.pairs[source][liar]?.[listener];
}

/** True when the cell has too few voters (or no rate) to count. */
export function belowThreshold(c: DeceptionCell | undefined, minN: number): boolean {
  return !c || c.vote_follow_rate === null || c.voters < minN;
}

/** Sorted liar and listener model names that appear in the source. */
export function listModels(m: DeceptionMatrix, source: DeceptionSource) {
  const liars = Object.keys(m.pairs[source]).sort();
  const listeners = [...new Set(Object.values(m.pairs[source]).flatMap((r) => Object.keys(r)))].sort();
  return { liars, listeners };
}

export interface RankedPair {
  liar: string;
  listener: string;
  cell: DeceptionCell;
}

/** Pairs with at least `minN` voters, highest vote-follow rate first (ties: more voters, then names). */
export function rankPairs(m: DeceptionMatrix, source: DeceptionSource, minN = DEFAULT_MIN_N): RankedPair[] {
  const out: RankedPair[] = [];
  for (const [liar, row] of Object.entries(m.pairs[source])) {
    for (const [listener, c] of Object.entries(row)) {
      if (!belowThreshold(c, minN)) out.push({ liar, listener, cell: c });
    }
  }
  return out.sort(
    (a, b) =>
      (b.cell.vote_follow_rate as number) - (a.cell.vote_follow_rate as number) ||
      b.cell.voters - a.cell.voters ||
      a.liar.localeCompare(b.liar) ||
      a.listener.localeCompare(b.listener),
  );
}

/** Pooled followed/voters; null with no voters. */
function pooled(cells: DeceptionCell[]): number | null {
  const voters = cells.reduce((s, c) => s + c.voters, 0);
  return voters ? cells.reduce((s, c) => s + c.followed, 0) / voters : null;
}

function byRate(keys: string[], rate: (k: string) => number | null): string[] {
  return [...keys].sort((a, b) => {
    const ra = rate(a);
    const rb = rate(b);
    if (ra === rb) return a.localeCompare(b);
    if (ra === null) return 1;
    if (rb === null) return -1;
    return rb - ra || a.localeCompare(b);
  });
}

/**
 * Heatmap axes. Rows: best liars first (pooled follow rate over everyone who heard them).
 * Columns: most gullible listeners first. Models with no votes go last.
 */
export function sortAxes(m: DeceptionMatrix, source: DeceptionSource) {
  const { liars, listeners } = listModels(m, source);
  const p = m.pairs[source];
  return {
    liars: byRate(liars, (l) => pooled(Object.values(p[l] ?? {}))),
    listeners: byRate(listeners, (t) => pooled(liars.flatMap((l) => (p[l]?.[t] ? [p[l][t]] : [])))),
  };
}
