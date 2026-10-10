// .ts, not .tsx: vite.config.ts only collects src/**/*.test.ts, and that file is outside this ticket.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import fixture from "../../../../fixtures/sample_game.jsonl?raw";
import { parseLog } from "../../data/parse";
import { deriveScene, type Mode } from "../../scene/deriveScene";
import { seatPosition, Table } from "./Table";

const events = parseLog(fixture).events;
const render = (seq: number, mode: Mode) =>
  renderToStaticMarkup(createElement(Table, { scene: deriveScene(events, seq, mode) })); // fixture seq == index

/** Each seat's markup, keyed by seat id. */
const seats = (html: string) =>
  Object.fromEntries(
    html
      .split("<li ")
      .slice(1)
      .map((li) => [li.match(/data-seat="(p\d)"/)![1], li]),
  );
// The role card is the last span in the seat button, before the optional "dead" line.
const card = (li: string) => li.match(/data-role-card="[^"]*"[^>]*>(.*?)<\/span>(?:<span[^>]*>dead<\/span>)?<\/button>/)![1];

describe("Table on the fixture", () => {
  it("shows every seat with name, model and model color", () => {
    const s = seats(render(0, "narrator"));
    expect(Object.keys(s)).toEqual(["p0", "p1", "p2", "p3", "p4", "p5", "p6"]);
    expect(s.p0).toContain("Ava");
    expect(s.p0).toContain("qwen3-235b");
    expect(s.p0).toContain("#4f8ef7");
    expect(s.p5).toContain("Finn");
    expect(s.p5).toContain("gemini-flash");
    expect(s.p5).toContain("#ec4899");
  });

  it("narrator mode shows every role", () => {
    const s = seats(render(0, "narrator"));
    expect(card(s.p0)).toBe("Seer");
    expect(card(s.p2)).toBe("Wolf");
    expect(card(s.p3)).toBe("Doctor");
    expect(card(s.p1)).toBe("Villager");
  });

  it("villager mode shows no role before any reveal", () => {
    const html = render(14, "villager");
    for (const role of ["Seer", "Wolf", "Doctor", "Villager"]) expect(html).not.toContain(role);
    for (const li of Object.values(seats(html))) expect(card(li)).toContain("role unknown");
  });

  it("dead players are greyed out with their revealed role, others stay hidden", () => {
    const s = seats(render(104, "villager")); // p0 killed (seer), p6 eliminated (villager)
    expect(s.p0).toContain('data-alive="false"');
    expect(s.p0).toContain("grayscale");
    expect(s.p0).toContain(">dead<");
    expect(card(s.p0)).toBe("Seer");
    expect(card(s.p6)).toBe("Villager");
    expect(s.p1).toContain('data-alive="true"');
    expect(s.p1).not.toContain("grayscale");
    expect(card(s.p2)).toContain("role unknown");
  });

  it("game end reveals every role in villager mode", () => {
    const s = seats(render(117, "villager"));
    expect(card(s.p2)).toBe("Wolf");
    expect(card(s.p5)).toBe("Wolf");
  });

  it("each seat is a button, pressed when it is the selected seat", () => {
    const html = renderToStaticMarkup(createElement(Table, { scene: deriveScene(events, 14, "villager"), selected: "p3" }));
    const s = seats(html);
    expect(s.p3).toContain('<button type="button" aria-pressed="true"');
    expect(s.p0).toContain('<button type="button" aria-pressed="false"');
  });

  it("tint follows the phase", () => {
    expect(render(0, "narrator")).toContain('data-tint="night"'); // setup
    expect(render(9, "villager")).toContain('data-tint="night"');
    expect(render(14, "villager")).toContain('data-tint="day"');
    expect(render(43, "villager")).toContain('data-tint="day"'); // vote
  });
});

describe("seatPosition", () => {
  it("puts seat 0 at the top and spaces seats evenly on one circle", () => {
    const top = seatPosition(0, 7);
    expect(top.x).toBeCloseTo(50);
    expect(top.y).toBeCloseTo(10);
    const r = (i: number) => Math.hypot(seatPosition(i, 7).x - 50, seatPosition(i, 7).y - 50);
    for (let i = 0; i < 7; i++) expect(r(i)).toBeCloseTo(40);
    expect(seatPosition(2, 4)).toEqual({ x: 50, y: 90 });
  });
});
