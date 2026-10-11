import type { Event } from "../types/events";
import type { Clip, NarrationIndex } from "./narration";

/**
 * Narrator lines built in the browser and played from the shared phrase bank
 * (viewer/public/narration-bank/, a copy of narration/bank). A port of `narrator_line` and the
 * narrator part of `build_clips` in src/societysim/analysis/narrate.py: the texts and voice ids
 * must stay identical, since bank files are named sha1(text + voice_id).mp3.
 */

/** Relative to the page, like every other asset (vite base "./"). Clips sit beside it. */
export const BANK_INDEX_URL = "narration-bank/index.json";

/** configs/voices.yaml narrator.day / narrator.night. */
export const NARRATOR_VOICES = { narrator: "N2lVS1w4EtoT3dr4eOWO", narrator_night: "SOYHLrjzK2X1ezoPC6cr" } as const;
type Voice = keyof typeof NARRATOR_VOICES;

const ROLE: Record<string, string> = { wolf: "a wolf", seer: "the Seer", doctor: "the Doctor", villager: "a villager" };

/**
 * (voice, text) the narrator says after `e`, or null. Public events only: the target's name and,
 * when the event itself reveals it, their role.
 */
export function narratorLine(e: Event, names: Record<string, string>): [Voice, string] | null {
  if (e.visibility !== "public") return null;
  const who = e.target ? (names[e.target] ?? e.target) : null;
  const role = e.meta?.role ? `, ${ROLE[e.meta.role]}` : "";
  switch (e.type) {
    case "phase_start":
      if (e.phase === "night") return ["narrator_night", `Night ${e.day}. The village sleeps.`];
      if (e.phase === "day") return ["narrator", `Day ${e.day}. The village wakes.`];
      if (e.phase === "vote") return ["narrator", `Day ${e.day}. Time to vote.`];
      return null;
    case "death_reveal":
      return who
        ? ["narrator_night", `The wolves struck. ${who}${role && role + ","} was found dead.`]
        : ["narrator_night", "Nobody died last night."];
    case "elimination":
      return who ? ["narrator", `The village voted out ${who}${role}.`] : null;
    case "no_elimination":
      if (e.meta?.reason === "tie") return ["narrator", "The vote is tied. Nobody is eliminated."];
      if (e.meta?.reason === "all_abstain") return ["narrator", "Everyone abstained. Nobody is eliminated."];
      return ["narrator", "Nobody is eliminated."];
    case "game_end":
      if (e.meta?.winner === "villagers") return ["narrator", "The village wins. Every wolf is dead."];
      if (e.meta?.winner === "wolves") return ["narrator", "The wolves win. They now match the village."];
      if (e.meta?.winner === "draw") return ["narrator", "Time is up. The game is a draw."];
      return null;
  }
  return null;
}

/** Python's clip_file: sha1 of the UTF-8 text + voice id, hex, ".mp3". */
export async function clipFile(text: string, voiceId: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-1", new TextEncoder().encode(text + voiceId));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("") + ".mp3";
}

/** Narrator clips for `events`, in log order. Seat names come from game_start, if the stream has it. */
export async function narratorClips(events: Event[]): Promise<Clip[]> {
  const seats = events.find((e) => e.type === "game_start")?.meta?.seats ?? {};
  const names = Object.fromEntries(Object.entries(seats).map(([s, seat]) => [s, seat.name]));
  const clips: Clip[] = [];
  for (const e of events) {
    const line = narratorLine(e, names);
    if (!line) continue;
    const [voice, text] = line;
    clips.push({ seq: e.seq, category: "narrator", speaker: "narrator", voice, text, file: await clipFile(text, NARRATOR_VOICES[voice]) });
  }
  return clips;
}

let bankFiles: Promise<Set<string>> | null = null;

/** File names in the bank's index.json, fetched once. A failed fetch is an empty bank (and is retried next time). */
export function loadBankFiles(fetchFn: typeof fetch = fetch, url = BANK_INDEX_URL): Promise<Set<string>> {
  bankFiles ??= fetchFn(url)
    .then((r) => (r.ok ? r.json() : null))
    .then((raw: { clips?: { file?: unknown }[] } | null) => {
      const files = new Set((raw?.clips ?? []).map((c) => c.file).filter((f): f is string => typeof f === "string"));
      if (!files.size) bankFiles = null;
      return files;
    })
    .catch(() => {
      bankFiles = null;
      return new Set<string>();
    });
  return bankFiles;
}

/**
 * A narration index for a game without its own audio: its narrator lines that the bank has.
 * A line the bank lacks (a name it was not built with, say) is left out, so nothing 404s.
 */
export async function bankIndex(events: Event[], files: Set<string>): Promise<NarrationIndex> {
  const clips = (await narratorClips(events)).filter((c) => files.has(c.file));
  return { gameId: events[0]?.game_id ?? "", clips };
}
