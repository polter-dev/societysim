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
    expect(listeners).toEqual(["b", "c", "a"]); // over cells with 5+ voters: b 8/10, c 3/6, a 2/10
  });

  it("does not let thin samples top the axes (#712)", () => {
    const f = structuredClone(fixture) as any;
    // d: 100% on cells under the threshold only; e: 4/5 loses to a's 8/10 on the lower bound.
    f.pairs.self_tag.d = { a: { ...f.pairs.self_tag.a.b, voters: 4, followed: 4, vote_follow_rate: 1 } };
    f.pairs.self_tag.e = { c: { ...f.pairs.self_tag.a.b, voters: 5, followed: 4, vote_follow_rate: 0.8 } };
    const t = parseDeceptionMatrix(f);
    expect(sortAxes(t, "self_tag").liars).toEqual(["a", "e", "b", "d"]);
    expect(sortAxes(t, "self_tag", 1).liars).toEqual(["a", "d", "e", "b"]); // 4/4 counts once the threshold allows it
  });

  it("puts thin models after well-sampled ones even when their lower bound is higher (#717)", () => {
    const f = structuredClone(fixture) as any;
    const big = f.pairs.self_tag.a.b;
    // d: two 5-voter cells at 5/5 (high bound) among five tiny cells -> thin; b stays well sampled.
    f.pairs.self_tag.d = {
      a: { ...big, voters: 5, followed: 5, vote_follow_rate: 1 },
      b: { ...big, voters: 5, followed: 5, vote_follow_rate: 1 },
      c: { ...big, voters: 2, followed: 2, vote_follow_rate: 1 },
      d: { ...big, voters: 2, followed: 2, vote_follow_rate: 1 },
      e: { ...big, voters: 2, followed: 2, vote_follow_rate: 1 },
    };
    const t = parseDeceptionMatrix(f);
    const liars = sortAxes(t, "self_tag").liars;
    expect(liars.indexOf("b")).toBeLessThan(liars.indexOf("d"));
    expect(liars.at(-1)).toBe("d");
  });
});
