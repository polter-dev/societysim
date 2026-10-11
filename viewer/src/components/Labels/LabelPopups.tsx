import type { CSSProperties, ReactNode } from "react";
import type { Label, LabelSource } from "../../data/labels";
import type { Mode, Scene } from "../../scene/deriveScene";
import { seatName } from "../../scene/seatName";
import { seatPosition } from "../Table/Table";

/**
 * Labels to show at this scene: the ones whose source_seq is the current event, or anywhere inside
 * `range` when the stepper passes the current beat's first and last seq (a beat spans several events,
 * and the scene sits on its last one). Filtered by source.
 * Villager mode shows none. Lie labels name hidden roles ("Finn claimed Seer; he is a wolf"), and
 * a label's detail can't be split into public and private parts, so we hide them all.
 * ponytail: could show a label once its truth is public (role revealed at death/game end); not worth it yet.
 */
export function visibleLabels(
  labels: Label[],
  scene: Scene,
  mode: Mode,
  sources?: LabelSource[],
  range?: SeqRange,
): Label[] {
  if (mode !== "narrator") return [];
  const inScene = (seq: number) => (range ? seq >= range.first && seq <= range.last : seq === scene.seq);
  return labels.filter((l) => inScene(l.source_seq) && (!sources || sources.includes(l.source)));
}

/** Inclusive seq range of the stepper's current beat. */
export interface SeqRange {
  first: number;
  last: number;
}

/** Past this many labels of one type in a beat they share one pop-up, so a vote beat stays readable. */
export const MAX_SEPARATE_LABELS = 2;

/** Group labels by type; a type with more than MAX_SEPARATE_LABELS becomes one group, the rest stay single. */
export function groupLabels(labels: Label[]): Label[][] {
  const byType = new Map<string, Label[]>();
  for (const l of labels) byType.set(l.label, [...(byType.get(l.label) ?? []), l]);
  return [...byType.values()].flatMap((g) => (g.length > MAX_SEPARATE_LABELS ? [g] : g.map((l) => [l])));
}

const pretty = (s: string) => s.replace(/_/g, " ");

/**
 * Wraps the Table and overlays one pop-up per label, anchored at the actor's seat position.
 * The wrapper has the same box as the Table (width 100%, max 640), so seatPosition percentages line up.
 */
export function LabelPopups({
  scene,
  labels,
  mode,
  playing = false,
  sources,
  range,
  children,
}: {
  scene: Scene;
  labels: Label[];
  mode: Mode;
  /** Autoplay is running: drop the live region so popups aren't announced. */
  playing?: boolean;
  sources?: LabelSource[];
  /** The current beat's seq range; without it only labels on the shown event pop up. */
  range?: SeqRange;
  children?: ReactNode;
}) {
  const shown = visibleLabels(labels, scene, mode, sources, range);
  const n = scene.players.length;
  const perAnchor = new Map<string, number>(); // stack popups that share a spot
  return (
    <div style={{ position: "relative", width: "100%", maxWidth: 640 }}>
      {children}
      {/* Always mounted, even empty: screen readers miss live regions that appear with their content. */}
      <div aria-live={playing ? "off" : "polite"} style={{ position: "absolute", inset: 0, pointerEvents: "none" }}>
        {groupLabels(shown).map((group, k) => {
          const l = group[0];
          const many = group.length > 1;
          // A group has no single seat; it sits at the table center.
          const anchor = many ? "" : l.actor;
          const stack = perAnchor.get(anchor) ?? 0;
          perAnchor.set(anchor, stack + 1);
          const i = many ? -1 : scene.players.findIndex((p) => p.id === l.actor);
          const { x, y } = i < 0 ? { x: 50, y: 50 } : seatPosition(i, n);
          // Pop toward the table center so it stays inside the box; stack same-spot popups.
          const style: CSSProperties = {
            position: "absolute",
            left: `${x}%`,
            top: `${y}%`,
            transform: `translate(-50%, ${y < 50 ? "35%" : "-135%"}) translateY(${stack * 8}px)`,
            width: "15em",
            padding: "0.5em 0.7em",
            borderRadius: 8,
            background: "#fffbe6",
            color: "#1c1c1c",
            border: "2px solid #c2410c",
            boxShadow: "0 2px 8px rgba(0,0,0,.35)",
            fontSize: "0.8em",
            lineHeight: 1.3,
            pointerEvents: "auto",
          };
          const lowest = Math.min(...group.map((g) => g.confidence));
          return (
            <aside
              key={`${l.label}-${l.source}-${l.actor}-${k}`}
              role="note"
              data-label-for={group.map((g) => g.actor).join(" ")}
              data-label-type={l.label}
              data-label-source={l.source}
              data-label-count={many ? group.length : undefined}
              style={style}
            >
              <strong>{pretty(l.label)}</strong>
              {many ? ` ×${group.length}` : ""} · {group.map((g) => seatName(scene.players, g.actor)).join(", ")}
              <div>
                {pretty(l.source)} · {Math.round(lowest * 100)}% confidence
              </div>
              {many ? (
                <details>
                  <summary>Details</summary>
                  <ul style={{ margin: 0, paddingLeft: "1.2em" }}>
                    {group.map((g, j) => (
                      <li key={j}>{g.detail}</li>
                    ))}
                  </ul>
                </details>
              ) : (
                <div>{l.detail}</div>
              )}
            </aside>
          );
        })}
      </div>
    </div>
  );
}
