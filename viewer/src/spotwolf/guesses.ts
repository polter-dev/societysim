import { deriveScene } from "../scene/deriveScene";
import type { Event, Role } from "../types/events";

/** Default rules: 2 wolves at the table (docs/RULES.md). Public knowledge, so safe to show during play. */
export const WOLF_COUNT = 2;
export const POINTS_PER_WOLF = 10;
export const PENALTY_PER_INNOCENT = 5;

/** The marks a spectator held at one beat (a beat = one log event) where they changed their mind. */
export interface Beat {
  seq: number;
  day: number;
  marks: string[];
}

export interface GuessState {
  gameId: string;
  /** Seats currently marked as suspected wolves. */
  marks: string[];
  /** One entry per beat where the marks changed, in order. */
  history: Beat[];
  /** seq of the reveal when the guesses were locked, else null. */
  lockedSeq: number | null;
}

export function initGuesses(gameId: string): GuessState {
  return { gameId, marks: [], history: [], lockedSeq: null };
}

function clamp(events: Event[], idx: number): number {
  return Math.max(0, Math.min(idx, events.length - 1));
}

/** True once events[0..idx] includes the game_end event. Nothing role-related may be shown before this. */
export function isRevealed(events: Event[], idx: number): boolean {
  if (events.length === 0) return false;
  return events.slice(0, clamp(events, idx) + 1).some((e) => e.type === "game_end");
}

/** Seats a spectator may mark right now: the living players, taken from the public (villager) view only. */
export function livingSeats(events: Event[], idx: number): { id: string; name: string }[] {
  if (events.length === 0) return [];
  return deriveScene(events, idx, "villager")
    .players.filter((p) => p.alive)
    .map((p) => ({ id: p.id, name: p.name }));
}

/** Living players plus any dead players the spectator has marked, so those marks can still be removed. */
export function selectableSeats(
  events: Event[],
  idx: number,
  marks: string[],
): { id: string; name: string; alive: boolean }[] {
  if (events.length === 0) return [];
  return deriveScene(events, idx, "villager")
    .players.filter((p) => p.alive || marks.includes(p.id))
    .map((p) => ({ id: p.id, name: p.name, alive: p.alive }));
}

export function isLocked(state: GuessState, events: Event[], idx: number): boolean {
  return state.lockedSeq !== null || isRevealed(events, idx);
}

/** Lock the guesses if the reveal has been reached. Returns the same object when nothing changes. */
export function lockIfRevealed(state: GuessState, events: Event[], idx: number): GuessState {
  if (state.lockedSeq !== null || !isRevealed(events, idx)) return state;
  const end = events.find((e) => e.type === "game_end")!;
  return { ...state, lockedSeq: end.seq };
}

/**
 * Mark or unmark a seat at the current beat. Ignored (same object returned) when guesses are locked,
 * the seat is not a living player, or marking would exceed `maxMarks`.
 */
export function toggleMark(
  state: GuessState,
  events: Event[],
  idx: number,
  seat: string,
  maxMarks = WOLF_COUNT,
): GuessState {
  if (events.length === 0 || isLocked(state, events, idx)) return state;
  const on = state.marks.includes(seat);
  // A marked player who has since died can still be unmarked; only living players can be newly marked.
  if (!on && !livingSeats(events, idx).some((s) => s.id === seat)) return state;
  if (!on && state.marks.length >= maxMarks) return state;
  const marks = on ? state.marks.filter((s) => s !== seat) : [...state.marks, seat];
  const e = events[clamp(events, idx)];
  const beat: Beat = { seq: e.seq, day: e.day, marks };
  const last = state.history[state.history.length - 1];
  const history = last && last.seq === beat.seq ? [...state.history.slice(0, -1), beat] : [...state.history, beat];
  return { ...state, marks, history };
}

// ---- scoring: everything below needs the real roles, so it only runs after the reveal ----

export interface Score {
  /** Marked seats that really are wolves. */
  hits: number;
  /** Marked seats that are not wolves. */
  misses: number;
  points: number;
  maxPoints: number;
}

