// Pure readers for the Research page's "Lie success" and "Doctor & Seer" tabs
// (docs/DATA_CONTRACT.md: models[*].all.lies, role_skill). Each returns null when the data is
// missing or malformed (an older metrics.json), so the page can hide the tab.

import { DEFAULT_MIN_N, DECEPTION_SOURCES, type Ci, type DeceptionSource } from "./deception";

export interface LieRow {
  model: string;
  lies: number;
  lie_rate: number | null;
  /** Share of listeners whose suspicion of the liar moved the lie's way, over `comparisons`. */
  share_moved: number | null;
  comparisons: number;
  /** Share of voters who voted as the lie implied, over `voters`; `base_rate` is the same with no lie. */
  share_followed: number | null;
  voters: number;
  base_rate: number | null;
  base_voters: number;
}

export type LieTable = Record<DeceptionSource, LieRow[]>;

export interface SkillRow {
  model: string;
  /** Nights as doctor, or checks as seer. */
  n: number;
  /** Saves, or wolves found. */
  hits: number;
  /** Doctor only. */
  selfProtects: number | null;
  rate: number | null;
  ci: Ci | null;
  chance: number | null;
}

export interface RoleSkillTable {
  doctor: SkillRow[];
  seer: SkillRow[];
}

const obj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const int = (v: unknown): v is number => typeof v === "number" && Number.isInteger(v);
const numOrNull = (v: unknown): v is number | null => v === null || (typeof v === "number" && Number.isFinite(v));
const ciOrNull = (v: unknown): v is Ci | null => v === null || (Array.isArray(v) && v.length === 2 && v.every((x) => typeof x === "number"));

/** Rows with enough data first (best rate on top), thin ones last, ties by model name. */
export function sortRows<T extends { model: string }>(rows: T[], n: (r: T) => number, rate: (r: T) => number | null, minN = DEFAULT_MIN_N): T[] {
  const thin = (r: T) => n(r) < minN;
  return [...rows].sort((a, b) => Number(thin(a)) - Number(thin(b)) || (rate(b) ?? -1) - (rate(a) ?? -1) || a.model.localeCompare(b.model));
}

/** Per-model lie numbers for each label source, from `models[*].all.lies`. Null when no model has them. */
export function parseLieTable(models: unknown): LieTable | null {
  if (!obj(models)) return null;
  const out: LieTable = { self_tag: [], jev: [] };
  for (const [model, groups] of Object.entries(models)) {
    const lies = obj(groups) && obj(groups.all) && obj(groups.all.lies) ? groups.all.lies : null;
    if (!lies) continue;
    for (const src of DECEPTION_SOURCES) {
      const l = lies[src];
      const ls = obj(l) && obj(l.lie_success) ? l.lie_success : null;
      if (!obj(l) || !ls || !int(l.lies) || !numOrNull(l.lie_rate)) return null;
      if (![ls.comparisons, ls.voters, ls.base_voters].every(int) || ![ls.share_moved, ls.share_followed, ls.base_rate].every(numOrNull)) return null;
      out[src].push({
        model,
        lies: l.lies,
        lie_rate: l.lie_rate,
        share_moved: ls.share_moved as number | null,
        comparisons: ls.comparisons as number,
        share_followed: ls.share_followed as number | null,
        voters: ls.voters as number,
        base_rate: ls.base_rate as number | null,
        base_voters: ls.base_voters as number,
      });
    }
  }
  return out.self_tag.length ? out : null;
}

/** True when the source has any lie at all (a self-tags-only batch has none under Jev). */
export const hasLies = (t: LieTable, src: DeceptionSource) => t[src].some((r) => r.lies > 0 || r.lie_rate !== null);

function skillRows(raw: unknown, role: "doctor" | "seer"): SkillRow[] | null {
  const models = obj(raw) && obj(raw[role]) && obj(raw[role].models) ? raw[role].models : null;
  if (!models) return null;
  const [nKey, hitKey, rateKey, ciKey] = role === "doctor" ? (["nights", "saves", "save_rate", "save_ci"] as const) : (["checks", "wolves_found", "hit_rate", "hit_ci"] as const);
  const rows: SkillRow[] = [];
  for (const [model, s] of Object.entries(models)) {
    if (!obj(s) || !int(s[nKey]) || !int(s[hitKey]) || !numOrNull(s[rateKey]) || !ciOrNull(s[ciKey]) || !numOrNull(s.chance_rate)) return null;
    if (role === "doctor" && !int(s.self_protects)) return null;
    rows.push({
      model,
      n: s[nKey],
      hits: s[hitKey],
      selfProtects: role === "doctor" ? (s.self_protects as number) : null,
      rate: s[rateKey] as number | null,
      ci: s[ciKey] as Ci | null,
      chance: s.chance_rate as number | null,
    });
  }
  return rows;
}

/** Doctor and seer rows from `role_skill`. Null when the key is missing, malformed or has no decisions. */
export function parseRoleSkill(raw: unknown): RoleSkillTable | null {
  const doctor = skillRows(raw, "doctor");
  const seer = skillRows(raw, "seer");
  if (!doctor || !seer || (!doctor.length && !seer.length)) return null;
  return { doctor, seer };
}
