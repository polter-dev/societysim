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

  function stop() {
    run++;
    if (audio) {
      audio.onended = audio.onerror = null;
      audio.pause();
    }
  }

  function play(clips: Clip[]) {
    stop();
    if (!clips.length) return;
    audio ??= makeAudio();
    const el = audio;
    const mine = run;
    let i = 0;
    const next = () => {
      if (mine !== run || i >= clips.length) return;
      el.src = urlOf(clips[i++]);
      el.onended = next;
      el.onerror = next; // a missing file skips to the next clip
      try {
        // Autoplay can be blocked until a user gesture; treat that as silence.
        void Promise.resolve(el.play()).catch(() => {});
      } catch {
        /* ignore */
      }
    };
    next();
  }

  return { play, stop };
}
