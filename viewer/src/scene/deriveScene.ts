import type { Event, Phase, Role } from "../types/events";

/** narrator: everything. villager: only what a player at the table could see (public events). */
export type Mode = "narrator" | "villager";

export interface PlayerView {
  id: string; // seat id
  name: string;
  model: string;
  color: string;
  alive: boolean;
  /** null when this mode may not know it. */
  role: Role | null;
  /** The role has been made public (death reveal, elimination or game end). */
  revealed: boolean;
}

export interface Accusation {
  actor: string;
  target: string;
  seq: number;
}

export interface Vote {
  actor: string;
  target: string | null; // null = abstain
  seq: number;
  fallback: boolean;
}

export interface Scene {
  /** seq of events[idx]. */
  seq: number;
  day: number;
  phase: Phase;
  players: PlayerView[];
  /** events[idx], or null when this mode can't see it. */
  current: Event | null;
  /** Every event up to idx this mode can see. In villager mode private_reasoning and suspicion are stripped. */
  log: Event[];
  /** Who is speaking: the actor of the last statement in the current phase. */
  speaker: string | null;
  /** Accusations made so far today. */
  accusations: Accusation[];
  /** Votes cast so far today, in log order. */
  votes: Vote[];
  /** Non-abstain votes per seat so far today; the logged meta.tally once the result is in. */
  tally: Record<string, number>;
  winner: "wolves" | "villagers" | "draw" | null;
}

export function visibleIn(e: Event, mode: Mode): boolean {
  return mode === "narrator" || e.visibility === "public";
}

function redact(e: Event, mode: Mode): Event {
  return mode === "narrator" ? e : { ...e, private_reasoning: null, suspicion: null };
}

/**
 * Pure: the scene after events[0..idx] (idx is clamped to the log), as seen in `mode`.
 * ponytail: refolds from 0 on every call; fine for ~200-event games, memoize per idx if scrubbing lags.
 */
export function deriveScene(events: Event[], idx: number, mode: Mode): Scene {
  if (events.length === 0) throw new Error("deriveScene needs at least one event");
  idx = Math.max(0, Math.min(idx, events.length - 1));

  const seats = events.find((e) => e.type === "game_start")?.meta.seats ?? {};
  const players: PlayerView[] = Object.entries(seats).map(([id, s]) => ({
    id,
    name: s.name,
    model: s.model,
    color: s.color,
    alive: true,
    role: mode === "narrator" ? s.role : null,
    revealed: false,
  }));
  const byId = new Map(players.map((p) => [p.id, p]));
  const reveal = (id: string | null, role: Role | null | undefined) => {
    const p = id ? byId.get(id) : undefined;
    if (!p || !role) return;
    p.revealed = true;
    p.role = role;
  };

  const scene: Scene = {
    seq: events[idx].seq,
    day: 0,
    phase: "setup",
    players,
    current: visibleIn(events[idx], mode) ? redact(events[idx], mode) : null,
    log: [],
    speaker: null,
    accusations: [],
    votes: [],
    tally: {},
    winner: null,
  };

  for (const e of events.slice(0, idx + 1)) {
    if (!visibleIn(e, mode)) continue;
    scene.log.push(redact(e, mode));
    if (e.day !== scene.day) {
      scene.accusations = [];
      scene.votes = [];
      scene.tally = {};
    }
    scene.day = e.day;
    scene.phase = e.phase;

    switch (e.type) {
      case "phase_start":
        scene.speaker = null;
        break;
      case "statement":
        scene.speaker = e.actor;
        break;
      case "accusation":
        if (e.actor && e.target) scene.accusations.push({ actor: e.actor, target: e.target, seq: e.seq });
        break;
      case "vote":
        if (!e.actor) break;
        scene.votes.push({ actor: e.actor, target: e.target, seq: e.seq, fallback: !!e.meta.fallback });
        if (e.target) scene.tally[e.target] = (scene.tally[e.target] ?? 0) + 1;
        break;
      case "death_reveal":
      case "elimination": {
        const p = e.target ? byId.get(e.target) : undefined;
        if (p) p.alive = false;
        reveal(e.target, e.meta.role);
        if (e.type === "elimination" && e.meta.tally) scene.tally = { ...e.meta.tally };
        break;
      }
      case "no_elimination":
        if (e.meta.tally) scene.tally = { ...e.meta.tally };
        break;
      case "game_end":
        scene.winner = e.meta.winner ?? null;
        for (const [id, role] of Object.entries(e.meta.roles ?? {})) reveal(id, role);
        break;
    }
  }
  return scene;
}
