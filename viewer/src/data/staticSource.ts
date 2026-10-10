import { fetchLabels, siblingLabelsUrl } from "./labels";
import { staticSource, type GameSource, type GameSummary } from "./source";

/**
 * One entry of `<batch>/report/index.json`, written by `societysim report` (analysis/report.py).
 * Root deployments put `file` alongside index.json; legacy report/index.json sits one level below it.
 */
export interface IndexEntry {
  game_id: string;
  file: string;
  seed: number;
  days: number;
  winner: string | null;
  seats: Record<string, { name: string; model: string; role: string }>;
}

/** Published site layout: index.json and game logs share the site root. */
export const DEFAULT_INDEX_URL = "./index.json";

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
    const urls = Object.fromEntries(entries.map((e) => [e.game_id, new URL(e.file, absIndex.pathname.endsWith("/report/index.json") ? new URL("../", absIndex) : absIndex).href]));
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

/** ?base overrides the published data root; never a filesystem path. */
export function publishedBase(search: string, href: string): URL {
  const value = new URLSearchParams(search).get('base');
  const url = value ? new URL(value, href) : new URL('./', href);
  if (!/^https?:$/.test(url.protocol)) throw new Error('Batch base must be an HTTP or HTTPS URL');
  url.search = ''; url.hash = '';
  if (!url.pathname.endsWith('/')) url.pathname += '/';
  return url;
}
