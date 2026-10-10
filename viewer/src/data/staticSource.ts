import { fetchLabels, siblingLabelsUrl } from "./labels";
import { staticSource, type GameSource, type GameSummary } from "./source";

/**
 * One entry of `<batch>/report/index.json`, written by `societysim report` (analysis/report.py).
 * `file` is relative to the batch dir, one level above index.json.
 */
export interface IndexEntry {
  game_id: string;
  file: string;
  seed: number;
  days: number;
  winner: string | null;
  seats: Record<string, { name: string; model: string; role: string }>;
}

/** The deploy layout: a batch dir copied to `data/` next to the viewer (scripts/deploy-space.sh). */
export const DEFAULT_INDEX_URL = "data/report/index.json";

export async function fetchJson<T>(url: string): Promise<T> {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`);
  try {
    return (await res.json()) as T;
  } catch {
    throw new Error(`${url}: not JSON`);
  }
}

/** Roles stay out: the summary is shown before anyone opens the game. */
export const summarize = (e: IndexEntry): GameSummary => ({
  id: e.game_id,
  days: e.days,
  winner: e.winner,
  models: [...new Set(Object.values(e.seats).map((s) => s.model))].sort(),
});

/** A GameSource over a static index.json. URLs resolve against `base`, so relative paths work on any host. */
export function indexSource(indexUrl: string, base = window.location.href): GameSource {
  const absIndex = new URL(indexUrl, base);
  const loaded = fetchJson<IndexEntry[]>(absIndex.href).then((entries) => {
    const urls = Object.fromEntries(entries.map((e) => [e.game_id, new URL(`../${e.file}`, absIndex).href]));
    return { entries, urls, source: staticSource(urls) };
  });
  loaded.catch(() => {}); // surfaced by whichever method awaits it
  return {
    listGames: async () => (await loaded).entries.map(summarize),
    loadGame: async (id) => (await loaded).source.loadGame(id),
    pollEvents: async (id, afterSeq) => (await loaded).source.pollEvents(id, afterSeq),
    loadLabels: async (id) => {
      const url = (await loaded).urls[id];
      return url ? fetchLabels(siblingLabelsUrl(url)) : [];
    },
  };
}
