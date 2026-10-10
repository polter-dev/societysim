import { deriveScene, type Mode } from "../../scene/deriveScene";
import type { Event, Label, LabelType, Phase } from "../../types/events";

export type MarkerKind = "kill" | "vote" | "lie";

export interface TimelineMarker {
  seq: number;
  kind: MarkerKind;
  title: string;
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
      group.markers.push({ seq: event.seq, kind: "kill", title: `${event.type === "elimination" ? "Elimination" : "Death"}: ${event.target}` });
    } else if (event.type === "vote") {
      group.markers.push({ seq: event.seq, kind: "vote", title: `Vote: ${event.actor ?? "unknown"} → ${event.target ?? "abstain"}` });
    }
  }

  if (mode === "narrator") {
    for (const label of labels) {
      const event = bySeq.get(label.source_seq);
      if (!event || event.game_id !== label.game_id) continue;
      byKey.get(`${event.day}:${event.phase}`)?.markers.push({
        seq: event.seq,
        kind: "lie",
        title: `Lie: ${label.actor} (${label.label.replaceAll("_", " ")})`,
        labelType: label.label,
      });
    }
  }
  for (const group of groups) group.markers.sort((a, b) => a.seq - b.seq);
  return groups;
}
