// @vitest-environment jsdom
// Tab keyboard and ARIA wiring for the global board (#481): the WAI-ARIA tabs pattern.
import axe from "axe-core";
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { GlobalBoard } from "../../data/globalBoard";
import { GlobalLeaderboard } from "./GlobalLeaderboard";

vi.mock("../ModelAvatar/ModelAvatar", async (orig) => ({
  ...(await orig<typeof import("../ModelAvatar/ModelAvatar")>()),
  useModelLabel: () => (m: string) => m,
}));

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const board: GlobalBoard = {
  detectors: [{ model: "a", village_games: 7, suspicion_gap: 0.45, votes: 8, wolf_vote_rate: 0.5, ranked: true }],
  liars: [
    {
      model: "y",
      wolf_games: 6,
      suspicion_received: 0.3,
      survival_rate: 0.833,
      votes_received_per_game: 1,
      self_tag_lie_voters: 0,
      self_tag_lie_success: null,
      jev_lie_voters: 4,
      jev_lie_success: 0.75,
      ranked: true,
    },
  ],
  failed: [],
};

let root: Root;
let box: HTMLElement;
const tabs = () => [...box.querySelectorAll<HTMLButtonElement>("[role=tab]")];
const selected = () => box.querySelector("[role=tab][aria-selected=true]")!.textContent;
const key = (k: string) =>
  act(() => {
    (document.activeElement as HTMLElement).dispatchEvent(new KeyboardEvent("keydown", { key: k, bubbles: true }));
  });

beforeEach(() => {
  box = document.createElement("div");
  document.body.append(box);
  root = createRoot(box);
  act(() => root.render(createElement(GlobalLeaderboard, { board })));
});
afterEach(() => {
  act(() => root.unmount());
  box.remove();
});

it("has one tab stop, and the panel is labelled by the selected tab", () => {
  const [d, l] = tabs();
  expect([d.tabIndex, l.tabIndex]).toEqual([0, -1]);
  const panel = box.querySelector("[role=tabpanel]")!;
  expect(d.getAttribute("aria-controls")).toBe(panel.id);
  expect(panel.getAttribute("aria-labelledby")).toBe(d.id);
  expect(panel.getAttribute("tabindex")).toBe("0");
  // The table keeps its table role; the panel wraps it.
  expect(panel.querySelector("table")!.getAttribute("role")).toBeNull();
});

it("arrow keys, Home and End move focus and selection, wrapping at the ends", async () => {
  tabs()[0].focus();
  await key("ArrowRight");
  expect(selected()).toBe("Liars");
  expect(document.activeElement).toBe(tabs()[1]);
  expect(box.querySelector("[role=tabpanel]")!.getAttribute("aria-labelledby")).toBe(tabs()[1].id);
  expect(box.textContent).toContain("Best liars");
  await key("ArrowRight");
  expect(selected()).toBe("Detectors");
  await key("ArrowLeft");
  expect(selected()).toBe("Liars");
  await key("Home");
  expect(selected()).toBe("Detectors");
  expect(document.activeElement).toBe(tabs()[0]);
  await key("End");
  expect(selected()).toBe("Liars");
  expect([tabs()[0].tabIndex, tabs()[1].tabIndex]).toEqual([-1, 0]);
  await key("a"); // other keys do nothing
  expect(selected()).toBe("Liars");
});

it("passes axe", async () => {
  const { violations } = await axe.run(box, { rules: { "color-contrast": { enabled: false } } });
  expect(violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`)).toEqual([]);
});
