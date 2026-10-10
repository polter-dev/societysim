import { seatPosition } from "../Table/Table";
import type { Scene } from "../../scene/deriveScene";

interface Arrow {
  actor: string;
  target: string;
  count: number;
  kind: "accusation" | "vote";
}

/** An SVG layer sized to the same square box as Table. Place it inside that box. */
export function Arrows({ scene }: { scene: Scene }) {
  const players = scene.players;
  const index = new Map(players.map((p, i) => [p.id, i]));
  const name = (id: string) => players[index.get(id) ?? -1]?.name ?? id;
  const resultVisible = scene.log.some(
    (e) => e.day === scene.day && (e.type === "elimination" || e.type === "no_elimination"),
  );

  const counts = new Map<string, Arrow>();
  for (const accusation of scene.accusations) {
    const key = `${accusation.actor}:${accusation.target}`;
    const arrow = counts.get(key);
    if (arrow) arrow.count++;
    else counts.set(key, { actor: accusation.actor, target: accusation.target, count: 1, kind: "accusation" });
  }
  const arrows = [...counts.values()];
  if (resultVisible) {
    for (const vote of scene.votes) {
      if (vote.target) arrows.push({ actor: vote.actor, target: vote.target, count: 1, kind: "vote" });
    }
  }
  arrows.sort((a, b) => (index.get(a.actor) ?? Infinity) - (index.get(b.actor) ?? Infinity)
    || (index.get(a.target) ?? Infinity) - (index.get(b.target) ?? Infinity));

  return (
    <svg
      role="group"
      aria-label="Accusations and revealed votes"
      viewBox="0 0 100 100"
      preserveAspectRatio="none"
      style={{ position: "absolute", inset: 0, width: "100%", height: "100%", pointerEvents: "none" }}
    >
      {arrows.map((arrow, i) => {
        const from = index.get(arrow.actor);
        const to = index.get(arrow.target);
        if (from === undefined || to === undefined || from === to) return null;
        const a = seatPosition(from, players.length);
        const b = seatPosition(to, players.length);
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const length = Math.hypot(dx, dy);
        const ux = dx / length;
        const uy = dy / length;
        const start = { x: a.x + ux * 7, y: a.y + uy * 7 };
        const end = { x: b.x - ux * 8, y: b.y - uy * 8 };
        const color = arrow.kind === "vote" ? "#2456aa" : "#a54435";
        const width = arrow.kind === "vote" ? 1.1 : Math.min(0.65 + 0.4 * (arrow.count - 1), 2.5);
        const wing = 1.8 + width;
        const points = [
          `${end.x},${end.y}`,
          `${end.x - ux * 3 + -uy * wing},${end.y - uy * 3 + ux * wing}`,
          `${end.x - ux * 3 - -uy * wing},${end.y - uy * 3 - ux * wing}`,
        ].join(" ");
        const label = `${name(arrow.actor)} ${arrow.kind === "vote" ? "voted for" : "accused"} ${name(arrow.target)}${arrow.count > 1 ? ` ${arrow.count} times` : ""}`;
        return (
          <g key={`${arrow.kind}:${arrow.actor}:${arrow.target}:${i}`} role="img" aria-label={label} data-kind={arrow.kind} data-count={arrow.count}>
            <line x1={start.x} y1={start.y} x2={end.x} y2={end.y} stroke={color} strokeWidth={width} />
            <polygon points={points} fill={color} />
          </g>
        );
      })}
      {resultVisible && scene.votes.filter((vote) => vote.target === null).map((vote) => {
        const seat = index.get(vote.actor);
        if (seat === undefined) return null;
        const { x, y } = seatPosition(seat, players.length);
        return (
          <g key={`abstain:${vote.seq}`} role="img" aria-label={`${name(vote.actor)} ${vote.fallback ? "was forced to abstain" : "abstained"}`} data-kind="abstain" data-fallback={vote.fallback}>
            <circle cx={x + 6} cy={y + 6} r="3" fill={vote.fallback ? "#a54435" : "#555"} />
            <text x={x + 6} y={y + 7.4} textAnchor="middle" fontSize="4" fill="white">×</text>
          </g>
        );
      })}
    </svg>
  );
}