/** Real roles by seat, from the game_end event. Null before the game has ended. */
export function revealedRoles(events: Event[], idx: number): Record<string, Role> | null {
  if (!isRevealed(events, idx)) return null;
  const end = events.find((e) => e.type === "game_end")!;
  if (end.meta.roles) return end.meta.roles;
  const seats = events.find((e) => e.type === "game_start")?.meta.seats ?? {};
  return Object.fromEntries(Object.entries(seats).map(([id, s]) => [id, s.role]));
}

export function scoreMarks(marks: string[], roles: Record<string, Role>): Score {
  const wolves = Object.keys(roles).filter((id) => roles[id] === "wolf");
  const hits = marks.filter((m) => roles[m] === "wolf").length;
  const misses = marks.length - hits;
  return {
    hits,
    misses,
    points: Math.max(0, hits * POINTS_PER_WOLF - misses * PENALTY_PER_INNOCENT),
    maxPoints: wolves.length * POINTS_PER_WOLF,
  };
}

export interface BeatScore extends Beat {
  score: Score;
}

export interface ModelRow {
  seat: string;
  name: string;
  model: string;
  /** Mean P(wolf) this model gave the real wolves in its last suspicion report. */
  meanWolfP: number;
  /** Mean P(wolf) it gave everyone else. */
  meanInnocentP: number;
  /** Its top-k most suspected seats (k = number of wolves), scored like the spectator's marks. */
  topPicks: string[];
  score: Score;
}

export interface Results {
  roles: Record<string, Role>;
  wolves: { id: string; name: string }[];
  /** The spectator's final marks and score. */
  marks: string[];
  score: Score;
  /** Score at each beat where the spectator changed their marks. */
  beats: BeatScore[];
  /** Non-wolf models only: wolves know who the wolves are. */
  models: ModelRow[];
}

const mean = (xs: number[]) => (xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : 0);

/** Each non-wolf model's last suspicion report, compared with the real wolves. */
export function compareModels(events: Event[], roles: Record<string, Role>): ModelRow[] {
  const seats = events.find((e) => e.type === "game_start")?.meta.seats ?? {};
  const wolves = Object.keys(roles).filter((id) => roles[id] === "wolf");
  const last = new Map<string, NonNullable<Event["suspicion"]>>();
  for (const e of events) if (e.actor && e.suspicion?.length) last.set(e.actor, e.suspicion);
  const rows: ModelRow[] = [];
  for (const [seat, susp] of last) {
    if (roles[seat] === "wolf" || !seats[seat]) continue;
    const topPicks = [...susp]
      .sort((a, b) => b.p - a.p || a.player.localeCompare(b.player))
      .slice(0, wolves.length)
      .map((s) => s.player);
    rows.push({
      seat,
      name: seats[seat].name,
      model: seats[seat].model,
      meanWolfP: mean(susp.filter((s) => roles[s.player] === "wolf").map((s) => s.p)),
      meanInnocentP: mean(susp.filter((s) => roles[s.player] !== "wolf").map((s) => s.p)),
      topPicks,
      score: scoreMarks(topPicks, roles),
    });
  }
  return rows.sort((a, b) => a.seat.localeCompare(b.seat, undefined, { numeric: true }));
}

/** The only door to anything role-related: null until the reveal, so nothing private can leak early. */
export function buildResults(events: Event[], idx: number, state: GuessState): Results | null {
  const roles = revealedRoles(events, idx);
  if (!roles) return null;
  return {
    roles,
    wolves: Object.keys(roles)
      .filter((id) => roles[id] === "wolf")
      .map((id) => ({ id, name: events.find((e) => e.type === "game_start")?.meta.seats?.[id]?.name ?? id })),
    marks: state.marks,
    score: scoreMarks(state.marks, roles),
    beats: state.history.map((b) => ({ ...b, score: scoreMarks(b.marks, roles) })),
    models: compareModels(events, roles),
  };
}
