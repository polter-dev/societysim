// .ts, not .tsx: vite.config.ts only collects src/**/*.test.ts.
// Node environment with a JSDOM window, not vitest's jsdom environment: that one serves
// imports like a browser, and vite then refuses the ../../fixtures files outside viewer/.
import axe from "axe-core";
import { JSDOM } from "jsdom";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import LandingPage from "./landing/LandingPage";
import ModeSelection from "./landing/ModeSelection";
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
  return page(renderToStaticMarkup(createElement(App)).replace(/<\/main>$/, `${renderToStaticMarkup(game)}</main>`));
}

/** Body markup in a window with axe loaded and the given stylesheets applied. */
function page(body: string, css = "") {
  const { window } = new JSDOM(`<!doctype html><html lang="en"><head><title>SocietySim viewer</title><style>${css}</style></head><body>${body}</body></html>`, {
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
    const seat = window.document.querySelector('[data-seat="p0"] button')!;
    seat.textContent = "";
    seat.removeAttribute("aria-label");
    expect((await violations(window)).map((v) => v.id)).toContain("button-name");
  });
});

describe("axe on the landing and mode pages", () => {
  const href = (extra: Record<string, string>) => "?" + new URLSearchParams(extra);
  it.each([
    ["landing", () => createElement(LandingPage, { href })],
    ["mode selection", () => createElement(ModeSelection, { href })],
  ])("%s: no violations", async (_, render) => {
    vi.stubEnv("VITE_PLAY_API_URL", "");
    const found = await violations(page(renderToStaticMarkup(render())));
    vi.unstubAllEnvs();
    expect(found.map((v) => `${v.impact} ${v.id}`)).toEqual([]);
  });
});

describe("seat and medallion names", () => {
  const doc = mount(idxOf(15), "villager", null).document;
  const seat = (id: string) => doc.querySelector(`[data-seat="${id}"] button`)!;

  it("names each table seat by player, model and life status, and marks the speaker", () => {
    expect(seat("p0").getAttribute("aria-label")).toBe("Ava, qwen3-235b, alive");
    expect(seat("p4").getAttribute("aria-label")).toBe("Elif, deepseek-v3.1, alive, speaking");
    expect(seat("p0").getAttribute("aria-haspopup")).toBe("dialog");
  });

  it("gives the Agents panel the same names as the seats, without the portrait repeating the model", () => {
    const agents = [...doc.querySelectorAll(".agent-button")].map((b) => b.getAttribute("aria-label"));
    const seats = [...doc.querySelectorAll(".round-table button")].map((b) => b.getAttribute("aria-label")!.replace(", speaking", ""));
    expect(agents).toEqual(seats);
    expect(agents).toHaveLength(7);
  });

  it("keeps medallion portraits decorative", () => {
    const imgs = [...doc.querySelectorAll(".medallion img")];
    expect(imgs.length).toBeGreaterThan(0);
    expect(imgs.every((img) => img.getAttribute("alt") === "")).toBe(true);
  });

  it("shows the role in the name only when the mode reveals it", () => {
    const narrator = mount(idxOf(15), "narrator", null).document;
    expect(narrator.querySelector('[data-seat="p0"] button')!.getAttribute("aria-label")).toMatch(/^Ava, qwen3-235b, alive, \w+$/);
  });
});

describe("replay focus order and announcements", () => {
  const doc = mount(idxOf(15), "villager", null).document;
  const before = (a: string, b: string) =>
    Boolean(doc.querySelector(a)!.compareDocumentPosition(doc.querySelector(b)!) & doc.defaultView!.Node.DOCUMENT_POSITION_FOLLOWING);

  it("reaches the table seats and the timeline before the Agents and Game events panels", () => {
    expect(before(".round-table button", ".agent-button")).toBe(true);
    expect(before('[aria-label="Game timeline"]', ".agent-button")).toBe(true);
    expect(before('[aria-label="Game timeline"]', ".event-list button")).toBe(true);
  });

  it("announces the phase from a region that stays mounted", () => {
    expect(doc.querySelector('.illustrated-shell [role="status"]')!.textContent).toBe("Day 1 · Discussion");
    expect(doc.querySelector(".day-counter")!.getAttribute("aria-hidden")).toBe("true");
    const night = mount(idxOf(0), "villager", null).document;
    expect(night.querySelector('.illustrated-shell [role="status"]')!.textContent).toBe("Gathering the village");
  });
});

describe("a11y.css", () => {
  // Read from disk: vitest serves imported .css (even ?raw) as empty. jsdom's parser drops most of the
  // minified scenery.css, so the two scenery.css rules that a11y.css overrides are restated here.
  const a11yCss = readFileSync(new URL("./a11y.css", import.meta.url), "utf8");
  const sheets = ".replay-tools h1{display:none}.day-counter>span{color:#f3e4b2}" + a11yCss;
  const view = (idx: number) => {
    const game = renderToStaticMarkup(createElement(Game, { events, labels, idx, mode: "villager", selected: null, onSelect: () => {} }));
    const shell = renderToStaticMarkup(createElement(App)).replace(/<\/main>$/, `${game}</main>`);
    return page(shell, sheets);
  };

  it("keeps the replay h1 in the accessibility tree (it was display: none)", () => {
    const w = view(idxOf(15));
    expect(w.getComputedStyle(w.document.querySelector(".replay-tools h1")!).display).not.toBe("none");
  });

  it("uses dark ink for the day counter and leaves the night counter pale", () => {
    const day = view(idxOf(15));
    expect(day.getComputedStyle(day.document.querySelector(".day-counter > span")!).color).toBe("rgb(42, 22, 6)");
    const night = view(idxOf(0));
    expect(night.getComputedStyle(night.document.querySelector(".day-counter > span")!).color).not.toBe("rgb(42, 22, 6)");
  });

  it("sets the spec focus ring with zero specificity, so custom rings still win", () => {
    expect(a11yCss).toMatch(/:where\(a, button, input, select, textarea, summary, \[tabindex\]\):focus-visible \{\s*outline: 3px solid #ffe499;\s*outline-offset: 3px;/);
  });
});
