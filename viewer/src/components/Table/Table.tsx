import type { Phase } from "../../types/events";
import type { PlayerView, Scene } from "../../scene/deriveScene";
import { AvatarMedallion } from "../AvatarMedallion/AvatarMedallion";
import { useModelLabel } from "../ModelAvatar/ModelAvatar";
import { modelName } from "../../data/modelNames";
import "./Table.css";

/**
 * The chair ring in the table artwork (illustrated/assets/new/table2.png, 1672 x 941), measured in
 * image pixels: ten chairs evenly spaced on an ellipse centered at (836, 472) with radii 307 x 262,
 * chair 0 at the top, clockwise. The table box is a square of TABLE_BOX image pixels centered on the
 * image center, so these percentages scale with the background at any viewport size (Table.css).
 */
export const TABLE_BOX = 800;
const ART_CENTER_Y = 941 / 2;
const RING = { cy: 472, rx: 307, ry: 262 };
export const CHAIRS = 10;

/**
 * Seat i of n on the table, as percentages of the table box (0-100), seat 0 at the top, clockwise.
 * Up to ten players sit in the artwork's chairs, spread out so the empty chairs fall between them.
 * Exported so arrows and speech bubbles can anchor to the same spots.
 */
export function seatPosition(i: number, n: number): { x: number; y: number } {
  const turn = n <= CHAIRS ? Math.round((i * CHAIRS) / n) / CHAIRS : i / n;
  const a = 2 * Math.PI * turn - Math.PI / 2;
  return {
    x: 50 + (100 * RING.rx * Math.cos(a)) / TABLE_BOX,
    y: 50 + (100 * (RING.cy - ART_CENTER_Y + RING.ry * Math.sin(a))) / TABLE_BOX,
  };
}

/** The game opens at night, so setup shares the night tint. */
export const tintFor = (phase: Phase): "day" | "night" =>
  phase === "day" || phase === "vote" ? "day" : "night";

const roleLabel = (role: string) => role[0].toUpperCase() + role.slice(1);

/**
 * "Seat 3, gemini-3.5-flash-lite, eliminated, villager": the role only when this mode shows it.
 * Screen readers get this name for the seat and its Agents-panel button; the medallion inside is decorative.
 */
export const seatLabel = (p: PlayerView, model: string, speaking = false) =>
  [p.name, model, p.alive ? "alive" : "eliminated", p.role ?? "", speaking ? "speaking" : ""].filter(Boolean).join(", ");

interface SeatProps {
  p: PlayerView;
  i: number;
  n: number;
  model: string;
  selected: boolean;
  speaking: boolean;
  onSelect?: (seat: string) => void;
}

function Seat({ p, i, n, model, selected, speaking, onSelect }: SeatProps) {
  const { x, y } = seatPosition(i, n);
  return (
    <li
      className="table-seat"
      data-seat={p.id}
      data-alive={p.alive}
      data-revealed={p.revealed}
      data-speaking={speaking || undefined}
      data-plate={y < 50 ? "above" : "below"}
      style={{ left: `${x}%`, top: `${y}%` }}
    >
      <button type="button" aria-pressed={selected} aria-haspopup="dialog" aria-label={seatLabel(p, model, speaking)} data-testid="seat-button" onClick={() => onSelect?.(p.id)}>
        <AvatarMedallion model={p.model} label={model} alive={p.alive} size={96} />
        <span className="seat-plate">
          <span className="seat-name">
            <strong>{p.name}</strong>
            {/* role is null whenever this mode may not know it; never guess. */}
            <span className="seat-role" data-role-card>
              {p.role ? roleLabel(p.role) : "?"}
            </span>
          </span>
          <span className="seat-model" title={model}>
            {modelName(p.model)}
          </span>
        </span>
      </button>
    </li>
  );
}

/** The round table. Renders `scene` only; all game state comes from deriveScene. */
export function Table({
  scene,
  selected = null,
  onSelect,
}: {
  scene: Scene;
  /** The seat open in the Inspector, shown pressed. */
  selected?: string | null;
  onSelect?: (seat: string) => void;
}) {
  // The plate shows the short name (modelNames.ts); the full catalog label is its title and the seat's name.
  const model = useModelLabel();
  return (
    <section className="round-table" aria-label={`Table, day ${scene.day}, ${scene.phase}`} data-tint={tintFor(scene.phase)}>
      <ul>
        {scene.players.map((p, i) => (
          <Seat
            key={p.id}
            p={p}
            i={i}
            n={scene.players.length}
            model={model(p.model)}
            selected={p.id === selected}
            speaking={p.id === scene.speaker}
            onSelect={onSelect}
          />
        ))}
      </ul>
    </section>
  );
}
