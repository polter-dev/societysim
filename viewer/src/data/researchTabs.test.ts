import { describe, expect, it } from "vitest";
import { hasLies, parseLieTable, parseRoleSkill, sortRows } from "./researchTabs";

const lies = (n: number, ls = {}) => ({
  lies: n,
  by_type: {},
  lie_rate: n ? n / 20 : null,
  lie_success: { exploratory: true, comparisons: 8, moved: 4, voters: 6, followed: 3, base_voters: 10, base_followed: 2, base_abstained: 0, share_moved: 0.5, share_followed: 0.5, base_rate: 0.2, ...ls },
});
const models = (selfN: number, jevN: number) => ({
  "gemini-flash": { all: { lies: { self_tag: lies(selfN), jev: lies(jevN) } }, wolf: {} },
  "gpt-oss-120b": { all: { lies: { self_tag: lies(selfN, { comparisons: 2, share_moved: 1 }), jev: lies(jevN) } } },
});

describe("parseLieTable", () => {
  it("reads each source's rows from models[*].all.lies", () => {
    const t = parseLieTable(models(3, 0))!;
    expect(t.self_tag.map((r) => r.model)).toEqual(["gemini-flash", "gpt-oss-120b"]);
    expect(t.self_tag[0]).toMatchObject({ lies: 3, lie_rate: 0.15, share_moved: 0.5, comparisons: 8, share_followed: 0.5, voters: 6, base_rate: 0.2, base_voters: 10 });
    expect(hasLies(t, "self_tag")).toBe(true);
    expect(hasLies(t, "jev")).toBe(false);
    expect(hasLies(parseLieTable(models(3, 2))!, "jev")).toBe(true);
  });

  it("returns null for missing or malformed data", () => {
    expect(parseLieTable(undefined)).toBeNull();
    expect(parseLieTable({})).toBeNull();
    expect(parseLieTable({ m: { all: {} } })).toBeNull();
    expect(parseLieTable({ m: { all: { lies: { self_tag: { lies: "3" }, jev: {} } } } })).toBeNull();
  });
});

const doctor = { nights: 32, saves: 12, self_protects: 13, fallbacks: 0, save_rate: 0.375, save_ci: [0.23, 0.55], chance_rate: 0.12 };
const seer = { checks: 27, wolves_found: 10, fallbacks: 0, hit_rate: 0.37, hit_ci: [0.22, 0.56], chance_rate: 0.22 };

describe("parseRoleSkill", () => {
  it("reads doctor and seer rows", () => {
    const t = parseRoleSkill({ doctor: { models: { a: doctor }, all: doctor }, seer: { models: { a: seer }, all: seer } })!;
    expect(t.doctor[0]).toEqual({ model: "a", n: 32, hits: 12, selfProtects: 13, rate: 0.375, ci: [0.23, 0.55], chance: 0.12 });
    expect(t.seer[0]).toMatchObject({ model: "a", n: 27, hits: 10, selfProtects: null, chance: 0.22 });
  });

  it("returns null when the key is missing, malformed, or has no decisions at all", () => {
    expect(parseRoleSkill(undefined)).toBeNull();
    expect(parseRoleSkill({ doctor: { models: {} }, seer: { models: {} } })).toBeNull();
    expect(parseRoleSkill({ doctor: { models: { a: { ...doctor, saves: "x" } } }, seer: { models: {} } })).toBeNull();
    expect(parseRoleSkill({ doctor: { models: { a: doctor } } })).toBeNull();
  });

  it("keeps a role with no decisions as an empty list", () => {
    expect(parseRoleSkill({ doctor: { models: { a: doctor } }, seer: { models: {} } })!.seer).toEqual([]);
  });
});

describe("sortRows", () => {
  it("puts thin rows last, then the best rate, then name", () => {
    const rows = [
      { model: "thin", n: 2, r: 1 },
      { model: "b", n: 30, r: 0.3 },
      { model: "a", n: 30, r: 0.3 },
      { model: "top", n: 10, r: 0.5 },
    ];
    expect(sortRows(rows, (x) => x.n, (x) => x.r).map((x) => x.model)).toEqual(["top", "a", "b", "thin"]);
  });
});
