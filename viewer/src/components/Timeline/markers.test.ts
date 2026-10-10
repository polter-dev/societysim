import { describe, expect, it } from "vitest";
import fixture from "../../../../fixtures/sample_game.jsonl?raw";
import labelFixture from "../../../../fixtures/sample_game.labels.jsonl?raw";
import { parseLog } from "../../data/parse";
import type { Label, LabelType } from "../../types/events";
import { buildTimelineGroups, MARKER_APPEARANCE } from "./markers";

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
    expect(lieMarkers.find((marker) => marker.labelType === "mis_elimination")?.title).toBe("Villager voted out: vote by p1");
    expect(villager.some((marker) => marker.kind === "lie")).toBe(false);
    expect(buildTimelineGroups(events, [], "narrator").some((group) => group.markers.some((marker) => marker.kind === "lie"))).toBe(false);
  });

  it("gives every label a distinct glyph and color and names mis-eliminations accurately", () => {
    const types: LabelType[] = ["false_role_claim", "false_report", "bussing", "hidden_knowledge", "mis_elimination"];
    expect(types.map((type) => MARKER_APPEARANCE[type].glyph)).toEqual(["R", "S", "B", "K", "X"]);
    expect(new Set(types.map((type) => MARKER_APPEARANCE[type].color)).size).toBe(types.length);

    const markers = buildTimelineGroups(events, labels, "narrator")
      .flatMap((group) => group.markers)
      .filter((marker) => marker.kind === "lie");
    for (const type of types) {
      const marker = markers.find((item) => item.labelType === type);
      expect(marker?.appearance).toEqual(MARKER_APPEARANCE[type]);
    }
    const misEliminations = markers.filter((marker) => marker.labelType === "mis_elimination");
    expect(misEliminations.length).toBeGreaterThan(0);
    for (const marker of misEliminations) {
      expect(marker.title).toContain("Villager voted out");
      expect(marker.title).not.toContain("Lie");
    }
  });

  it("does not expose hidden events as villager markers or groups", () => {
    const publicGroups = buildTimelineGroups(events, labels, "villager");
    expect(publicGroups.flatMap((group) => group.markers).some((marker) => marker.seq === 11)).toBe(false);
    expect(publicGroups.every((group) => group.startSeq !== 0)).toBe(true);
    const publicMarkers = publicGroups.flatMap((group) => group.markers);
    expect(publicMarkers.find((marker) => marker.kind === "kill")?.appearance).toEqual({ glyph: "✝", color: "#b42318" });
    expect(publicMarkers.find((marker) => marker.kind === "vote")?.appearance).toEqual({ glyph: "●", color: "#175cd3" });
  });
});
