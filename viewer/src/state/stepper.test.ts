import { describe, expect, it } from "vitest";
import fixture from "../__fixtures__/modes_game.jsonl?raw";
import { parseLog } from "../data/parse";
import { atLatest, buildBeats, eventIdx, initStepper, newCount, parseStep, stepperReducer, withStep } from "./stepper";

const events = parseLog(fixture).events;

describe("buildBeats", () => {
  const beats = buildBeats(events);
  it("groups the fixture, ids anchored on first seq", () => {
    expect(beats.map((b) => [b.id, b.kind])).toEqual([
      [0, "start"],
      [1, "phase"],
      [6, "night_kill"],
      [8, "death"],
      [9, "phase"],
      [10, "statement"],
      [11, "statement"],
      [12, "statement"],
      [13, "statement"],
      [17, "phase"],
      [18, "vote_result"],
      [25, "phase"],
      [28, "night_kill"],
      [30, "death"],
    ]);
  });
  it("exposes the seq range of each beat", () => {
    expect(beats.find((b) => b.id === 18)).toMatchObject({ firstSeq: 18, lastSeq: 24 });
    expect(beats.find((b) => b.id === 13)).toMatchObject({ firstSeq: 13, lastSeq: 16 });
    expect(beats[0]).toMatchObject({ firstSeq: 0, lastSeq: 0 });
  });
  it("folds the elimination into the vote beat and chat into its neighbour", () => {
    expect(beats.find((b) => b.id === 18)?.endIdx).toBe(24);
    expect(beats.find((b) => b.id === 1)?.endIdx).toBe(5);
    expect(beats.find((b) => b.id === 13)?.endIdx).toBe(16);
  });
});

describe("stepperReducer", () => {
  it("starts at the tip and steps with clamping", () => {
    let s = initStepper(events);
    expect(atLatest(s)).toBe(true);
    s = stepperReducer(s, { type: "next" });
    expect(s.pos).toBe(s.beats.length - 1);
    s = stepperReducer(s, { type: "first" });
    s = stepperReducer(s, { type: "prev" });
    expect(s.pos).toBe(0);
    expect(eventIdx(s)).toBe(0);
    s = stepperReducer(s, { type: "last" });
    expect(eventIdx(s)).toBe(30);
  });
  it("goto accepts a beat id or any seq inside the beat", () => {
    const s = initStepper(events);
    expect(stepperReducer(s, { type: "goto", seq: 18 }).pos).toBe(10);
    expect(stepperReducer(s, { type: "goto", seq: 22 }).pos).toBe(10);
    expect(stepperReducer(s, { type: "goto", seq: 999 }).pos).toBe(s.beats.length - 1);
  });
  it("live append: follows at the tip, stays put and counts new when stepped back", () => {
    let s = initStepper(events.slice(0, 18), { live: true });
    const tip = s.pos;
    s = stepperReducer(s, { type: "append", events: events.slice(18, 19) });
    expect(s.pos).toBe(s.beats.length - 1);
    expect(s.pos).toBeGreaterThan(tip);
    s = stepperReducer(s, { type: "prev" });
    const held = s.beats[s.pos].id;
    s = stepperReducer(s, { type: "append", events: events.slice(19) });
    expect(s.beats[s.pos].id).toBe(held);
    expect(atLatest(s)).toBe(false);
    expect(newCount(s)).toBe(s.beats.length - 1 - s.pos);
    expect(newCount(s)).toBeGreaterThan(1);
    s = stepperReducer(s, { type: "last" });
    expect(newCount(s)).toBe(0);
  });
  it("initStepper honours a ?step seq", () => {
    expect(initStepper(events, { seq: 10 }).pos).toBe(5);
  });
});

describe("URL step", () => {
  it("parses", () => {
    expect(parseStep("?step=12")).toBe(12);
    expect(parseStep("?a=1&step=0")).toBe(0);
    expect(parseStep("")).toBeNull();
    expect(parseStep("?step=-1")).toBeNull();
    expect(parseStep("?step=abc")).toBeNull();
  });
  it("writes and removes, keeping other params", () => {
    expect(withStep("?game=a", 7)).toBe("?game=a&step=7");
    expect(withStep("?game=a&step=3", 7)).toBe("?game=a&step=7");
    expect(withStep("?step=3", null)).toBe("");
  });
});
