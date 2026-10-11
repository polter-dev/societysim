import type { Mode } from "../../scene/deriveScene";
import { ThemedSelect } from "../ThemedSelect/ThemedSelect";

interface ModeToggleProps {
  mode: Mode;
  onChange: (mode: Mode) => void;
}

const MODES = [
  { value: "narrator", label: "Narrator" },
  { value: "villager", label: "Villager" },
];

export function ModeToggle({ mode, onChange }: ModeToggleProps) {
  return (
    <label>
      View{" "}
      <ThemedSelect className="themed-select--field" label="View" value={mode} options={MODES} onChange={(v) => onChange(v as Mode)} />
    </label>
  );
}
