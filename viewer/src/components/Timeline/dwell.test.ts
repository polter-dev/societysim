import { describe, expect, it } from "vitest";
import fixture from "../../../../fixtures/sample_game.jsonl?raw";
import { parseLog } from "../../data/parse";
import type { Event } from "../../types/events";
import { dwellMs, stepDelayMs } from "./dwell";

const base = parseLog(fixture).events[0]!;
const ev = (over: Partial<Event>): Event => ({ ...base, type: "statement", visibility: "public", public_text: null, private_reasoning: null, ...over });
const words = (n: number) => Array.from({ length: n }, () => "word").join(" ");

describe("dwellMs", () => {
  it("gives a 40-word statement 1500 + 250 per word", () => {
    expect(dwellMs(ev({ public_text: words(40) }), "villager")).toBe(11500);
    expect(dwellMs(ev({ public_text: words(10) }), "villager")).toBe(4000);
  });
  it("keeps 1000 ms for events with no text, such as a vote", () => {
    expect(dwellMs(ev({ type: "vote", target: "p1" }), "villager")).toBe(1000);
  });
  it("clamps long text to 12000 ms and short text to 2000 ms", () => {
    expect(dwellMs(ev({ public_text: words(500) }), "narrator")).toBe(12000);
    expect(dwellMs(ev({ public_text: "ok" }), "narrator")).toBe(2000);
  });
  it("ignores private_reasoning length", () => {
    const a = ev({ public_text: words(5), private_reasoning: "x" });
    const b = ev({ public_text: words(5), private_reasoning: words(400) });
    expect(dwellMs(a, "narrator")).toBe(dwellMs(b, "narrator"));
    expect(dwellMs(ev({ type: "vote", private_reasoning: words(400) }), "narrator")).toBe(1000);
  });
  it("does not count text the viewer cannot see in villager mode", () => {
    const wolf = ev({ type: "wolf_chat", visibility: "wolves", public_text: words(40) });
    expect(dwellMs(wolf, "villager")).toBe(1000);
    expect(dwellMs(wolf, "narrator")).toBe(11500);
  });
  it("divides by the speed multiplier", () => {
    const e = ev({ public_text: words(10) });
    expect(stepDelayMs(e, "villager", 1)).toBe(4000);
    expect(stepDelayMs(e, "villager", 4)).toBe(1000);
    expect(stepDelayMs(e, "villager", 0.5)).toBe(8000);
  });
});
