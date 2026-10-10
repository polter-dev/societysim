import { describe, expect, it } from "vitest";
import { belowThreshold, cell, listModels, parseDeceptionMatrix, rankPairs, sortAxes } from "./deception";
import { fixture } from "../components/DeceptionMatrix/fixture";

describe("parseDeceptionMatrix", () => {
  it("accepts the schema shape, including an empty source", () => {
    const m = parseDeceptionMatrix(structuredClone(fixture));
    expect(cell(m, "self_tag", "a", "b")?.followed).toBe(8);
    expect(cell(m, "jev", "a", "b")).toBeUndefined();
    expect(listModels(m, "jev")).toEqual({ liars: [], listeners: [] });
  });

  it("accepts null rates and CIs", () => {
    const f = structuredClone(fixture);
    f.pairs.self_tag.a.b = { ...f.pairs.self_tag.a.b, voters: 0, followed: 0, vote_follow_rate: null, vote_follow_ci: null };
    expect(() => parseDeceptionMatrix(f)).not.toThrow();
  });

  it("names the bad path", () => {
    const f = structuredClone(fixture) as any;
    f.pairs.self_tag.a.b.voters = "x";
    expect(() => parseDeceptionMatrix(f)).toThrow("pairs.self_tag.a.b.voters");
    expect(() => parseDeceptionMatrix({ exploratory: true, pairs: { self_tag: {} }, baseline: {} })).toThrow("pairs.jev");
    expect(() => parseDeceptionMatrix(null)).toThrow();
  });
});

describe("ranking and sorting", () => {
  const m = parseDeceptionMatrix(structuredClone(fixture));

  it("ranks by rate and drops pairs under the threshold", () => {
    const r = rankPairs(m, "self_tag", 5).map((p) => `${p.liar}>${p.listener}`);
    expect(r).toEqual(["a>b", "b>c", "b>a"]); // a>c has n=4 and is dropped
    const all = rankPairs(m, "self_tag", 1).map((p) => `${p.liar}>${p.listener}`);
    expect(all[0]).toBe("a>c"); // 100% beats 80%
  });

  it("flags cells under the threshold", () => {
    expect(belowThreshold(cell(m, "self_tag", "a", "c"), 5)).toBe(true);
    expect(belowThreshold(cell(m, "self_tag", "a", "b"), 5)).toBe(false);
    expect(belowThreshold(undefined, 5)).toBe(true);
  });

  it("sorts rows by liar skill and columns by gullibility", () => {
    const { liars, listeners } = sortAxes(m, "self_tag");
    expect(liars).toEqual(["a", "b"]); // a: 12/14, b: 5/16
    expect(listeners).toEqual(["b", "c", "a"]); // b: 8/10, c: 7/10, a: 2/10
  });
});
