import { describe, expect, it } from "vitest";
import fixture from "../../../fixtures/sample_game.jsonl?raw";
import { parseLog } from "./parse";

const line = (over: Record<string, unknown>) =>
  JSON.stringify({
    schema_version: 2,
    game_id: "g",
    seq: 0,
    t: 0,
    day: 0,
    phase: "setup",
    type: "game_start",
    visibility: "narrator",
    actor: null,
    target: null,
    public_text: null,
    private_reasoning: null,
    suspicion: null,
    meta: {},
    ...over,
  });

describe("parseLog", () => {
  it("parses the fixture with no bad lines", () => {
    const { events, errors } = parseLog(fixture);
    expect(errors).toEqual([]);
    expect(events).toHaveLength(118);
    expect(events.map((e) => e.seq)).toEqual([...Array(118).keys()]);
  });

  it("accepts schema_version 1 and 2", () => {
    const text = [line({ schema_version: 1 }), line({ seq: 1 })].join("\n");
    expect(parseLog(text).errors).toEqual([]);
  });

  it("reports bad lines with their 1-based line number and keeps the good ones", () => {
    const text = [
      line({}),
      "{not json",
      line({ seq: 1, actor: "Ava" }),
      line({ seq: 1, type: "lynch" }),
      line({ seq: 1, schema_version: 3 }),
      "",
      line({ seq: 1, meta: null }),
      line({ seq: 1 }),
      line({ seq: 1 }),
    ].join("\n");
    const { events, errors } = parseLog(text);
    expect(events.map((e) => e.seq)).toEqual([0, 1]);
    expect(errors.map((b) => b.line)).toEqual([2, 3, 4, 5, 7, 9]);
    expect(errors[1].error).toMatch(/actor/);
    expect(errors[5].error).toMatch(/seq 1 is not after 1/);
  });
});
