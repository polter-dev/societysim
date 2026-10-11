import { useEffect, useMemo, useState, type CSSProperties, type KeyboardEvent } from "react";
import { type Mode } from "../../scene/deriveScene";
import type { Event, Label } from "../../types/events";
import { stepDelayMs } from "./dwell";
import { buildTimelineGroups } from "./markers";
import { indexForSeq, stepSeq } from "./seq";
import { useSeqParam } from "./useSeqParam";
import { ThemedSelect } from "../ThemedSelect/ThemedSelect";

const SPEEDS = ["0.5", "1", "2", "4"].map((v) => ({ value: v, label: `${v}×` }));

export interface TimelineProps {
  events: Event[];
  labels?: Label[];
  mode: Mode;
  /** Called with both the shareable seq and the array index needed by deriveScene. */
  onSeqChange?: (seq: number, index: number) => void;
  /**
   * Controlled mode: the stepper owns the position. `seq` is the shown event; every jump (slider, markers,
   * play) calls `onSeek` instead, and the ?seq= param is left alone. Arrow keys, Home and End are left to the stepper.
   */
  seq?: number | null;
  onSeek?: (seq: number) => void;
  /** Controlled mode: step by beat. The slider and Up/Down keys use it, since a seq +/- 1 seek can land in the same beat. */
  onStep?: (type: "next" | "prev" | "first" | "last") => void;
  /** Reports autoplay on/off so chat can leave the live regions while it runs. */
  onPlayingChange?: (playing: boolean) => void;
  /**
   * Narration hook: `cb` runs once the beat's clips have finished or failed (at once when none are playing).
   * Autoplay stays on a beat until its dwell has passed and the clips are done, plus a short gap.
   * Returns a cancel function. Without it, autoplay uses the dwell alone.
   */
  narrationIdle?: (cb: () => void) => () => void;
}

/** Pause after a narrated clip ends before the next beat. */
export const CLIP_GAP_MS = 300;
/** A clip that never ends (stalled load) cannot hold autoplay longer than this. */
export const MAX_CLIP_HOLD_MS = 20000;

