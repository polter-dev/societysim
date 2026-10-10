import type { Mode } from "../../scene/deriveScene";

interface ModeToggleProps {
  mode: Mode;
  onChange: (mode: Mode) => void;
}

export function ModeToggle({ mode, onChange }: ModeToggleProps) {
  return (
    <label>
      View{" "}
      <select value={mode} onChange={(event) => onChange(event.target.value as Mode)}>
        <option value="narrator">Narrator</option>
        <option value="villager">Villager</option>
      </select>
    </label>
  );
}
