// Pure logic for the Basic/Advanced game setup form. No React, no I/O besides loadSetupAssets.
// Mirrors src/societysim/setup.py; the contract is schema/game_setup.schema.json + models_catalog.json.

export const HUMAN = "human";
export const MIN_PLAYERS = 7;
export const MAX_PLAYERS = 10;

export interface CatalogModel {
  name: string;
  display_name: string;
  provider: string;
  sponsor: string | null;
  price_tier: string;
  structured: string;
  structured_ok: boolean;
  color: string | null;
  available: boolean;
  unavailable_reason: string | null;
}
export interface ModelsCatalog {
  human: string;
  default_lineup: string[];
  models: CatalogModel[];
}
export type Schema = Record<string, any>;
export interface SetupAssets {
  catalog: ModelsCatalog;
  schema: Schema;
}

/** The setup JSON (GameSetup). */
export interface GameSetupJson {
  mode: "basic" | "advanced";
  seed: number;
  human_seat?: string | null;
  n_players?: number;
  n_wolves?: number;
  special_roles?: { seer: boolean; doctor: boolean };
  seats?: { seat: string; model: string }[];
}

export interface SetupState {
  mode: "basic" | "advanced";
  seed: number;
  /** Basic: the person plays p0. */
  basicHuman: boolean;
  nPlayers: number;
  nWolves: number;
  seer: boolean;
  doctor: boolean;
  /** Model name or "human" per seat p0..p9 (only the first nPlayers count). Advanced only. */
  seats: string[];
}

export const seatId = (i: number) => `p${i}`;
export const maxWolves = (n: number) => Math.floor((n - 1) / 2);

export async function loadSetupAssets(catalogUrl: string, schemaUrl: string): Promise<SetupAssets> {
  const get = async (url: string) => {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
    return res.json();
  };
  const [catalog, schema] = await Promise.all([get(catalogUrl), get(schemaUrl)]);
  return { catalog, schema };
}

/** Basic defaults: 10 seats, 2 wolves, seer and doctor, seed 0, no human. Seats start as the lineup. */
export function defaultState(catalog: ModelsCatalog): SetupState {
  const lineup = catalog.default_lineup.length ? catalog.default_lineup : catalog.models.map((m) => m.name);
  return {
    mode: "basic",
    seed: 0,
    basicHuman: false,
    nPlayers: MAX_PLAYERS,
    nWolves: 2,
    seer: true,
    doctor: true,
    seats: Array.from({ length: MAX_PLAYERS }, (_, i) => lineup[i % lineup.length] ?? ""),
  };
}

export function humanSeat(s: SetupState): string | null {
  if (s.mode === "basic") return s.basicHuman ? "p0" : null;
  const i = s.seats.slice(0, s.nPlayers).indexOf(HUMAN);
  return i < 0 ? null : seatId(i);
}

/** Move the human to seat `i` (or remove with null). The seat they leave gets a default model. */
export function setHumanSeat(s: SetupState, i: number | null, catalog: ModelsCatalog): SetupState {
  const base = defaultState(catalog).seats;
  const seats = s.seats.map((m, k) => (m === HUMAN ? base[k] : m));
  if (i !== null) seats[i] = HUMAN;
  return { ...s, seats };
}

/** Switching Basic -> Advanced keeps a Basic human at p0. */
export function setMode(s: SetupState, mode: SetupState["mode"]): SetupState {
  if (mode === s.mode) return s;
  if (mode === "advanced" && s.basicHuman) {
    const seats = s.seats.slice();
    seats[0] = HUMAN;
    return { ...s, mode, seats };
  }
  return { ...s, mode };
}

