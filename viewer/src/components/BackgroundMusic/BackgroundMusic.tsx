import { useEffect, useRef, useState } from "react";
import { MUSIC_PATH, MUSIC_VOLUME, readMuted, readPosition, writeMuted, writePosition } from "../../audio/music";
import "./BackgroundMusic.css";

/**
 * Looping background track plus a fixed mute button. Renders nothing until the file has loaded, so it
 * is harmless while ./music/background.mp3 is missing. Browsers block autoplay, so the track starts on
 * the first click or keypress. Pages are full reloads: the position is kept in sessionStorage.
 */
export default function BackgroundMusic() {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [ready, setReady] = useState(false);
  const [muted, setMuted] = useState(readMuted);
  const mutedRef = useRef(muted);
  mutedRef.current = muted;

  useEffect(() => {
    const audio = new Audio();
    audioRef.current = audio;
    audio.loop = true;
    audio.volume = MUSIC_VOLUME;
    audio.preload = "auto";
    let off = false;

    const start = () => {
      if (off || mutedRef.current) return;
      try {
        // Resolves only when sound actually starts; a blocked autoplay rejects and we wait for the next gesture.
        Promise.resolve(audio.play()).then(removeGestureListeners, () => {});
      } catch {
        /* no playback in this browser */
      }
    };
    const gestures = ["pointerdown", "keydown"] as const;
    const removeGestureListeners = () => gestures.forEach((g) => window.removeEventListener(g, start, true));
    gestures.forEach((g) => window.addEventListener(g, start, true));

    audio.addEventListener("loadedmetadata", () => {
      const at = readPosition();
      if (at > 0 && Number.isFinite(audio.duration) && audio.duration > 0) audio.currentTime = at % audio.duration;
    });
    audio.addEventListener("canplay", () => !off && setReady(true), { once: true });
    audio.addEventListener("error", () => !off && setReady(false), { once: true });
    audio.src = new URL(MUSIC_PATH, new URL(import.meta.env.BASE_URL, location.href)).href;
    start(); // allowed without a gesture when the visitor already interacted with the site

    const save = () => audio.currentTime > 0 && writePosition(audio.currentTime); // 0 = not started yet: keep the older position
    window.addEventListener("pagehide", save);
    return () => {
      off = true;
      removeGestureListeners();
      window.removeEventListener("pagehide", save);
      save();
      audio.pause();
      audio.removeAttribute("src");
      audioRef.current = null;
    };
  }, []);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    if (muted) audio.pause();
    else if (ready) {
      try {
        Promise.resolve(audio.play()).catch(() => {}); // still blocked: the first-gesture listener covers it
      } catch {
        /* no playback in this browser */
      }
    }
  }, [muted, ready]);

  // Lets CSS show the credit line (landing About dialog) only when there is music to credit.
  useEffect(() => {
    document.documentElement.toggleAttribute("data-music", ready);
    return () => document.documentElement.removeAttribute("data-music");
  }, [ready]);

  if (!ready) return null;
  const toggle = () => {
    const next = !muted;
    writeMuted(next);
    setMuted(next);
  };
  return (
    <button
      type="button"
      className="music-toggle"
      aria-label={muted ? "Unmute music" : "Mute music"}
      aria-pressed={muted}
      title={muted ? "Unmute music" : "Mute music"}
      onClick={toggle}
    >
      <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M11 5 6 9H3v6h3l5 4z" fill="currentColor" />
        {muted ? <path d="m16 9 5 6m0-6-5 6" /> : <path d="M15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13" />}
      </svg>
    </button>
  );
}
