import type { Clip } from "./narration";

export interface AudioLike {
  src: string;
  onended: (() => void) | null;
  onerror: (() => void) | null;
  play(): Promise<void> | void;
  pause(): void;
}

/** Plays clips one after another on a single audio element. `play` replaces whatever is running. */
export function createPlayer(makeAudio: () => AudioLike, urlOf: (c: Clip) => string) {
  let audio: AudioLike | null = null;
  let run = 0;
  let busy = false;
  let waiting = new Set<() => void>();

  /** The current run is over (all clips ended, failed, or it was stopped): release anyone waiting. */
  function settle() {
    busy = false;
    const cbs = [...waiting];
    waiting = new Set();
    cbs.forEach((cb) => cb());
  }

  function halt() {
    run++;
    if (audio) {
      audio.onended = audio.onerror = null;
      audio.pause();
    }
  }

  function stop() {
    halt();
    queue = [];
    settle();
  }

  /**
   * Calls `cb` once the clips of the current `play` have all finished, failed or been stopped
   * (right away when nothing is playing). Returns a function that cancels the wait.
   */
  function whenIdle(cb: () => void): () => void {
    if (!busy) {
      cb();
      return () => {};
    }
    waiting.add(cb);
    return () => void waiting.delete(cb);
  }

  /** The clips of the current run and how many have started. */
  let queue: Clip[] = [];
  let i = 0;

  function next(mine: number) {
    if (mine !== run || !audio) return;
    if (i >= queue.length) return settle();
    const el = audio;
    el.src = urlOf(queue[i++]);
    el.onended = () => next(mine);
    el.onerror = () => next(mine); // a missing file skips to the next clip
    try {
      // Autoplay can be blocked until a user gesture; treat that as silence.
      // It also plays no sound, so nothing will fire onended: give up on this run rather than stall autoplay.
      void Promise.resolve(el.play()).catch(() => mine === run && stop());
    } catch {
      stop();
    }
  }

  function play(clips: Clip[]) {
    halt();
    queue = clips;
    i = 0;
    // Waiters carry over to a new run (the clips changed under them); they leave only when no clips remain.
    if (!clips.length) return settle();
    busy = true;
    audio ??= makeAudio();
    next(run);
  }

  /**
   * Like `play`, but when `clips` starts with the current run's clips (a live game's beat gained a
   * line), the clip playing keeps going and the new ones queue after it. Otherwise it is `play`.
   */
  function extend(clips: Clip[]) {
    const same = (a: Clip, b: Clip) => a.seq === b.seq && a.category === b.category && a.file === b.file;
    if (!queue.length || clips.length < queue.length || !queue.every((c, k) => same(c, clips[k]))) return play(clips);
    queue = clips;
    if (!busy && i < queue.length) {
      busy = true;
      next(run);
    }
  }

  return { play, extend, stop, whenIdle };
}
