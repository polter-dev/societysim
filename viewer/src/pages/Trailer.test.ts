// .ts, not .tsx: vite.config.ts only collects src/**/*.test.ts.
import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { parseIndex } from "../audio/narration";
import TrailerPage, { G0043, GAME_ID, narratedBetween, stepAt, TIMELINE, TrailerFrame, trailerState } from "./Trailer";

const seqs = new Set(G0043.map((e) => e.seq));
const seats = G0043[0].meta.seats!;
const audioIndex = parseIndex(JSON.parse(readFileSync(new URL(`../../public/${GAME_ID}.audio/index.json`, import.meta.url), "utf8")))!;
const stepIndex = (pred: (step: (typeof TIMELINE)[number]) => boolean) => TIMELINE.findIndex(pred);
const frame = (idx: number) => renderToStaticMarkup(createElement(TrailerFrame, { state: trailerState(TIMELINE, idx) }));

describe("trailer timeline", () => {
  it("is 45 to 60 seconds, starts at 0 and never runs backwards", () => {
    expect(TIMELINE[0].t).toBe(0);
    for (let i = 1; i < TIMELINE.length; i++) expect(TIMELINE[i].t).toBeGreaterThanOrEqual(TIMELINE[i - 1].t);
    const end = TIMELINE.at(-1)!;
    expect(end.kind).toBe("end");
    expect(end.t).toBeGreaterThanOrEqual(45);
    expect(end.t).toBeLessThanOrEqual(60);
  });

  it("only references events that exist in g0043, in log order", () => {
    for (let i = 1; i < G0043.length; i++) expect(G0043[i].seq).toBeGreaterThan(G0043[i - 1].seq);
    for (const e of G0043) expect(e.game_id).toBe(GAME_ID);
    let last = -1;
    for (const step of TIMELINE) {
      if (step.kind !== "seq") continue;
      expect(seqs.has(step.data.seq)).toBe(true);
      expect(step.data.seq).toBeGreaterThan(last);
      last = step.data.seq;
    }
  });

  it("plays only narrator clips that ship with the game", () => {
    const narrated = narratedBetween(TIMELINE, -1, TIMELINE.length - 1);
    expect(narrated).toEqual([1, 14, 68, 79]);
    for (const seq of narrated) expect(audioIndex.clips.find((c) => c.seq === seq && c.category === "narrator")).toBeTruthy();
  });

  it("embeds no private data: the one notes line is Hugo's seq 48 and the reveal matches the recorded roles", () => {
    for (const e of G0043) {
      expect(e.private_reasoning).toBeNull();
      expect(e.suspicion).toBeNull();
      expect(e.meta.usage).toBeUndefined();
    }
    const notes = TIMELINE.filter((step) => step.kind === "notes" && step.data.text !== null);
    expect(notes).toHaveLength(1);
    expect(notes[0].data).toMatchObject({ seq: 48 });
    expect(G0043.find((e) => e.seq === 48)).toMatchObject({ type: "statement", actor: "p5" });
    const reveal = TIMELINE.find((step) => step.kind === "reveal")!;
    const wolves = Object.entries(seats).filter(([, s]) => s.role === "wolf").map(([id]) => id);
    expect((reveal.data as { wolves: string[] }).wolves.sort()).toEqual(wolves.sort());
  });

  it("folds steps into state: idle before the first, everything set by the last", () => {
    expect(trailerState(TIMELINE, -1)).toMatchObject({ seq: 0, lit: 0, title: null, card: null, done: false });
    const end = trailerState(TIMELINE, TIMELINE.length - 1);
    expect(end).toMatchObject({ lit: 10, seq: 79, wolves: ["p2", "p5"], card: "end", done: true, notes: null, caption: null });
    const stats = trailerState(TIMELINE, stepIndex((s) => s.kind === "card" && s.data.card === "cta") - 1).stats.map((s) => s.value);
    expect(stats).toEqual(["356", "40% vs 71%", "65%"]);
  });

  it("finds the last step at a time, including same-instant batches", () => {
    expect(stepAt(TIMELINE, -1)).toBe(-1);
    expect(stepAt(TIMELINE, 0)).toBe(1); // seq 1 and its narration share t=0
    expect(stepAt(TIMELINE, 1000)).toBe(TIMELINE.length - 1);
    expect(narratedBetween(TIMELINE, -1, stepAt(TIMELINE, 0))).toEqual([1]);
    expect(narratedBetween(TIMELINE, stepAt(TIMELINE, 0), stepAt(TIMELINE, 5.2))).toEqual([14]);
  });
});

describe("trailer frames", () => {
  it("shows Hugo's statement in a bubble at its step and his notes only during the notes beat", () => {
    const bubble = frame(stepIndex((s) => s.kind === "seq" && s.data.seq === 48));
    expect(bubble).toContain('data-speaker="p5"');
    expect(bubble).toContain("uncontested Seer claim");
    expect(bubble).not.toContain("tied to her");
    const notes = frame(stepIndex((s) => s.kind === "notes"));
    expect(notes).toContain("tied to her");
    expect(notes).toContain("Private notes");
  });

  it("reveals wolves only at the reveal, after the elimination shows Iris as a wolf", () => {
    const before = frame(stepIndex((s) => s.kind === "seq" && s.data.seq === 79));
    expect(before).toContain('data-seat="p2" data-alive="false" data-revealed="true"');
    expect(before).toContain('data-seat="p5" data-alive="true" data-revealed="false"');
    expect(before).not.toMatch(/Seer|Doctor/);
    const after = frame(stepIndex((s) => s.kind === "reveal"));
    expect(after).toContain('data-seat="p5" data-alive="true" data-revealed="true"');
    expect(after.match(/>Wolf</g)).toHaveLength(2);
    expect(after).not.toMatch(/Seer|Doctor/);
  });

  it("lights the seats one by one and counts the votes on Iris", () => {
    expect(frame(stepIndex((s) => s.kind === "seats" && s.data.lit === 3)).match(/data-lit="true"/g)).toHaveLength(3);
    const voted = frame(stepIndex((s) => s.kind === "seq" && s.data.seq === 78));
    expect(voted).toContain('aria-label="9 votes"');
    expect(voted).toContain('aria-label="1 votes"');
  });

  it("ends with the numbers, the call to action and the end card", () => {
    const stats = frame(stepIndex((s) => s.kind === "stat" && s.data.value === "65%"));
    expect(stats).toContain("356");
    expect(stats).toContain("40% vs 71%");
    expect(stats).toContain("Gemini Flash win rate");
    expect(frame(stepIndex((s) => s.kind === "card" && s.data.card === "cta"))).toContain("Play a seat against the models.");
    expect(frame(TIMELINE.length - 1)).toContain("society-sim.us");
  });

  it("renders the page idle with a sound button and no spoilers", () => {
    const html = renderToStaticMarkup(createElement(TrailerPage));
    expect(html).toContain("Play with sound");
    expect(html).toContain("Play muted");
    expect(html).toContain('class="forest-scene');
    expect(html).not.toContain("tied to her");
    expect(html).not.toContain(">Wolf<");
  });
});
