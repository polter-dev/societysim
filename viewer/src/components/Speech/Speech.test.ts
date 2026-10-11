// .ts, not .tsx: vite.config.ts only collects src/**/*.test.ts.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import fixture from "../../../../fixtures/sample_game.jsonl?raw";
import { parseLog } from "../../data/parse";
import { deriveScene, type Mode } from "../../scene/deriveScene";
import { seatPosition } from "../Table/Table";
import { Speech } from "./Speech";

const events = parseLog(fixture).events;
const render = (idx: number, mode: Mode) => renderToStaticMarkup(createElement(Speech, { scene: deriveScene(events, idx, mode) })); // fixture seq == index

describe("Speech bubble on the fixture", () => {
  it("shows the current statement at the speaker's seat", () => {
    const html = render(15, "villager"); // p4 (Eli) opens day 1
    expect(html).toContain('data-speaker="p4"');
    expect(html).toContain("Nobody died, so the Doctor guessed right");
    const { x, y } = seatPosition(4, 7);
    expect(html).toContain(`left:${50 + (x - 50) / 2}%`);
    expect(html).toContain(`top:${50 + (y - 50) / 2}%`);
  });

  it("keeps the bubble through the statement's self-tag events, then moves to the next speaker", () => {
    expect(render(19, "villager")).toContain('data-speaker="p5"'); // role_claim after p5's statement
    expect(render(21, "villager")).toContain('data-speaker="p1"');
  });

  it("is empty outside discussion and never shows wolf chat", () => {
    const empty = '<div aria-live="polite" style="position:absolute;inset:0;pointer-events:none"></div>';
    expect(render(43, "narrator")).toBe(empty); // vote phase_start clears the speaker
    for (const idx of [2, 4, 6, 8]) {
      expect(render(idx, "narrator")).toBe(empty); // wolf_chat is not a statement
      expect(render(idx, "villager")).toBe(empty);
    }
  });

  it("keeps the same live region mounted with and without a bubble", () => {
    const region = (html: string) => html.slice(0, html.indexOf(">") + 1);
    expect(region(render(15, "villager"))).toBe(region(render(43, "villager")));
    expect(region(render(15, "villager"))).toContain('aria-live="polite"');
  });

  it("never shows private reasoning", () => {
    expect(render(15, "narrator")).not.toContain(events[15].private_reasoning!);
  });
});

describe("Speech live region during autoplay", () => {
  const scene = deriveScene(events, 15, "villager");
  it("announces politely when stepping, and is off while playing", () => {
    expect(renderToStaticMarkup(createElement(Speech, { scene }))).toContain('aria-live="polite"');
    expect(renderToStaticMarkup(createElement(Speech, { scene, playing: true }))).toContain('aria-live="off"');
  });
});
