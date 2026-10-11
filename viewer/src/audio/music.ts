/** Background music: the track, its credit, and the few settings the player shares. */

/** Where Seth drops the file: viewer/public/music/background.mp3 (served at ./music/background.mp3). */
export const MUSIC_PATH = "music/background.mp3";
export const MUSIC_VOLUME = 0.25;

/** TODO(Seth): fill these in when the track is added. Shown in the About dialog as "Music: <title> by <artist>". */
export const MUSIC_TITLE = "TBD";
export const MUSIC_ARTIST = "TBD";
export const musicCredit = () => `Music: ${MUSIC_TITLE} by ${MUSIC_ARTIST}`;

export const MUTED_KEY = "societysim.music.muted";
export const POSITION_KEY = "societysim.music.position";

export function readMuted(): boolean {
  try {
    return localStorage.getItem(MUTED_KEY) === "1";
  } catch {
    return false;
  }
}

export function writeMuted(muted: boolean) {
  try {
    localStorage.setItem(MUTED_KEY, muted ? "1" : "0");
  } catch {
    /* storage blocked: the choice just won't persist */
  }
}

/** Seconds into the track when the previous page of this visit unloaded (pages are full reloads). */
export function readPosition(): number {
  try {
    const t = Number(sessionStorage.getItem(POSITION_KEY));
    return Number.isFinite(t) && t > 0 ? t : 0;
  } catch {
    return 0;
  }
}

export function writePosition(seconds: number) {
  try {
    sessionStorage.setItem(POSITION_KEY, String(seconds));
  } catch {
    /* storage blocked: the track restarts on the next page */
  }
}
