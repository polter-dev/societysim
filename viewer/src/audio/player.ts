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

  function play(clips: Clip[]) {
    halt();
    // Waiters carry over to a new run (the clips changed under them); they leave only when no clips remain.
    if (!clips.length) return settle();
    busy = true;
    audio ??= makeAudio();
    const el = audio;
    const mine = run;
    let i = 0;
    const next = () => {
      if (mine !== run) return;
      if (i >= clips.length) return settle();
      el.src = urlOf(clips[i++]);
      el.onended = next;
      el.onerror = next; // a missing file skips to the next clip
      try {
        // Autoplay can be blocked until a user gesture; treat that as silence.
        // It also plays no sound, so nothing will fire onended: give up on this run rather than stall autoplay.
        void Promise.resolve(el.play()).catch(() => mine === run && stop());
      } catch {
        stop();
      }
    };
    next();
  }

  return { play, stop, whenIdle };
}