/** One readable line per problem, worded like the backend's. Empty means valid. */
export function validate(s: SetupState, catalog: ModelsCatalog): string[] {
  const errors: string[] = [];
  if (!Number.isInteger(s.seed)) errors.push("seed: Input should be a valid integer");
  if (s.mode === "basic") return errors;
  const n = s.nPlayers;
  if (!Number.isInteger(n) || n < MIN_PLAYERS || n > MAX_PLAYERS) {
    errors.push(`n_players: Input should be between ${MIN_PLAYERS} and ${MAX_PLAYERS}`);
    return errors;
  }
  if (!Number.isInteger(s.nWolves) || s.nWolves < 1) errors.push("n_wolves: Input should be at least 1");
  else if (s.nWolves >= n - s.nWolves) {
    errors.push(
      `${s.nWolves} wolves need more than ${n - s.nWolves} villagers; use at most ${maxWolves(n)} wolves for ${n} players`,
    );
  }
  const byName = new Map(catalog.models.map((m) => [m.name, m]));
  const seats = s.seats.slice(0, n);
  const humans = seats.flatMap((m, i) => (m === HUMAN ? [seatId(i)] : []));
  if (humans.length > 1) errors.push(`at most one human seat, got ${humans.join(", ")}`);
  seats.forEach((m, i) => {
    if (m === HUMAN) return;
    const spec = byName.get(m);
    if (!spec) {
      const known = catalog.models.map((x) => x.name).sort().join(", ");
      errors.push(`unknown model ${JSON.stringify(m)} at ${seatId(i)}; known: ${known}`);
    } else if (!spec.available) {
      errors.push(`${seatId(i)}: ${m} is unavailable${spec.unavailable_reason ? ` (${spec.unavailable_reason})` : ""}`);
    }
  });
  return errors;
}

/** The setup JSON. Basic sends only seed and human_seat; Advanced sends explicit seats. */
export function toSetupJson(s: SetupState): GameSetupJson {
  if (s.mode === "basic") {
    const out: GameSetupJson = { mode: "basic", seed: s.seed };
    if (s.basicHuman) out.human_seat = "p0";
    return out;
  }
  const seats = s.seats.slice(0, s.nPlayers).map((model, i) => ({ seat: seatId(i), model }));
  const h = humanSeat(s);
  const out: GameSetupJson = {
    mode: "advanced",
    n_players: s.nPlayers,
    n_wolves: s.nWolves,
    special_roles: { seer: s.seer, doctor: s.doctor },
    seats,
    seed: s.seed,
  };
  if (h) out.human_seat = h;
  return out;
}

export const PLAY_COMMAND = "societysim play --setup setup.json";

// --- a tiny JSON Schema checker: the keywords game_setup.schema.json uses ---------------------

export function schemaErrors(value: unknown, schema: Schema, root: Schema = schema, at = "$"): string[] {
  if (schema.$ref) {
    const target = (schema.$ref as string).replace("#/", "").split("/").reduce((o, k) => o[k], root);
    return schemaErrors(value, target, root, at);
  }
  if (schema.anyOf) {
    const tries = (schema.anyOf as Schema[]).map((sub) => schemaErrors(value, sub, root, at));
    return tries.some((t) => t.length === 0) ? [] : [`${at}: matches none of anyOf`];
  }
  const errs: string[] = [];
  const t = schema.type as string | undefined;
  const kind = value === null ? "null" : Array.isArray(value) ? "array" : typeof value;
  if (t && !(t === kind || (t === "integer" && Number.isInteger(value)) || (t === "number" && kind === "number"))) {
    return [`${at}: expected ${t}, got ${kind}`];
  }
  if (schema.enum && !schema.enum.includes(value)) errs.push(`${at}: not one of ${schema.enum.join(", ")}`);
  if (typeof value === "number") {
    if (schema.minimum !== undefined && value < schema.minimum) errs.push(`${at}: below ${schema.minimum}`);
    if (schema.maximum !== undefined && value > schema.maximum) errs.push(`${at}: above ${schema.maximum}`);
  }
  if (typeof value === "string" && schema.pattern && !new RegExp(schema.pattern).test(value)) {
    errs.push(`${at}: does not match ${schema.pattern}`);
  }
  if (Array.isArray(value)) {
    if (schema.minItems !== undefined && value.length < schema.minItems) errs.push(`${at}: too few items`);
    if (schema.items) value.forEach((v, i) => errs.push(...schemaErrors(v, schema.items, root, `${at}[${i}]`)));
  }
  if (kind === "object") {
    const obj = value as Record<string, unknown>;
    const props = (schema.properties ?? {}) as Record<string, Schema>;
    for (const k of schema.required ?? []) if (!(k in obj)) errs.push(`${at}.${k}: required`);
    for (const [k, v] of Object.entries(obj)) {
      if (k in props) errs.push(...schemaErrors(v, props[k], root, `${at}.${k}`));
      else if (schema.additionalProperties === false) errs.push(`${at}.${k}: unknown field`);
    }
  }
  return errs;
}
