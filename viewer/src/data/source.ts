import type { Event } from "../types/events";
import { parseLog, type ParsedLog } from "./parse";

export interface GameSummary {
  id: string;
}

/** Where games come from: static files now, Neon later (T6.2). */
export interface GameSource {
  listGames(): Promise<GameSummary[]>;
  loadGame(id: string): Promise<ParsedLog>;
  /** Events with seq > afterSeq. Static games are finished, so this only matters for live sources. */
  pollEvents(gameId: string, afterSeq: number): Promise<Event[]>;
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