const BEAT_KEYS: Record<string, "next" | "prev" | "first" | "last"> = {
  ArrowLeft: "prev", ArrowDown: "prev", ArrowRight: "next", ArrowUp: "next", Home: "first", End: "last",
};
const EMPTY_LABELS: Label[] = [];
export default function Timeline({ events, labels = EMPTY_LABELS, mode, onSeqChange, seq: controlled, onSeek, onStep, onPlayingChange, narrationIdle }: TimelineProps) {
  const isControlled = onSeek !== undefined;
  const [ownSeq, ownSet] = useSeqParam(events, !isControlled);
  const seq = isControlled ? (controlled ?? null) : ownSeq;
  const setSeq = isControlled ? onSeek : ownSet;
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  const groups = useMemo(() => buildTimelineGroups(events, labels, mode), [events, labels, mode]);
  const index = seq === null ? -1 : indexForSeq(events, seq);
  const first = events[0]?.seq;
  const last = events.at(-1)?.seq;

  const togglePlaying = () => {
    if (!playing && seq === last && first !== undefined) setSeq(first);
    setPlaying((value) => !value);
  };

  useEffect(() => onPlayingChange?.(playing), [playing, onPlayingChange]);

  useEffect(() => {
    if (seq !== null && index >= 0) onSeqChange?.(seq, index);
  }, [seq, index, onSeqChange]);

  useEffect(() => {
    if (!playing || seq === null || last === undefined) return;
    if (seq === last) {
      setPlaying(false);
      return;
    }
    const current = events[index];
    const advance = () => {
      const next = stepSeq(events, seq, 1);
      if (next !== null) setSeq(next);
    };
    const timers: number[] = [];
    let cancelWait = () => {};
    // Dwell first (shortened by speed); then, if a narrated clip is still playing, wait for it so it is never cut.
    timers.push(
      window.setTimeout(() => {
        if (!narrationIdle) return advance();
        let sync = true;
        cancelWait = narrationIdle(() => {
          if (sync) return advance();
          timers.push(window.setTimeout(advance, CLIP_GAP_MS));
        });
        sync = false;
        timers.push(window.setTimeout(advance, MAX_CLIP_HOLD_MS));
      }, current ? stepDelayMs(current, mode, speed) : 1000 / speed),
    );
    return () => {
      cancelWait();
      timers.forEach((t) => window.clearTimeout(t));
    };
  }, [events, index, last, mode, narrationIdle, playing, seq, setSeq, speed]);

  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    const tag = (event.target as HTMLElement).tagName;
    // defaultPrevented: the Speed ThemedSelect already used the key (arrows, Home/End, Space).
    if (event.defaultPrevented || tag === "SELECT" || (tag === "BUTTON" && event.key === " ")) return;
    let next: number | null | undefined;
    if (isControlled && onStep) {
      // The stepper's page-level keys skip inputs, so a focused slider would otherwise move one seq and snap back into the same beat.
      const type = BEAT_KEYS[event.key];
      if (type && (tag === "INPUT" || event.key === "ArrowUp" || event.key === "ArrowDown")) {
        event.preventDefault();
        onStep(type);
        return;
      }
    }
    if (isControlled && ["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    switch (event.key) {
      case "ArrowLeft":
      case "ArrowDown":
        next = seq === null ? null : stepSeq(events, seq, -1);
        break;
      case "ArrowRight":
      case "ArrowUp":
        next = seq === null ? null : stepSeq(events, seq, 1);
        break;
      case "Home":
        next = first ?? null;
        break;
      case "End":
        next = last ?? null;
        break;
      case " ":
        event.preventDefault();
        togglePlaying();
        return;
      default:
        return;
    }
    event.preventDefault();
    if (next !== null && next !== undefined) setSeq(next);
  };

  if (seq === null || first === undefined || last === undefined) return null;

  return (
    <section aria-label="Game timeline" data-playing={playing} onKeyDown={onKeyDown} tabIndex={0}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <button type="button" onClick={togglePlaying} aria-label={playing ? "Pause playback" : "Play playback"}>
          {playing ? "Pause" : "Play"}
        </button>
        <label>
          Speed{" "}
          <ThemedSelect className="themed-select--field" label="Speed" value={String(speed)} options={SPEEDS} onChange={(v) => setSpeed(Number(v))} />
        </label>
        <label style={{ flex: "1 1 240px" }}>
          Event {seq}
          <input
            aria-label="Event sequence"
            type="range"
            min={first}
            max={last}
            step={1}
            value={seq}
            onChange={(event) => setSeq(Number(event.target.value))}
            style={{ display: "block", width: "100%", "--pct": `${last > first ? ((seq - first) / (last - first)) * 100 : 0}%` } as CSSProperties}
          />
        </label>
        <span>{index + 1} / {events.length}</span>
      </div>
      <nav aria-label="Timeline markers" style={{ display: "flex", flexWrap: "wrap", gap: 12, marginTop: 12 }}>
        {groups.map((group) => (
          <div key={`${group.day}:${group.phase}`} style={{ border: "1px solid #d0d5dd", borderRadius: 6, padding: 8 }}>
            <button type="button" onClick={() => setSeq(group.startSeq)}>
              {group.phase === "setup" ? "Setup" : `${group.phase} ${group.day}`}
            </button>
            {group.markers.map((marker, i) => (
              <button
                key={`${marker.seq}:${marker.kind}:${i}`}
                type="button"
                onClick={() => setSeq(marker.seq)}
                title={marker.title}
                aria-label={`${marker.title}, event ${marker.seq}`}
                data-label-type={marker.labelType}
                style={{ color: marker.appearance.color, marginLeft: 4, minWidth: 24, minHeight: 24 }}
              >
                {marker.appearance.glyph}
              </button>
            ))}
          </div>
        ))}
      </nav>
    </section>
  );
}
