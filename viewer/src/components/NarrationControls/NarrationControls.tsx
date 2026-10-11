import { allowedCategories, type Category, type Enabled } from "../../audio/narration";
import type { Mode } from "../../scene/deriveScene";

const LABELS: Record<Category, string> = {
  narrator: "Narrator events",
  speech: "Speeches",
  wolf_chat: "Wolf chat (narrator view only)",
  highlight: "Highlights (narrator view only)",
};

interface Props {
  mode: Mode;
  on: boolean;
  available?: boolean;
  enabled: Enabled;
  onToggle: (on: boolean) => void;
  onCategory: (c: Category, v: boolean) => void;
}

/** Narration switch plus category chips (styled in controls.css). Categories a mode may not play are hidden. */
export function NarrationControls({ mode, on, enabled, onToggle, onCategory, available = true }: Props) {
  return (
    <fieldset className="narration-controls">
      <legend>Narration</legend>
      <label className="narration-switch">
        <input type="checkbox" role="switch" checked={on} disabled={!available} onChange={(e) => onToggle(e.target.checked)} />
        Narration on
      </label>
      {!available && <small>Audio is not available for this game yet.</small>}
      {allowedCategories(mode).map((c) => (
        <label key={c}>
          <input
            type="checkbox"
            checked={enabled[c]}
            disabled={!on || !available}
            onChange={(e) => onCategory(c, e.target.checked)}
          />
          {LABELS[c]}
        </label>
      ))}
    </fieldset>
  );
}
