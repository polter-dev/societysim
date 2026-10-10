import { deriveScene, type Mode } from "../../scene/deriveScene";
import type { Event, Label, LabelType, Phase } from "../../types/events";

export type MarkerKind = "kill" | "vote" | "lie";

interface MarkerAppearance {
  glyph: string;
  color: string;
}

export const MARKER_APPEARANCE = {
  kill: { glyph: "✝", color: "#b42318" },
  vote: { glyph: "●", color: "#175cd3" },
  false_role_claim: { glyph: "R", color: "#9333aa" },
  false_report: { glyph: "S", color: "#b54708" },
  bussing: { glyph: "B", color: "#b42318" },
  hidden_knowledge: { glyph: "K", color: "#6941c6" },
  mis_elimination: { glyph: "X", color: "#475467" },
} satisfies Record<"kill" | "vote" | LabelType, MarkerAppearance>;

const LABEL_TITLES: Record<LabelType, string> = {
  false_role_claim: "False role claim",
  false_report: "False report",
  bussing: "Bussing",
  hidden_knowledge: "Hidden knowledge",
  mis_elimination: "Villager voted out",
};

export interface TimelineMarker {
  seq: number;
  kind: MarkerKind;
  title: string;
  appearance: MarkerAppearance;
  labelType?: LabelType;
}

export interface TimelineGroup {
  day: number;
  phase: Phase;
  startSeq: number;
  endSeq: number;
  markers: TimelineMarker[];
}

/** Build navigation markers only from events visible in this view. */
export function buildTimelineGroups(
  events: Event[],
  labels: readonly Label[],
  mode: Mode,
): TimelineGroup[] {
  if (!events.length) return [];
  const visible = deriveScene(events, events.length - 1, mode).log;
  const groups: TimelineGroup[] = [];
  const bySeq = new Map(visible.map((event) => [event.seq, event]));
  const byKey = new Map<string, TimelineGroup>();

  for (const event of visible) {
    const key = `${event.day}:${event.phase}`;
    let group = byKey.get(key);
    if (!group) {
      group = { day: event.day, phase: event.phase, startSeq: event.seq, endSeq: event.seq, markers: [] };
      groups.push(group);
      byKey.set(key, group);
    }
    group.endSeq = event.seq;
    if ((event.type === "death_reveal" || event.type === "elimination") && event.target) {
      group.markers.push({ seq: event.seq, kind: "kill", title: `${event.type === "elimination" ? "Elimination" : "Death"}: ${event.target}`, appearance: MARKER_APPEARANCE.kill });
    } else if (event.type === "vote") {
      group.markers.push({ seq: event.seq, kind: "vote", title: `Vote: ${event.actor ?? "unknown"} → ${event.target ?? "abstain"}`, appearance: MARKER_APPEARANCE.vote });
    }
  }

  if (mode === "narrator") {
    for (const label of labels) {
      const event = bySeq.get(label.source_seq);
      if (!event || event.game_id !== label.game_id) continue;
      byKey.get(`${event.day}:${event.phase}`)?.markers.push({
        seq: event.seq,
        kind: "lie",
        title: label.label === "mis_elimination"
          ? `${LABEL_TITLES.mis_elimination}: vote by ${label.actor}`
          : `${LABEL_TITLES[label.label]}: ${label.actor}`,
        appearance: MARKER_APPEARANCE[label.label],
        labelType: label.label,
      });
    }
  }
  for (const group of groups) group.markers.sort((a, b) => a.seq - b.seq);
  return groups;
}
