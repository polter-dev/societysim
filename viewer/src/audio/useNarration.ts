import { useEffect, useMemo, useRef, useState } from "react";
import type { Mode } from "../scene/deriveScene";
import type { Event } from "../types/events";
import { BANK_INDEX_URL, bankIndex, loadBankFiles } from "./bank";
import {
  type Category,
  clipsForSeqRange,
  clipUrl,
  DEFAULT_ENABLED,
  type Enabled,
  loadIndex,
  type NarrationIndex,
} from "./narration";
import { type AudioLike, createPlayer } from "./player";

/**
 * Narration for the current step. `at` is one event seq, or `{ first, last }` (a stepper beat's
 * firstSeq/lastSeq) to play every clip in the range; null when there is none.
 * Each change of range, mode, toggles or of the range's clips stops the old clips first.
 * `indexUrl` is the URL of `<game_id>.audio/index.json` (a game's own audio), or null.
 * Without a usable own index, `events` (a replay or a live game) get narrator lines from the
 * shared phrase bank (bank.ts).
 * `control`: an outside [on, setOn] pair, so the switch survives a remount of the caller.
 */
export function useNarration(
  indexUrl: string | null,
  at: number | { first: number; last: number } | null,
  mode: Mode,
  events: Event[] = [],
  control?: readonly [boolean, (on: boolean) => void],
) {
  const first = at === null ? null : typeof at === "number" ? at : at.first;
  const last = at === null ? null : typeof at === "number" ? at : at.last;
  const local = useState(false);
  const [on, setOn] = control ?? local;
  const [enabled, setEnabled] = useState<Enabled>(DEFAULT_ENABLED);
  // undefined while the game's own index loads; null when there is none
  const [own, setOwn] = useState<NarrationIndex | null | undefined>(indexUrl ? undefined : null);
  const [bank, setBank] = useState<NarrationIndex | null>(null);
  const useOwn = own != null && own.clips.length > 0;
  const index = useOwn ? own : own !== undefined ? bank : null;
  const baseUrl = useOwn ? indexUrl : BANK_INDEX_URL;
  const urlRef = useRef(baseUrl);
  urlRef.current = baseUrl;
  const player = useMemo(
    () =>
      createPlayer(
        () => new Audio() as AudioLike,
        (c) => clipUrl(urlRef.current ?? "", c),
      ),
    [],
  );

  useEffect(() => {
    let live = true;
    setOwn(indexUrl ? undefined : null);
    if (indexUrl) loadIndex(indexUrl).then((i) => live && setOwn(i));
    return () => {
      live = false;
    };
  }, [indexUrl]);

  const needBank = own !== undefined && !useOwn && events.length > 0;
  useEffect(() => {
    if (!needBank) return;
    let live = true;
    loadBankFiles()
      .then((files) => bankIndex(events, files))
      .then((i) => live && setBank(i));
    return () => {
      live = false;
    };
  }, [needBank, events]);

  const clips = first === null || last === null ? [] : clipsForSeqRange(index, first, last, mode, enabled);
  // A live game rebuilds the bank index as events arrive. Act only when this beat's clips change,
  // and when the same beat gains a line, queue it after the clip playing instead of restarting.
  const clipsKey = clips.map((c) => `${c.seq}:${c.category}:${c.file}`).join(",");
  const clipsRef = useRef(clips);
  clipsRef.current = clips;
  const playedFor = useRef<number | null>(null);
  useEffect(() => {
    if (!on || first === null) {
      playedFor.current = null;
      return player.stop();
    }
    if (playedFor.current === first) player.extend(clipsRef.current);
    else player.play(clipsRef.current);
    playedFor.current = first;
  }, [on, first, clipsKey, player]);
  useEffect(() => player.stop, [player]);

  return {
    available: index !== null && index.clips.length > 0,
    on,
    setOn,
    enabled,
    /** See `createPlayer().whenIdle`: autoplay holds the next beat until the clips have finished. */
    whenIdle: player.whenIdle,
    setCategory: (c: Category, v: boolean) => setEnabled((e) => ({ ...e, [c]: v })),
  };
}
