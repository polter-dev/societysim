import type { Mode } from "../scene/deriveScene";

export type Category = "narrator" | "speech" | "wolf_chat" | "highlight";
export const CATEGORIES: Category[] = ["narrator", "speech", "wolf_chat", "highlight"];

export interface Clip {
  seq: number;
  category: Category;
  speaker: string;
  voice: string;
  text: string;
  file: string;
}

export interface NarrationIndex {
  gameId: string;
  clips: Clip[];
}

/** Which categories a view mode may play. wolf_chat and highlight leak hidden info. */
export function allowedCategories(mode: Mode): Category[] {
  return mode === "narrator" ? CATEGORIES : ["narrator", "speech"];
}

/** Default toggles: narrator events on, everything else off. */
export type Enabled = Record<Category, boolean>;
export const DEFAULT_ENABLED: Enabled = {
  narrator: true,
  speech: false,
  wolf_chat: false,
  highlight: false,
};

/** Validate `<game_id>.audio/index.json`. Anything unusable gives null (no narration), never a throw. */
export function parseIndex(raw: unknown): NarrationIndex | null {
  if (typeof raw !== "object" || raw === null) return null;
  const r = raw as Record<string, unknown>;
  if (!Array.isArray(r.clips)) return null;
  const clips: Clip[] = [];
  for (const c of r.clips) {
    if (typeof c !== "object" || c === null) continue;
    const x = c as Record<string, unknown>;
    if (
      typeof x.seq !== "number" ||
      typeof x.file !== "string" ||
      !CATEGORIES.includes(x.category as Category)
    )
      continue;
    clips.push({
      seq: x.seq,
      category: x.category as Category,
      speaker: typeof x.speaker === "string" ? x.speaker : "",
      voice: typeof x.voice === "string" ? x.voice : "",
      text: typeof x.text === "string" ? x.text : "",
      file: x.file,
    });
  }
  return { gameId: typeof r.game_id === "string" ? r.game_id : "", clips };
}

/** Fetch the index; a missing or broken index means no narration. */
export async function loadIndex(
  url: string,
  fetchFn: typeof fetch = fetch,
): Promise<NarrationIndex | null> {
  try {
    const res = await fetchFn(url);
    return res.ok ? parseIndex(await res.json()) : null;
  } catch {
    return null;
  }
}

/** Clips whose seq is in [firstSeq, lastSeq] (a stepper beat), in index order, filtered by mode and toggles. */
export function clipsForSeqRange(
  index: NarrationIndex | null,
  firstSeq: number,
  lastSeq: number,
  mode: Mode,
  enabled: Enabled = DEFAULT_ENABLED,
): Clip[] {
  if (!index) return [];
  const ok = allowedCategories(mode);
  return index.clips.filter(
    (c) => c.seq >= firstSeq && c.seq <= lastSeq && ok.includes(c.category) && enabled[c.category],
  );
}

/** Clips that follow event `seq`, in index order, filtered by mode and toggles. */
export function clipsForSeq(
  index: NarrationIndex | null,
  seq: number,
  mode: Mode,
  enabled: Enabled = DEFAULT_ENABLED,
): Clip[] {
  return clipsForSeqRange(index, seq, seq, mode, enabled);
}

/** URL of a clip given the index.json URL (clips sit beside it). */
export function clipUrl(indexUrl: string, clip: Clip): string {
  return indexUrl.slice(0, indexUrl.lastIndexOf("/") + 1) + clip.file;
}
