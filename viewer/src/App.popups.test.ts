// @vitest-environment jsdom
// #702: Escape closes the bottom-bar pop-ups (Logs, Spot the wolf, Narration) and returns focus to the summary.
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it } from "vitest";
import fixture from "./__fixtures__/modes_game.jsonl?raw";
import { Game } from "./App";
import { parseLog } from "./data/parse";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let host: HTMLDivElement;
let root: Root;
beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  const events = parseLog(fixture).events;
  act(() => root.render(createElement(Game, { events, labels: [], idx: 5, mode: "villager", selected: null, onSelect: () => {} })));
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

const esc = (el: Element) => act(() => { el.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true })); });

it("closes the open Logs pop-up from inside it and focuses its summary", () => {
  const logs = host.querySelector<HTMLDetailsElement>("details.replay-chat")!;
  logs.open = true;
  const inner = logs.querySelector(".replay-pop__body")!;
  esc(inner);
  expect(logs.open).toBe(false);
  expect(document.activeElement).toBe(logs.querySelector("summary"));
});

it("leaves closed pop-ups alone", () => {
  const logs = host.querySelector<HTMLDetailsElement>("details.replay-chat")!;
  const ev = new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true });
  act(() => { logs.querySelector("summary")!.dispatchEvent(ev); });
  expect(logs.open).toBe(false);
  expect(ev.defaultPrevented).toBe(false);
});
