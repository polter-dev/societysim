// .ts, not .tsx: vite.config.ts only collects src/**/*.test.ts.
// Node environment with a JSDOM window, not vitest's jsdom environment: that one serves
// imports like a browser, and vite then refuses the ../../fixtures files outside viewer/.
import axe from "axe-core";
import { JSDOM } from "jsdom";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import fixture from "../../fixtures/sample_game.jsonl?raw";
import labelsText from "../../fixtures/sample_game.labels.jsonl?raw";
import App, { Game } from "./App";
import { parseLabels } from "./data/labels";
import { parseLog } from "./data/parse";
import type { Mode } from "./scene/deriveScene";
import type { Label } from "./types/events";

const events = parseLog(fixture).events;
const labels = parseLabels(labelsText) as Label[];
const idxOf = (seq: number) => events.findIndex((e) => e.seq === seq);

// App reads the URL while rendering.
(globalThis as { window?: unknown }).window ??= { location: { search: "" } };

/** The composed page (App shell plus Game) at one event, in a window with axe loaded. */
function mount(idx: number, mode: Mode, selected: string | null) {
  const game = createElement(Game, { events, labels, idx, mode, selected, onSelect: () => {}, onSeqChange: () => {} });
  const page = renderToStaticMarkup(createElement(App)).replace(/<\/main>$/, `${renderToStaticMarkup(game)}</main>`);
  const { window } = new JSDOM(`<!doctype html><html lang="en"><head><title>SocietySim viewer</title></head><body>${page}</body></html>`, {
    runScripts: "outside-only",
  });
  window.eval(axe.source);
  return window as unknown as Window & { axe: typeof axe };
}

// jsdom has no layout, so color-contrast can't run here; it is checked in a browser (see the PR).
const violations = async (window: Window & { axe: typeof axe }) =>
  (await window.axe.run(window.document, { rules: { "color-contrast": { enabled: false } } })).violations;

describe("axe on the composed viewer", () => {
  // Night setup, a speech bubble, a lie label, the day 1 tie, a death, game end.
  const cases: [number, Mode, string | null][] = [
    [idxOf(0), "narrator", null],
    [idxOf(15), "villager", null],
    [idxOf(19), "narrator", "p5"],
    [idxOf(54), "villager", "p0"],
    [idxOf(104), "narrator", "p2"],
    [events.length - 1, "villager", "p1"],
  ];

  it.each(cases)("event %i, %s, selected %s: no violations", async (idx, mode, selected) => {
    const found = await violations(mount(idx, mode, selected));
    expect(found.map((v) => `${v.impact} ${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
  });

  it("catches a real violation (so the cases above can fail)", async () => {
    const window = mount(idxOf(15), "villager", null);
    window.document.querySelector('[data-seat="p0"] button')!.textContent = "";
    expect((await violations(window)).map((v) => v.id)).toContain("button-name");
  });
});
