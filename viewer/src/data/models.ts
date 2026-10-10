// Model profile pages: metrics.json -> profiles (docs/DATA_CONTRACT.md, schema/metrics.schema.json).
import { DECEPTION_SOURCES, type DeceptionCell, type DeceptionSource } from "./deception";
import { fetchJson } from "./staticSource";

export interface ProfileRating {
  mu: number;
  sigma: number;
  ordinal: number;
}

/** One deception_matrix cell seen from one side; `model` is the other model. */
export interface PairRow extends DeceptionCell {
  model: string;
}

export interface ModelProfile {
  model: string;
  games: number | null;
  seats: number;
  win_rate: number | null;
  win_ci: [number, number] | null;
  wolf_win_rate: number | null;
  village_win_rate: number | null;
  openskill: ProfileRating | null;
  statements: number;
  lie_rate: Record<DeceptionSource, number | null>;
  detection: { n: number; auc: number | null; brier: number | null };
  median_latency_ms: number | null;
  voice: { voice_id: string; fallback: boolean };
  /** Listeners who heard this model lie, best fooled first. */
  fools: Record<DeceptionSource, PairRow[]>;
  /** Liars this model heard, most convincing first. */
  fooled_by: Record<DeceptionSource, PairRow[]>;
}

/**
 * Avatar hook: model name -> image URL. Empty until the avatar art is in the repo; import each
 * image here (Vite gives a URL) and the profile page shows it instead of the placeholder.
 */
const AVATARS: Record<string, string> = {};

export function avatarFor(model: string): string | null {
  return Object.hasOwn(AVATARS, model) ? AVATARS[model] : null;
}

function isObj(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function fail(path: string, why: string): never {
  throw new Error(`profiles.${path}: ${why}`);
}

/** Validate one profile's shape where the page depends on it. Throws an Error naming the bad path. */
export function parseProfile(raw: unknown, path = "profile"): ModelProfile {
  if (!isObj(raw)) fail(path, "expected object");
  if (typeof raw.model !== "string") fail(`${path}.model`, "expected string");
  if (!Number.isInteger(raw.seats)) fail(`${path}.seats`, "expected integer");
  if (!isObj(raw.lie_rate)) fail(`${path}.lie_rate`, "expected object");
  if (!isObj(raw.detection)) fail(`${path}.detection`, "expected object");
  if (!isObj(raw.voice) || typeof raw.voice.voice_id !== "string") fail(`${path}.voice`, "expected {voice_id}");
  for (const side of ["fools", "fooled_by"] as const) {
    const bySrc = raw[side];
    if (!isObj(bySrc)) fail(`${path}.${side}`, "expected object");
    for (const src of DECEPTION_SOURCES) {
      const rows = bySrc[src];
      if (!Array.isArray(rows)) fail(`${path}.${side}.${src}`, "expected list");
      rows.forEach((r, i) => {
        if (!isObj(r) || typeof r.model !== "string" || !Number.isInteger(r.voters)) {
          fail(`${path}.${side}.${src}[${i}]`, "expected {model, voters, ...}");
        }
      });
    }
  }
  return raw as unknown as ModelProfile;
}

/** The profile for `model` from a parsed metrics.json; null when the file has none for it. */
export function profileFromMetrics(metrics: unknown, model: string): ModelProfile | null {
  if (!isObj(metrics) || !isObj(metrics.profiles) || !Object.hasOwn(metrics.profiles, model)) return null;
  return parseProfile(metrics.profiles[model], model);
}

/** Fetch metrics.json and pick one model's profile. */
export async function loadModelProfile(
  metricsUrl: string,
  model: string,
  fetchMetrics: (url: string) => Promise<unknown> = fetchJson,
): Promise<ModelProfile | null> {
  return profileFromMetrics(await fetchMetrics(metricsUrl), model);
}
