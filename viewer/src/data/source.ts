import type { Event } from "../types/events";
import type { Label } from "./labels";
import { parseLog, type ParsedLog } from "./parse";

/** What the index page shows before a game is opened. Never roles; the page hides winner by default. */
export interface GameSummary {
  id: string;
  days?: number;
  winner?: string | null;
  models?: string[];
  /** Still being played (Neon `games.status = 'running'`). */
  live?: boolean;
}

/**
 * A loaded game. `live`: still running; the events are public only and more will come.
 * `partial`: the rows hold public events only (a running game, or a visitor game that never
 * finished), so the seat list was rebuilt without names or roles.
 */
export interface LoadedGame extends ParsedLog {
  live?: boolean;
  partial?: boolean;
  /** Swapped in by the live poll when the game ended: the replay opens at the end, not the start. */
  watchedLive?: boolean;
  /** The game ended but its full log is not backfilled yet; the live poll is still waiting for it. */
  finishing?: boolean;
}

/** Where games come from: static files or Neon (data/neonSource.ts). */
export interface GameSource {
  listGames(): Promise<GameSummary[]>;
  loadGame(id: string): Promise<LoadedGame>;
  /** Events with seq > afterSeq. Static games are finished, so this only matters for live sources. */
  pollEvents(gameId: string, afterSeq: number): Promise<Event[]>;
  /** `running`, `finished` or `failed`; live sources only. */
  gameStatus?(id: string): Promise<string | null>;
  /** The game's lie labels; [] when it has none. */
  loadLabels?(id: string): Promise<Label[]>;
  /** URL of the game's `<game_id>.audio/index.json`, or null when the source has no narration. */
  audioIndexUrl?(id: string): Promise<string | null>;
}

/** Fetch and parse one .jsonl log by URL. */
export async function fetchLog(url: string): Promise<ParsedLog> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  return parseLog(await res.text());
}

/**
 * Static source over a fixed map of game id -> .jsonl URL (relative URLs work, e.g. a
 * `runs/<batch_id>/` folder served next to the viewer). T4.9 builds the map from index.json.
 */
export function staticSource(urls: Record<string, string>): GameSource {
  const url = (id: string) => {
    const u = urls[id];
    if (!u) throw new Error(`unknown game ${id}`);
    return u;
  };
  return {
    listGames: async () => Object.keys(urls).map((id) => ({ id })),
    loadGame: (id) => fetchLog(url(id)),
    pollEvents: async (id, afterSeq) =>
      (await fetchLog(url(id))).events.filter((e) => e.seq > afterSeq),
  };
}

/**
 * Retry `loadGame` while it fails, for a visitor game opened right after `POST /api/games`: its
 * Neon row lands a moment later (longer on a cold start), so the first loads say "unknown game".
 */
export function retryLoad<S extends GameSource>(source: S, tries = 30, delayMs = 2000): S {
  return {
    ...source,
    loadGame: async (id) => {
      for (let i = 1; ; i++) {
        try {
          return await source.loadGame(id);
        } catch (err) {
          if (i >= tries) throw err;
          await new Promise((r) => setTimeout(r, delayMs));
        }
      }
    },
  };
}
