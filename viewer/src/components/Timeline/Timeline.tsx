import { useEffect, useMemo, useState, type KeyboardEvent } from "react";
import { type Mode } from "../../scene/deriveScene";
import type { Event, Label } from "../../types/events";
import { buildTimelineGroups, type MarkerKind } from "./markers";
import { indexForSeq, stepSeq } from "./seq";
import { useSeqParam } from "./useSeqParam";

export interface TimelineProps {
  events: Event[];
  labels?: Label[];
  mode: Mode;
  /** Called with both the shareable seq and the array index needed by deriveScene. */
  onSeqChange?: (seq: number, index: number) => void;
}

const EMPTY_LABELS: Label[] = [];
const markerColor: Record<MarkerKind, string> = {
  kill: "#b42318",
  vote: "#175cd3",
  lie: "#9333aa",
};

export default function Timeline({ events, labels = EMPTY_LABELS, mode, onSeqChange }: TimelineProps) {
  const [seq, setSeq] = useSeqParam(events);
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

  useEffect(() => {
    if (seq !== null && index >= 0) onSeqChange?.(seq, index);
  }, [seq, index, onSeqChange]);

  useEffect(() => {
    if (!playing || seq === null || last === undefined) return;
    if (seq === last) {
      setPlaying(false);
      return;
    }
    const timer = window.setInterval(() => {
      const next = stepSeq(events, seq, 1);
      if (next !== null) setSeq(next);
    }, 1000 / speed);
    return () => window.clearInterval(timer);
  }, [events, last, playing, seq, setSeq, speed]);

  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    const tag = (event.target as HTMLElement).tagName;
    if (tag === "SELECT" || (tag === "BUTTON" && event.key === " ")) return;
    let next: number | null | undefined;
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
    <section aria-label="Game timeline" onKeyDown={onKeyDown} tabIndex={0}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <button type="button" onClick={togglePlaying} aria-label={playing ? "Pause playback" : "Play playback"}>
          {playing ? "Pause" : "Play"}
        </button>
        <label>
          Speed{" "}
          <select value={speed} onChange={(event) => setSpeed(Number(event.target.value))}>
            <option value={0.5}>0.5×</option>
            <option value={1}>1×</option>
            <option value={2}>2×</option>
            <option value={4}>4×</option>
          </select>
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
            style={{ display: "block", width: "100%" }}
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
                style={{ color: markerColor[marker.kind], marginLeft: 4 }}
              >
                {marker.kind === "kill" ? "✝" : marker.kind === "vote" ? "●" : "!"}
              </button>
            ))}
          </div>
        ))}
      </nav>
    </section>
  );
}
