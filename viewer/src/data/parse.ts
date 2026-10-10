import type { Event, EventType, Phase, Visibility } from "../types/events";

export interface BadLine {
  line: number; // 1-based
  error: string;
}

export interface ParsedLog {
  events: Event[];
  errors: BadLine[];
}

// `satisfies Record<Union, true>` makes tsc fail if the generated types gain or lose a member.
const PHASES = { setup: true, night: true, day: true, vote: true } satisfies Record<Phase, true>;
const VISIBILITIES = {
  public: true,
  wolves: true,
  seer: true,
  doctor: true,
  narrator: true,
} satisfies Record<Visibility, true>;
const EVENT_TYPES = {
  game_start: true,
  phase_start: true,
  wolf_chat: true,
  wolf_proposal: true,
  night_kill: true,
  doctor_save: true,
  seer_check: true,
  death_reveal: true,
  statement: true,
  accusation: true,
  role_claim: true,
  seer_claim: true,
  vote: true,
  elimination: true,
  no_elimination: true,
  invalid_output: true,
  fallback_action: true,
  game_end: true,
} satisfies Record<EventType, true>;

const SEAT = /^p[0-8]$/;

const isInt = (v: unknown) => Number.isInteger(v);
const isStrOrNull = (v: unknown) => v === null || typeof v === "string";
const isSeatOrNull = (v: unknown) => v === null || (typeof v === "string" && SEAT.test(v));

/** Returns why `v` is not a valid Event, or null if it is. Checks the envelope, not every meta key. */
function invalid(v: unknown): string | null {
  if (typeof v !== "object" || v === null || Array.isArray(v)) return "not a JSON object";
  const e = v as Record<string, unknown>;
  if (e.schema_version !== 1 && e.schema_version !== 2)
    return `unsupported schema_version ${String(e.schema_version)}`;
  if (typeof e.game_id !== "string") return "game_id must be a string";
  for (const k of ["seq", "t", "day"]) if (!isInt(e[k])) return `${k} must be an integer`;
  if (!Object.hasOwn(PHASES, e.phase as string)) return `unknown phase ${String(e.phase)}`;
  if (!Object.hasOwn(EVENT_TYPES, e.type as string)) return `unknown type ${String(e.type)}`;
  if (!Object.hasOwn(VISIBILITIES, e.visibility as string))
    return `unknown visibility ${String(e.visibility)}`;
  if (!isSeatOrNull(e.actor)) return "actor must be a seat id or null";
  if (!isSeatOrNull(e.target)) return "target must be a seat id or null";
  if (!isStrOrNull(e.public_text)) return "public_text must be a string or null";
  if (!isStrOrNull(e.private_reasoning)) return "private_reasoning must be a string or null";
  if (e.suspicion !== null && !Array.isArray(e.suspicion)) return "suspicion must be a list or null";
  if (typeof e.meta !== "object" || e.meta === null || Array.isArray(e.meta))
    return "meta must be an object";
  return null;
}

/** Parse a <game_id>.jsonl log. Bad lines are reported and skipped; blank lines are ignored. */
export function parseLog(text: string): ParsedLog {
  const events: Event[] = [];
  const errors: BadLine[] = [];
  text.split("\n").forEach((raw, i) => {
    if (!raw.trim()) return;
    let v: unknown;
    try {
      v = JSON.parse(raw);
    } catch (err) {
      errors.push({ line: i + 1, error: `invalid JSON: ${(err as Error).message}` });
      return;
    }
    const why = invalid(v);
    if (why) return errors.push({ line: i + 1, error: why });
    const ev = v as Event;
    const prev = events.at(-1);
    if (prev && ev.seq <= prev.seq)
      return errors.push({ line: i + 1, error: `seq ${ev.seq} is not after ${prev.seq}` });
    events.push(ev);
  });
  return { events, errors };
}
