import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { deriveScene } from "../../scene/deriveScene";
import type { Event } from "../../types/events";
import { Inspector, inspect } from "./Inspector";

const base = { schema_version: 2, game_id: "g", day: 1, phase: "day", visibility: "public", target: null, meta: {} };
const ev = (o: Record<string, unknown> & { seq: number }) =>
  ({ ...base, t: o.seq, type: "statement", actor: null, public_text: null, private_reasoning: null, suspicion: null, ...o }) as unknown as Event;
const seat = (name: string, role: string) => ({ name, model: "m", color: "#000", role });
const events: Event[] = [
  ev({ seq: 0, type: "game_start", phase: "setup", meta: { seats: { p0: seat("Ann", "wolf"), p1: seat("Bo", "villager"), p2: seat("Cy", "seer") } } }),
  ev({ seq: 1, actor: "p0", public_text: "I trust Bo", private_reasoning: "SECRET-A", suspicion: [{ player: "p1", p: 0.1 }, { player: "p2", p: 0.9 }] }),
  ev({ seq: 2, actor: "p0", public_text: "Cy is odd", private_reasoning: "SECRET-B", suspicion: [{ player: "p1", p: 0.2 }, { player: "p2", p: 0.8 }] }),
  ev({ seq: 3, type: "death_reveal", target: "p2", meta: { role: "seer" } }),
];
const html = (idx: number, mode: "narrator" | "villager", selected: string | null = "p0") =>
  renderToStaticMarkup(createElement(Inspector, { scene: deriveScene(events, idx, mode), mode, selected, onSelect: () => {} }));

describe("Inspector", () => {
  it("renders nothing when closed", () => expect(html(2, "narrator", null)).toBe(""));

  it("narrator: public text beside latest reasoning, tracks the timeline", () => {
    expect(html(1, "narrator")).toContain("SECRET-A");
    const h = html(2, "narrator");
    expect(h).toContain("Cy is odd");
    expect(h).toContain("SECRET-B");
    expect(h).not.toContain("SECRET-A");
  });

  it("suspicion covers living others only, latest row", () => {
    expect(inspect(deriveScene(events, 2, "narrator"), "p0").suspicion.map((s) => [s.player, s.p])).toEqual([
      ["p1", 0.2],
      ["p2", 0.8],
    ]);
    expect(inspect(deriveScene(events, 3, "narrator"), "p0").suspicion.map((s) => s.player)).toEqual(["p1"]);
  });

  it("villager mode renders nothing private", () => {
    const h = html(2, "villager");
    expect(h).toContain("Cy is odd");
    expect(h).not.toMatch(/SECRET|Private reasoning|Suspicion|meter|80%/);
  });
});
