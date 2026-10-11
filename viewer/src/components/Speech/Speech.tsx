import type { CSSProperties, ReactNode } from "react";
import type { Event } from "../../types/events";
import type { Scene } from "../../scene/deriveScene";
import { seatPosition } from "../Table/Table";

/**
 * A statement's text, with fallbacks and truncation marked.
 * Model text is only ever a React text child (escaped), never HTML.
 */
export function SpeechText({ e }: { e: Event }) {
  if (e.meta.fallback)
    return (
      <em data-fallback title="No valid reply; the engine logged a fallback">
        {e.public_text ?? "(silent)"} <small>no valid reply</small>
      </em>
    );
  return (
    <>
      {e.public_text}
      {e.meta.truncated && (
        <>
          … <small data-truncated title="Cut at the word cap">(truncated)</small>
        </>
      )}
    </>
  );
}

/** The statement the bubble shows: the last one in the log, while its speaker holds the floor. */
export function currentStatement(scene: Scene): Event | null {
  if (!scene.speaker) return null;
  return [...scene.log].reverse().find((e) => e.type === "statement" && e.actor === scene.speaker) ?? null;
}

/**
 * Speech bubble overlay. Place it in a `position: relative` box the same size as the Table:
 * it fills that box and anchors the bubble between the speaker's seat and the table center.
 */
export function Speech({ scene, playing = false }: { scene: Scene; playing?: boolean }) {
  const e = currentStatement(scene);
  const i = scene.players.findIndex((p) => p.id === e?.actor);
  // The live region stays mounted while empty; screen readers miss regions that appear with their content.
  const live = (bubble?: ReactNode) => (
    <div aria-live={playing ? "off" : "polite"} style={{ position: "absolute", inset: 0, pointerEvents: "none" }}>
      {bubble}
    </div>
  );
  if (!e || i < 0) return live();
  const p = scene.players[i];
  const seat = seatPosition(i, scene.players.length);
  // Anchored halfway to the center, the bubble grows from there toward the center (away from the
  // speaker), so it stays on the felt and off the speaker's own name plate.
  const dx = seat.x - 50;
  const dy = seat.y - 50;
  const len = Math.hypot(dx, dy) || 1;
  const shift = (d: number) => Math.round(-50 - (50 * d) / len);
  // Sizes in cqw follow the table box (the Table's container), so the bubble scales with the table.
  const style: CSSProperties = {
    position: "absolute",
    left: `${50 + dx / 2}%`,
    top: `${50 + dy / 2}%`,
    transform: `translate(${shift(dx)}%, ${shift(dy)}%)`,
    maxWidth: "30cqw",
    maxHeight: "21cqw",
    overflowY: "auto",
    padding: "0.5em 0.75em",
    borderRadius: 12,
    border: `2px solid ${p.color}`,
    background: "#fffdf7",
    color: "#1c1c1c",
    fontSize: "max(12px, 1.6cqw)",
    lineHeight: 1.35,
    boxShadow: "0 2px 8px rgba(0,0,0,0.25)",
    pointerEvents: "auto",
  };
  return live(
    <p data-speaker={p.id} data-seq={e.seq} style={style}>
      <strong>{p.name}:</strong> <SpeechText e={e} />
    </p>,
  );
}
