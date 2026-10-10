import { describe, expect, it } from "vitest";
import fixture from "../../../../fixtures/sample_game.jsonl?raw";
import labelFixture from "../../../../fixtures/sample_game.labels.jsonl?raw";
import { parseLog } from "../../data/parse";
import type { Label } from "../../types/events";
import { buildTimelineGroups } from "./markers";

const events = parseLog(fixture).events;
const labels = labelFixture.trim().split("\n").map((line) => JSON.parse(line) as Label);

describe("timeline markers", () => {
  it("groups visible events by day and phase", () => {
    const groups = buildTimelineGroups(events, labels, "villager");
    expect(groups.map(({ day, phase }) => [day, phase])).toEqual([
      [1, "night"], [1, "day"], [1, "vote"],
      [2, "night"], [2, "day"], [2, "vote"], [3, "night"],
    ]);
    expect(groups[0].startSeq).toBe(1);
    expect(groups[0].endSeq).toBe(13);
  });

  it("marks actual deaths and eliminations, never a saved kill", () => {
    const kills = buildTimelineGroups(events, labels, "narrator")
      .flatMap((group) => group.markers)
      .filter((marker) => marker.kind === "kill");
    expect(kills.map((marker) => marker.seq)).toEqual([67, 104, 116]);
    expect(kills.some((marker) => marker.seq === 13)).toBe(false);
  });

  it("marks public votes, including abstentions", () => {
    const votes = buildTimelineGroups(events, labels, "villager")
      .flatMap((group) => group.markers)
      .filter((marker) => marker.kind === "vote");
    expect(votes.find((marker) => marker.seq === 50)?.title).toContain("abstain");
    expect(votes.map((marker) => marker.seq)).toContain(103);
  });

  it("shows labeled lies only to the narrator", () => {
    const narrator = buildTimelineGroups(events, labels, "narrator").flatMap((group) => group.markers);
    const villager = buildTimelineGroups(events, labels, "villager").flatMap((group) => group.markers);
    const lieMarkers = narrator.filter((marker) => marker.kind === "lie");
    expect(lieMarkers.map(({ seq, labelType }) => [seq, labelType])).toEqual([
      [16, "hidden_knowledge"],
      [19, "false_role_claim"],
      [20, "false_report"],
      [70, "bussing"],
      [98, "mis_elimination"],
      [99, "mis_elimination"],
      [101, "mis_elimination"],
      [102, "mis_elimination"],
    ]);
    expect(lieMarkers.some((marker) => marker.seq === 100)).toBe(false);
    for (const marker of lieMarkers) {
      expect(marker.title).toContain(marker.labelType?.replaceAll("_", " "));
    }
    expect(villager.some((marker) => marker.kind === "lie")).toBe(false);
    expect(buildTimelineGroups(events, [], "narrator").some((group) => group.markers.some((marker) => marker.kind === "lie"))).toBe(false);
  });

  it("does not expose hidden events as villager markers or groups", () => {
    const publicGroups = buildTimelineGroups(events, labels, "villager");
    expect(publicGroups.flatMap((group) => group.markers).some((marker) => marker.seq === 11)).toBe(false);
    expect(publicGroups.every((group) => group.startSeq !== 0)).toBe(true);
  });
});
