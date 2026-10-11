// @vitest-environment jsdom
// #703: starting the trailer from a start button keeps keyboard focus on the page's controls.
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import TrailerPage from "./Trailer";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let host: HTMLDivElement;
let root: Root;
beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(() => Promise.reject(new Error("offline"))));
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  act(() => root.render(createElement(TrailerPage, { audioIndexUrl: "https://x.test/index.json" })));
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

it("moves focus to Replay when the Play muted button it was on goes away", () => {
  const muted = [...host.querySelectorAll("button")].find((b) => b.textContent === "Play muted")!;
  muted.focus();
  act(() => muted.click());
  expect(muted.isConnected).toBe(false);
  expect(document.activeElement?.textContent).toBe("Replay");
});
