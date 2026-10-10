import { describe, expect, it } from "vitest";
import { normalizeStepSearch } from "./replayUrl";

describe("normalizeStepSearch", () => {
  it("turns a highlight link's ?seq= into ?step= and keeps the other params", () => {
    expect(normalizeStepSearch("?game=c-g0001&seq=57&mode=narrator", 0)).toBe("?game=c-g0001&mode=narrator&step=57");
  });
  it("prefers an explicit step over seq", () => {
    expect(normalizeStepSearch("?step=9&seq=57", 0)).toBe("?step=9");
  });
  it("opens a finished game at its first event when the link names none", () => {
    expect(normalizeStepSearch("?game=g1", 0)).toBe("?game=g1&step=0");
  });
  it("ignores a junk seq", () => {
    expect(normalizeStepSearch("?seq=abc", 2)).toBe("?step=2");
  });
});
