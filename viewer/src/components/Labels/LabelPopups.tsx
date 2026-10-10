import type { CSSProperties, ReactNode } from "react";
import type { Label, LabelSource } from "../../data/labels";
import type { Mode, Scene } from "../../scene/deriveScene";
import { seatPosition } from "../Table/Table";

/**
 * Labels to show at this scene: the ones whose source_seq is the current event, filtered by source.
 * Villager mode shows none. Lie labels name hidden roles ("Finn claimed Seer; he is a wolf"), and
 * a label's detail can't be split into public and private parts, so we hide them all.
 * ponytail: could show a label once its truth is public (role revealed at death/game end); not worth it yet.
 */
export function visibleLabels(
  labels: Label[],
  scene: Scene,
  mode: Mode,
  sources?: LabelSource[],
): Label[] {
  if (mode !== "narrator") return [];
  return labels.filter(
    (l) => l.source_seq === scene.seq && (!sources || sources.includes(l.source)),
  );
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
  sources,
  children,
}: {
  scene: Scene;
  labels: Label[];
  mode: Mode;
  sources?: LabelSource[];
  children?: ReactNode;
}) {
  const shown = visibleLabels(labels, scene, mode, sources);
  const n = scene.players.length;
  return (
    <div style={{ position: "relative", width: "100%", maxWidth: 640 }}>
      {children}
      {shown.length > 0 && (
        <div aria-live="polite" style={{ position: "absolute", inset: 0, pointerEvents: "none" }}>
          {shown.map((l, k) => {
            const i = scene.players.findIndex((p) => p.id === l.actor);
            const { x, y } = i < 0 ? { x: 50, y: 50 } : seatPosition(i, n);
            // Pop toward the table center so it stays inside the box; stack same-seat popups.
            const style: CSSProperties = {
              position: "absolute",
              left: `${x}%`,
              top: `${y}%`,
              transform: `translate(-50%, ${y < 50 ? "35%" : "-135%"}) translateY(${k * 8}px)`,
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
            return (
              <aside
                key={`${l.label}-${l.source}-${l.actor}-${k}`}
                role="note"
                data-label-for={l.actor}
                data-label-type={l.label}
                data-label-source={l.source}
                style={style}
              >
                <strong>{pretty(l.label)}</strong> · {l.actor}
                <div>
                  {pretty(l.source)} · {Math.round(l.confidence * 100)}% confidence
                </div>
                <div>{l.detail}</div>
              </aside>
            );
          })}
        </div>
      )}
    </div>
  );
}
