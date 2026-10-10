import { describe, expect, it } from "vitest";
import fixture from "../../../../fixtures/sample_game.jsonl?raw";
import { parseLog } from "../../data/parse";
import { indexForSeq, nearestSeq, readSeqParam, stepSeq, withSeqParam } from "./seq";

const events = parseLog(fixture).events;

describe("sequence navigation", () => {
  it("maps seq to an array index even when log lines have gaps", () => {
    const sparse = events.filter((event) => event.seq !== 1 && event.seq !== 2);
    expect(indexForSeq(sparse, 3)).toBe(1);
    expect(stepSeq(sparse, 0, 1)).toBe(3);
    expect(stepSeq(sparse, 3, -1)).toBe(0);
    expect(nearestSeq(sparse, 2)).toBe(3);
    expect(nearestSeq(sparse, 1)).toBe(0);
  });

  it("clamps steps at each end", () => {
    expect(stepSeq(events, 0, -1)).toBe(0);
    expect(stepSeq(events, 117, 1)).toBe(117);
    expect(stepSeq([], 0, 1)).toBeNull();
  });

  it("reads exact URL seq and preserves unrelated parameters", () => {
    expect(readSeqParam(events, "?log=game.jsonl&seq=67")).toBe(67);
    expect(readSeqParam(events, "?seq=999")).toBe(0);
    expect(readSeqParam(events, "?seq=abc")).toBe(0);
    expect(withSeqParam("?log=game.jsonl&seq=3", 67)).toBe("?log=game.jsonl&seq=67");
  });
});
