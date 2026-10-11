// .ts, not .tsx: vite.config.ts only collects src/**/*.test.ts, and that file is outside this ticket.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import fixture from "../../../../fixtures/sample_game.jsonl?raw";
import { parseLog } from "../../data/parse";
import { deriveScene, type Mode } from "../../scene/deriveScene";
import { seatPosition, TABLE_BOX, Table } from "./Table";

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
// The role chip's text, and the seat button's accessible name.
const card = (li: string) => li.match(/data-role-card="[^"]*"[^>]*>(.*?)<\/span>/)![1];
const label = (li: string) => li.match(/aria-label="([^"]*)"/)![1];

describe("Table on the fixture", () => {
  it("shows every seat as a medallion with name and model", () => {
    const s = seats(render(0, "narrator"));
    expect(Object.keys(s)).toEqual(["p0", "p1", "p2", "p3", "p4", "p5", "p6"]);
    expect(s.p0).toContain('class="medallion"');
    expect(s.p0).toContain("avatars/qwen3-235b.webp");
    expect(s.p0).toContain("<strong>Ava</strong>");
    expect(s.p0).toContain("qwen3-235b");
    expect(s.p5).toContain("<strong>Finn</strong>");
    expect(s.p5).toContain("gemini-flash");
  });

  it("shows the short model name on the plate, with the full id on hover and in the seat's name", () => {
    const s = seats(render(0, "narrator"));
    expect(s.p0).toContain('<span class="seat-model" title="qwen3-235b">Qwen3 235B</span>');
    expect(label(s.p0)).toBe("Ava, qwen3-235b, alive, seer");
  });

  it("marks each seat's button for the live smoke check (the <li> is a zero-size anchor)", () => {
    const s = seats(render(0, "villager"));
    for (const li of Object.values(s)) expect(li).toContain('data-testid="seat-button"');
  });

  it("names each seat with name, model, status and only the roles this mode shows", () => {
    expect(label(seats(render(0, "narrator")).p2)).toBe("Cleo, gemma-4-26b, alive, wolf");
    expect(label(seats(render(14, "villager")).p2)).toBe("Cleo, gemma-4-26b, alive");
    const dead = seats(render(104, "villager"));
    expect(label(dead.p0)).toBe("Ava, qwen3-235b, eliminated, seer");
    expect(label(dead.p6)).toBe("Gia, gemini-flash-lite, eliminated, villager");
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
    for (const role of ["seer", "wolf", "doctor", "villager"]) expect(html).not.toContain(`, ${role}"`);
    for (const li of Object.values(seats(html))) expect(card(li)).toBe("?");
  });

  it("dead players get the dead medallion and their revealed role, others stay hidden", () => {
    const s = seats(render(104, "villager")); // p0 killed (seer), p6 eliminated (villager)
    expect(s.p0).toContain('data-alive="false"');
    expect(s.p0).toContain('class="medallion" data-alive="false"');
    expect(s.p0).toContain('data-revealed="true"');
    expect(card(s.p0)).toBe("Seer");
    expect(card(s.p6)).toBe("Villager");
    expect(s.p1).toContain('class="medallion" data-alive="true"');
    expect(card(s.p2)).toBe("?");
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

  it("marks the current speaker, and nobody outside discussion", () => {
    const speaking = (html: string) =>
      Object.entries(seats(html))
        .filter(([, li]) => li.includes("data-speaking"))
        .map(([id]) => id);
    expect(speaking(render(15, "villager"))).toEqual(["p4"]); // p4 opens day 1
    expect(speaking(render(43, "villager"))).toEqual([]); // vote
  });

  it("hangs the name plate above upper seats and below lower ones", () => {
    const s = seats(render(0, "narrator"));
    expect(s.p0).toContain('data-plate="above"');
    expect(s.p3).toContain('data-plate="below"');
  });

  it("tint follows the phase", () => {
    expect(render(0, "narrator")).toContain('data-tint="night"'); // setup
    expect(render(9, "villager")).toContain('data-tint="night"');
    expect(render(14, "villager")).toContain('data-tint="day"');
    expect(render(43, "villager")).toContain('data-tint="day"'); // vote
  });
});

describe("seatPosition", () => {
  // Chair cushion centers read off table2.png (1672 x 941 image px), clockwise from the top.
  const CHAIRS_PX = [
    [837, 210], [1020, 260], [1122, 390], [1127, 550], [1030, 685],
    [836, 737], [642, 685], [542, 550], [550, 390], [657, 260],
  ];
  const toPx = ({ x, y }: { x: number; y: number }) => [836 + ((x - 50) * TABLE_BOX) / 100, 941 / 2 + ((y - 50) * TABLE_BOX) / 100];
  const chairOf = (i: number, n: number) => {
    const [x, y] = toPx(seatPosition(i, n));
    return CHAIRS_PX.findIndex(([cx, cy]) => Math.hypot(x - cx, y - cy) < 15);
  };

  it("puts ten seats on the ten chairs of the artwork, in order", () => {
    expect(Array.from({ length: 10 }, (_, i) => chairOf(i, 10))).toEqual([0, 1, 2, 3, 4, 5, 6, 7, 8, 9]);
  });

  it("seats smaller games in the same chairs, spread out, leaving the rest empty", () => {
    for (const n of [7, 8, 9]) {
      const used = Array.from({ length: n }, (_, i) => chairOf(i, n));
      expect(used).not.toContain(-1);
      expect(used).toEqual([...new Set(used)].sort((a, b) => a - b)); // distinct, still clockwise
      for (let i = 1; i < n; i++) expect(used[i] - used[i - 1]).toBeLessThanOrEqual(2);
    }
  });
});
