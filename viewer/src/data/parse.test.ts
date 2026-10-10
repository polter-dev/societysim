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

  it("accepts a version 2 log with seats up to p8", () => {
    const text = [line({}), line({ seq: 1, actor: "p8", target: "p0" })].join("\n");
    const { events, errors } = parseLog(text);
    expect(errors).toEqual([]);
    expect(events.map((e) => e.actor)).toEqual([null, "p8"]);
  });

  it("accepts a version 3 (10-player) log with seat p9", () => {
    const v3 = (over: Record<string, unknown>) => line({ schema_version: 3, ...over });
    const text = [v3({}), v3({ seq: 1, actor: "p9", target: "p0" }), v3({ seq: 2, target: "p9" })];
    const { events, errors } = parseLog(text.join("\n"));
    expect(errors).toEqual([]);
    expect(events.map((e) => [e.schema_version, e.actor, e.target])).toEqual([
      [3, null, null],
      [3, "p9", "p0"],
      [3, null, "p9"],
    ]);
  });

  it("rejects seat ids past p9", () => {
    const { errors } = parseLog([line({}), line({ seq: 1, actor: "p10" })].join("\n"));
    expect(errors.map((b) => b.line)).toEqual([2]);
    expect(errors[0].error).toMatch(/actor/);
  });

  it("reports bad lines with their 1-based line number and keeps the good ones", () => {
    const text = [
      line({}),
      "{not json",
      line({ seq: 1, actor: "Ava" }),
      line({ seq: 1, type: "lynch" }),
      line({ seq: 1, schema_version: 4 }),
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
