import { useEffect, useMemo, useRef, useState } from "react";
import type { Mode } from "../scene/deriveScene";
import {
  type Category,
  clipsForSeq,
  clipUrl,
  DEFAULT_ENABLED,
  type Enabled,
  loadIndex,
  type NarrationIndex,
} from "./narration";
import { type AudioLike, createPlayer } from "./player";

/**
 * Narration for the current step. `seq` is the seq of the event at the current step
 * (null when there is none). Each change of seq, mode or toggles stops the old clips first.
 * `indexUrl` is the URL of `<game_id>.audio/index.json`, or null for no narration.
 */
export function useNarration(indexUrl: string | null, seq: number | null, mode: Mode) {
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
    if (!on || seq === null) player.stop();
    else player.play(clipsForSeq(index, seq, mode, enabled));
  }, [on, seq, mode, enabled, index, player]);
  useEffect(() => player.stop, [player]);

  return {
    available: index !== null && index.clips.length > 0,
    on,
    setOn,
    enabled,
    setCategory: (c: Category, v: boolean) => setEnabled((e) => ({ ...e, [c]: v })),
  };
}
