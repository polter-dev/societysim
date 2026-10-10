import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import fixture from "../../../fixtures/sample_game.jsonl?raw";
import { parseLog } from "../data/parse";
import { SpotWolf } from "./SpotWolf";
import {
  buildResults,
  compareModels,
  initGuesses,
  isLocked,
  isRevealed,
  livingSeats,
  lockIfRevealed,
  revealedRoles,
  scoreMarks,
  toggleMark,
} from "./guesses";
import { loadGuesses, saveGuesses } from "./storage";
import { spotWolfView } from "./useSpotWolf";

const events = parseLog(fixture).events;
const last = events.length - 1;
const endIdx = events.findIndex((e) => e.type === "game_end");
const roles = events[endIdx].meta.roles!;
const wolves = Object.keys(roles).filter((id) => roles[id] === "wolf");
const mid = 20;

function memoryStore() {
  const m = new Map<string, string>();
  return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), m };
}

describe("reveal gating", () => {
  it("is not revealed until game_end", () => {
    expect(isRevealed(events, 0)).toBe(false);
    expect(isRevealed(events, endIdx - 1)).toBe(false);
    expect(isRevealed(events, endIdx)).toBe(true);
    expect(isRevealed([], 0)).toBe(false);
  });

  it("gives no roles or results before the reveal", () => {
    expect(revealedRoles(events, endIdx - 1)).toBeNull();
    expect(buildResults(events, endIdx - 1, toggleMark(initGuesses("g"), events, mid, "p2"))).toBeNull();
  });

  it("view before the reveal carries no role or private text", () => {
    let s = initGuesses(events[0].game_id);
    s = toggleMark(s, events, mid, "p2");
    for (let i = 0; i < endIdx; i += 7) {
      const json = JSON.stringify(spotWolfView(events, i, s));
      expect(json).not.toMatch(/wolf|seer|doctor|villager/i);
      for (const e of events) if (e.private_reasoning) expect(json).not.toContain(e.private_reasoning);
    }
    const html = renderToStaticMarkup(createElement(SpotWolf, { view: spotWolfView(events, mid, s), onToggle: () => {} }));
    expect(html).not.toMatch(/seer|doctor|villager/i);
    expect(html).not.toContain("How the models did");
  });

  it("only offers living players, from the public view", () => {
    const early = livingSeats(events, 0).map((p) => p.id);
    expect(early).toHaveLength(7);
    const dead = events.filter((e) => e.type === "death_reveal" && e.target).map((e) => e.target!);
    expect(dead.length).toBeGreaterThan(0);
    const after = livingSeats(events, last - 1).map((p) => p.id);
    for (const d of dead) expect(after).not.toContain(d);
  });
});

describe("marking and locking", () => {
  it("toggles, records a beat per change and caps marks", () => {
    let s = initGuesses("g");
    s = toggleMark(s, events, 5, "p2");
    s = toggleMark(s, events, 5, "p5");
    expect(s.marks).toEqual(["p2", "p5"]);
    expect(s.history).toHaveLength(1); // same beat replaces
    expect(toggleMark(s, events, 5, "p0")).toBe(s); // over the cap
    s = toggleMark(s, events, 30, "p5");
    expect(s.marks).toEqual(["p2"]);
    expect(s.history.map((b) => b.seq)).toEqual([events[5].seq, events[30].seq]);
    expect(s.history[1].marks).toEqual(["p2"]);
  });

  it("ignores unknown or dead seats", () => {
    const s = initGuesses("g");
    expect(toggleMark(s, events, 5, "p99")).toBe(s);
    const dead = events.find((e) => e.type === "death_reveal" && e.target)!;
    const afterIdx = events.indexOf(dead) + 1;
    expect(toggleMark(s, events, afterIdx, dead.target!)).toBe(s);
  });

  it("locks at the reveal and stays locked when scrubbing back", () => {
    let s = toggleMark(initGuesses("g"), events, mid, "p2");
    expect(isLocked(s, events, mid)).toBe(false);
    expect(lockIfRevealed(s, events, mid)).toBe(s);
    s = lockIfRevealed(s, events, endIdx);
    expect(s.lockedSeq).toBe(events[endIdx].seq);
    expect(toggleMark(s, events, mid, "p5")).toBe(s);
    expect(isLocked(s, events, 0)).toBe(true);
    // even without a stored lock, the reveal itself blocks new marks
    expect(toggleMark(initGuesses("g"), events, endIdx, "p2").marks).toEqual([]);
  });
});

