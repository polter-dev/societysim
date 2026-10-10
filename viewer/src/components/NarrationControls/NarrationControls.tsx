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
  enabled: Enabled;
  onToggle: (on: boolean) => void;
  onCategory: (c: Category, v: boolean) => void;
}

/** Unstyled narration switch plus category checkboxes. Categories a mode may not play are hidden. */
export function NarrationControls({ mode, on, enabled, onToggle, onCategory }: Props) {
  return (
    <fieldset className="narration-controls">
      <legend>Narration</legend>
      <label>
        <input type="checkbox" checked={on} onChange={(e) => onToggle(e.target.checked)} />
        Narration on
      </label>
      {allowedCategories(mode).map((c) => (
        <label key={c}>
          <input
            type="checkbox"
            checked={enabled[c]}
            disabled={!on}
            onChange={(e) => onCategory(c, e.target.checked)}
          />
          {LABELS[c]}
        </label>
      ))}
    </fieldset>
  );
}
