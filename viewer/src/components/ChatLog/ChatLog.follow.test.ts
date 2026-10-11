// @vitest-environment jsdom
// keepPlace: the your-turn panel follows the newest entry unless the reader scrolled up.
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { parseLog } from "../../data/parse";
import { deriveScene } from "../../scene/deriveScene";
import { ChatLog } from "./ChatLog";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const events = parseLog(readFileSync(resolve(process.cwd(), "../fixtures/sample_game.jsonl"), "utf8")).events;

function setup(keepPlace: boolean) {
  const box = document.createElement("div");
  const root = createRoot(box);
  const draw = (idx: number) => act(() => root.render(createElement(ChatLog, { scene: deriveScene(events, idx, "villager"), keepPlace })));
  draw(20);
  const el = box.querySelector("section")!;
  // jsdom has no layout: a 500px tall log in a 100px box.
  Object.defineProperty(el, "scrollHeight", { configurable: true, value: 500 });
  Object.defineProperty(el, "clientHeight", { configurable: true, value: 100 });
  return { el, draw, done: () => act(() => root.unmount()) };
}
const scrollTo = (el: HTMLElement, top: number) => act(() => { el.scrollTop = top; el.dispatchEvent(new Event("scroll")); });

describe("ChatLog keepPlace", () => {
  it("follows the newest entry while the reader is at the bottom", () => {
    const { el, draw, done } = setup(true);
    scrollTo(el, 400);
    draw(40);
    expect(el.scrollTop).toBe(500);
    done();
  });

  it("stays where the reader scrolled up to", () => {
    const { el, draw, done } = setup(true);
    scrollTo(el, 50);
    draw(40);
    expect(el.scrollTop).toBe(50);
    done();
  });

  it("the pop-up keeps jumping to the newest entry", () => {
    const { el, draw, done } = setup(false);
    scrollTo(el, 50);
    draw(40);
    expect(el.scrollTop).toBe(500);
    done();
  });
});