describe("scoring", () => {
  it("scores hits and misses", () => {
    expect(scoreMarks(wolves, roles)).toEqual({ hits: 2, misses: 0, points: 20, maxPoints: 20 });
    expect(scoreMarks([wolves[0], "p0"], roles)).toEqual({ hits: 1, misses: 1, points: 5, maxPoints: 20 });
    expect(scoreMarks(["p0", "p1"], roles).points).toBe(0); // never negative
    expect(scoreMarks([], roles).points).toBe(0);
  });

  it("builds results with per-beat scores after the reveal", () => {
    let s = toggleMark(initGuesses("g"), events, 5, "p0");
    s = toggleMark(s, events, 40, "p0");
    s = toggleMark(s, events, 40, wolves[0]);
    s = toggleMark(s, events, 60, wolves[1]);
    const r = buildResults(events, last, s)!;
    expect(r.wolves.map((w) => w.id)).toEqual(wolves);
    expect(r.marks).toEqual([wolves[0], wolves[1]]);
    expect(r.score.points).toBe(20);
    expect(r.beats.map((b) => b.score.points)).toEqual([0, 10, 20]);
  });
});

describe("model comparison", () => {
  const rows = compareModels(events, roles);

  it("covers non-wolf models only", () => {
    expect(rows.length).toBeGreaterThan(0);
    for (const r of rows) expect(roles[r.seat]).not.toBe("wolf");
  });

  it("uses each model's last suspicion report", () => {
    const r = rows[0];
    const lastReport = [...events].reverse().find((e) => e.actor === r.seat && e.suspicion?.length)!.suspicion!;
    const wolfPs = lastReport.filter((x) => roles[x.player] === "wolf").map((x) => x.p);
    expect(r.meanWolfP).toBeCloseTo(wolfPs.reduce((a, b) => a + b, 0) / wolfPs.length);
    expect(r.topPicks).toHaveLength(wolves.length);
    expect(r.score).toEqual(scoreMarks(r.topPicks, roles));
  });
});

describe("storage", () => {
  it("round-trips per game", () => {
    const store = memoryStore();
    const s = toggleMark(initGuesses("a"), events, 5, "p2");
    saveGuesses(s, store);
    expect(loadGuesses("a", store)).toEqual(s);
    expect(loadGuesses("b", store)).toEqual(initGuesses("b"));
  });

  it("falls back on bad data and blocked storage", () => {
    const store = memoryStore();
    store.setItem("societysim.spotwolf.v1.a", "{nope");
    expect(loadGuesses("a", store)).toEqual(initGuesses("a"));
    store.setItem("societysim.spotwolf.v1.a", JSON.stringify({ gameId: "a", marks: [1] }));
    expect(loadGuesses("a", store)).toEqual(initGuesses("a"));
    const broken = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
    };
    expect(loadGuesses("a", broken)).toEqual(initGuesses("a"));
    expect(() => saveGuesses(initGuesses("a"), broken)).not.toThrow();
    expect(loadGuesses("a", null)).toEqual(initGuesses("a"));
  });
});

describe("component", () => {
  it("shows results with model table after the reveal", () => {
    const s = lockIfRevealed(toggleMark(initGuesses(events[0].game_id), events, mid, wolves[0]), events, last);
    const html = renderToStaticMarkup(createElement(SpotWolf, { view: spotWolfView(events, last, s), onToggle: () => {} }));
    expect(html).toContain("You scored 10 of 20");
    expect(html).toContain("How the models did");
  });

  it("disables buttons once locked", () => {
    const s = lockIfRevealed(initGuesses("g"), events, endIdx);
    const v = spotWolfView(events, endIdx, s);
    expect(v.locked).toBe(true);
  });
});
