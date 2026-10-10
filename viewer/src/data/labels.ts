/** One line of <game_id>.labels.jsonl (mirrors Label in logging/events.py). */
export type LabelType =
  | "false_role_claim"
  | "false_report"
  | "bussing"
  | "hidden_knowledge"
  | "mis_elimination";
export type LabelSource = "self_tag" | "jev" | "engine";

export interface Label {
  schema_version: number;
  game_id: string;
  label: LabelType;
  actor: string; // seat id
  source_seq: number; // seq of the event the label is about
  source: LabelSource;
  confidence: number;
  detail: string;
}

/** Parse a labels.jsonl body. Blank lines are skipped; a malformed line throws with its line number. */
export function parseLabels(text: string): Label[] {
  const out: Label[] = [];
  text.split("\n").forEach((line, i) => {
    if (!line.trim()) return;
    let l: Label;
    try {
      l = JSON.parse(line);
    } catch {
      throw new Error(`labels line ${i + 1}: not JSON`);
    }
    if (typeof l.actor !== "string" || typeof l.label !== "string" || !Number.isInteger(l.source_seq))
      throw new Error(`labels line ${i + 1}: missing actor, label or source_seq`);
    out.push(l);
  });
  return out;
}

/** `runs/b/g1.jsonl` -> `runs/b/g1.labels.jsonl`. */
export const siblingLabelsUrl = (logUrl: string) => logUrl.replace(/\.jsonl$/, ".labels.jsonl");

/**
 * Fetch labels; a missing file (404, offline, bad body) is not an error, the viewer just shows none.
 * A dev server may answer 200 with index.html for a missing file; that fails parsing and also gives [].
 */
export async function fetchLabels(url: string): Promise<Label[]> {
  try {
    const res = await fetch(url);
    return res.ok ? parseLabels(await res.text()) : [];
  } catch {
    return [];
  }
}
