import { useEffect, useMemo, useRef, useState } from "react";
import type { Mode } from "../scene/deriveScene";
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
 * Each change of range, mode or toggles stops the old clips first.
 * `indexUrl` is the URL of `<game_id>.audio/index.json`, or null for no narration.
 */
export function useNarration(indexUrl: string | null, at: number | { first: number; last: number } | null, mode: Mode) {
  const first = at === null ? null : typeof at === "number" ? at : at.first;
  const last = at === null ? null : typeof at === "number" ? at : at.last;
  const [on, setOn] = useState(false);
  const [enabled, setEnabled] = useState<Enabled>(DEFAULT_ENABLED);
  const [index, setIndex] = useState<NarrationIndex | null>(null);
  const urlRef = useRef(indexUrl);
  urlRef.current = indexUrl;
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
    setIndex(null);
    if (indexUrl) loadIndex(indexUrl).then((i) => live && setIndex(i));
    return () => {
      live = false;
    };
  }, [indexUrl]);

  useEffect(() => {
    if (!on || first === null || last === null) player.stop();
    else player.play(clipsForSeqRange(index, first, last, mode, enabled));
  }, [on, first, last, mode, enabled, index, player]);
  useEffect(() => player.stop, [player]);

  return {
    available: index !== null && index.clips.length > 0,
    on,
    setOn,
    enabled,
    setCategory: (c: Category, v: boolean) => setEnabled((e) => ({ ...e, [c]: v })),
  };
}
