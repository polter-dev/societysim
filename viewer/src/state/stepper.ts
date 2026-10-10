import type { Event } from "../types/events";

export type BeatKind =
  | "start"
  | "phase"
  | "statement"
  | "vote_result"
  | "night_kill"
  | "death"
  | "elimination"
  | "game_end";

export interface Beat {
  /** Stable id: seq of the beat's first event. */
  id: number;
  /** Seq of the first and last event in the beat; narration plays clips in this range. */
  firstSeq: number;
  lastSeq: number;
  kind: BeatKind;
  /** Index into `events` of the beat's last event; pass to deriveScene(events, endIdx, mode). */
  endIdx: number;
}

const STARTERS: Partial<Record<Event["type"], BeatKind>> = {
  game_start: "start",
  phase_start: "phase",
  statement: "statement",
  accusation: "statement",
  role_claim: "statement",
  seer_claim: "statement",
  night_kill: "night_kill",
  death_reveal: "death",
  elimination: "elimination",
  no_elimination: "elimination",
  game_end: "game_end",
};

/** Group raw events into beats. Events with no beat of their own (chat, saves, retries) join the current one. */
export function buildBeats(events: Event[]): Beat[] {
  const beats: Beat[] = [];
  let prevVote = false;
  events.forEach((e, i) => {
    const isVote = e.type === "vote";
    const last = beats[beats.length - 1];
    let kind = STARTERS[e.type];
    if (isVote && !prevVote) kind = "vote_result";
    // the elimination (or tie) closes the vote beat it follows
    if (last?.kind === "vote_result" && (e.type === "elimination" || e.type === "no_elimination")) kind = undefined;
    if (kind || !last) beats.push({ id: e.seq, firstSeq: e.seq, lastSeq: e.seq, kind: kind ?? "phase", endIdx: i });
    else {
      last.endIdx = i;
      last.lastSeq = e.seq;
    }
    prevVote = isVote;
  });
  return beats;
}

export interface StepperState {
  events: Event[];
  beats: Beat[];
  /** Index into beats. */
  pos: number;
  /** True while the game is still being appended to. */
  live: boolean;
}

export type StepperAction =
  | { type: "next" | "prev" | "first" | "last" }
  | { type: "goto"; seq: number } // a beat id or any event seq; lands on the beat containing it
  | { type: "append"; events: Event[] }
  | { type: "finish" };

export function initStepper(events: Event[], opts: { live?: boolean; seq?: number | null } = {}): StepperState {
  const beats = buildBeats(events);
  const s: StepperState = { events, beats, pos: Math.max(0, beats.length - 1), live: !!opts.live };
  return opts.seq == null ? s : stepperReducer(s, { type: "goto", seq: opts.seq });
}

const clamp = (n: number, len: number) => Math.max(0, Math.min(n, len - 1));

export function stepperReducer(s: StepperState, a: StepperAction): StepperState {
  const n = s.beats.length;
  switch (a.type) {
    case "next":
      return { ...s, pos: clamp(s.pos + 1, n) };
    case "prev":
      return { ...s, pos: clamp(s.pos - 1, n) };
    case "first":
      return { ...s, pos: 0 };
    case "last":
      return { ...s, pos: clamp(n - 1, n) };
    case "goto": {
      let pos = 0;
      s.beats.forEach((b, i) => {
        if (b.id <= a.seq) pos = i;
      });
      return { ...s, pos };
    }
    case "append": {
      if (a.events.length === 0) return s;
      const events = [...s.events, ...a.events];
      const beats = buildBeats(events);
      // following the tip keeps following; a stepped-back reader stays put
      return { ...s, events, beats, pos: atLatest(s) ? beats.length - 1 : s.pos };
    }
    case "finish":
      return { ...s, live: false };
  }
}

export const atLatest = (s: StepperState) => s.pos >= s.beats.length - 1;
/** Beats added after the current one; the "N new" in "Back to live (N new)". */
export const newCount = (s: StepperState) => Math.max(0, s.beats.length - 1 - s.pos);
/** Event index for deriveScene(events, idx, mode). */
export const eventIdx = (s: StepperState) => s.beats[s.pos]?.endIdx ?? 0;
export const currentSeq = (s: StepperState) => s.beats[s.pos]?.id ?? 0;

/** `?step=<seq>` -> seq, or null if missing or invalid. */
export function parseStep(search: string): number | null {
  const v = new URLSearchParams(search).get("step");
  return v !== null && /^\d+$/.test(v) ? Number(v) : null;
}

/** Search string with `step` set (or removed when seq is null), other params kept. */
export function withStep(search: string, seq: number | null): string {
  const p = new URLSearchParams(search);
  if (seq === null) p.delete("step");
  else p.set("step", String(seq));
  const out = p.toString();
  return out ? `?${out}` : "";
}
