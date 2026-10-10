import type { CSSProperties } from "react";
import type { Phase } from "../../types/events";
import type { PlayerView, Scene } from "../../scene/deriveScene";

/**
 * Seat i of n on the table, as percentages of the table box (0-100), seat 0 at the top, clockwise.
 * Exported so arrows and speech bubbles can anchor to the same spots.
 */
export function seatPosition(i: number, n: number): { x: number; y: number } {
  const a = (2 * Math.PI * i) / n - Math.PI / 2;
  return { x: 50 + 40 * Math.cos(a), y: 50 + 40 * Math.sin(a) };
}

/** The game opens at night, so setup shares the night tint. */
export const tintFor = (phase: Phase): "day" | "night" =>
  phase === "day" || phase === "vote" ? "day" : "night";

// ponytail: hard-coded colors until the T4.0 design tokens land.
const TINTS = {
  day: { background: "#f4efe1", color: "#1c1c1c", felt: "#d9cfb0" },
  night: { background: "#151b2b", color: "#e8ecf4", felt: "#26304a" },
};

const SR_ONLY: CSSProperties = {
  position: "absolute",
  width: 1,
  height: 1,
  overflow: "hidden",
  clip: "rect(0 0 0 0)",
  whiteSpace: "nowrap",
};

const roleLabel = (role: string) => role[0].toUpperCase() + role.slice(1);

function Seat({ p, i, n }: { p: PlayerView; i: number; n: number }) {
  const { x, y } = seatPosition(i, n);
  const style: CSSProperties = {
    position: "absolute",
    left: `${x}%`,
    top: `${y}%`,
    transform: "translate(-50%, -50%)",
    width: "8.5em",
    padding: "0.4em",
    borderRadius: 8,
    border: `3px solid ${p.color}`,
    background: "inherit",
    textAlign: "center",
    lineHeight: 1.25,
    opacity: p.alive ? 1 : 0.55,
    filter: p.alive ? undefined : "grayscale(1)",
  };
  return (
    <li data-seat={p.id} data-alive={p.alive} data-revealed={p.revealed} style={style}>
      <strong>{p.name}</strong>
      <div style={{ fontSize: "0.8em" }}>
        <span aria-hidden style={{ color: p.color }}>
          ●
        </span>{" "}
        {p.model}
      </div>
      <div
        data-role-card
        style={{
          marginTop: 4,
          fontSize: "0.85em",
          fontWeight: p.revealed ? 700 : 400,
          border: "1px solid currentColor",
          borderRadius: 4,
        }}
      >
        {/* role is null whenever this mode may not know it; never guess. */}
        {p.role ? (
          roleLabel(p.role)
        ) : (
          <>
            <span aria-hidden>?</span>
            <span style={SR_ONLY}>role unknown</span>
          </>
        )}
      </div>
      {!p.alive && <div style={{ fontSize: "0.75em" }}>dead</div>}
    </li>
  );
}

/** The round table. Renders `scene` only; all game state comes from deriveScene. */
export function Table({ scene }: { scene: Scene }) {
  const tint = tintFor(scene.phase);
  const { felt, ...colors } = TINTS[tint];
  return (
    <section
      aria-label={`Table, day ${scene.day}, ${scene.phase}`}
      data-tint={tint}
      style={{ ...colors, position: "relative", width: "100%", maxWidth: 640, aspectRatio: "1", borderRadius: 16 }}
    >
      <div
        aria-hidden
        style={{ position: "absolute", inset: "28%", borderRadius: "50%", background: felt }}
      />
      <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
        {scene.players.map((p, i) => (
          <Seat key={p.id} p={p} i={i} n={scene.players.length} />
        ))}
      </ul>
    </section>
  );
}
