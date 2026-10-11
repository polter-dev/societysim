// @vitest-environment jsdom
// #701: Open a log file with a bad file keeps the open-log panel; the same file can be picked twice; empty ?game=.
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it } from "vitest";
import App from "./App";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let host: HTMLDivElement;
let root: Root;
const mount = (search: string) => {
  window.history.replaceState(null, "", `/${search}`);
  act(() => root.render(createElement(App)));
};
beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  window.history.replaceState(null, "", "/");
});

/** Picks `text` as a file in the panel's input, the way a browser change event does. */
async function pick(text: string) {
  const input = host.querySelector<HTMLInputElement>('input[type=file]')!;
  Object.defineProperty(input, "files", { configurable: true, value: [new File([text], "bad.jsonl")] });
  await act(async () => {
    input.dispatchEvent(new Event("change", { bubbles: true }));
    await new Promise((r) => setTimeout(r, 0));
  });
  return input;
}

it("keeps the open-log panel, guidance and Games link after a file with no valid events", async () => {
  mount("?page=replay");
  await pick("hello\nnot json\n");
  const panel = host.querySelector(".replay-tools")!;
  expect(panel.classList.contains("open-log")).toBe(true);
  expect(panel.querySelector("h1")!.textContent).toBe("Open a game log");
  expect(panel.textContent).toContain("This file has no valid game events.");
  expect(panel.textContent).toContain("2 bad line(s) skipped");
  expect(panel.textContent).toContain("Browse games");
  expect(panel.querySelectorAll('input[type=file]')).toHaveLength(1);
  expect(panel.querySelector('[aria-label^="View"], .mode-toggle')).toBeNull();
});

it("clears the file input so the same file can be chosen again", async () => {
  mount("?page=replay");
  const input = host.querySelector<HTMLInputElement>("input[type=file]")!;
  // jsdom keeps a file input's value empty, so record writes instead: the handler must reset it.
  const writes: string[] = [];
  Object.defineProperty(input, "value", { configurable: true, get: () => "C:\\fakepath\\bad.jsonl", set: (v: string) => writes.push(v) });
  await pick("nope\n");
  expect(writes).toContain("");
});

it("treats an empty ?game= as no game instead of loading forever", () => {
  mount("?game=");
  expect(host.textContent).not.toContain("Loading the game");
  expect(host.querySelector(".replay-tools")!.classList.contains("open-log")).toBe(true);
});
