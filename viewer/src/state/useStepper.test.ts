// @vitest-environment jsdom
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import fixture from "../__fixtures__/modes_game.jsonl?raw";
import { parseLog } from "../data/parse";
import { useStepper } from "./useStepper";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const events = parseLog(fixture).events;

type H = ReturnType<typeof useStepper>;
type Evs = typeof events;
let root: Root;
let box: HTMLElement;
let h: { current: H };

function mount(evs: Evs = events, live = false) {
  h = { current: undefined as unknown as H };
  const C = ({ evs, live }: { evs: Evs; live: boolean }) => {
    h.current = useStepper(evs, live);
    return null;
  };
  act(() => root.render(createElement(C, { evs, live })));
  return (evs2: Evs, live2 = live) => act(() => root.render(createElement(C, { evs: evs2, live: live2 })));
}
const key = (k: string, init: KeyboardEventInit = {}, target: EventTarget = window) =>
  act(() => {
    target.dispatchEvent(new KeyboardEvent("keydown", { key: k, bubbles: true, cancelable: true, ...init }));
  });
const tipPos = () => h.current.state.beats.length - 1;

beforeEach(() => {
  window.history.replaceState(null, "", "/");
  box = document.createElement("div");
  document.body.append(box);
  root = createRoot(box);
});
afterEach(() => {
  act(() => root.unmount());
  box.remove();
});

describe("useStepper keys", () => {
  it("arrows, Home and End step beats", () => {
    mount();
    const last = tipPos();
    expect(h.current.state.pos).toBe(last);
    key("Home");
    expect(h.current.state.pos).toBe(0);
    key("ArrowRight");
    expect(h.current.state.pos).toBe(1);
    key("ArrowLeft");
    expect(h.current.state.pos).toBe(0);
    key("End");
    expect(h.current.state.pos).toBe(last);
  });
  it("ignores keys typed in inputs and textareas", () => {
    mount();
    for (const tag of ["input", "textarea"]) {
      const el = document.createElement(tag);
      document.body.append(el);
      key("Home", {}, el);
      expect(h.current.state.pos).toBe(tipPos());
      el.remove();
    }
  });
  it("ignores keys with modifiers", () => {
    mount();
    key("Home", { metaKey: true });
    key("Home", { ctrlKey: true });
    key("Home", { altKey: true });
    expect(h.current.state.pos).toBe(tipPos());
  });
});

describe("useStepper URL", () => {
  it("reads ?step= on load", () => {
    window.history.replaceState(null, "", "/?step=10");
    mount();
    expect(h.current.state.beats[h.current.state.pos].id).toBe(10);
  });
  it("writes ?step= with replaceState when stepping", () => {
    const len = window.history.length;
    mount();
    key("Home");
    expect(window.location.search).toBe("?step=0");
    key("ArrowRight");
    expect(window.location.search).toBe("?step=1");
    expect(window.history.length).toBe(len);
  });
  it("omits the param at the live tip, writes it when stepped back", () => {
    window.history.replaceState(null, "", "/?step=3");
    mount(events, true);
    key("End");
    expect(window.location.search).toBe("");
    key("ArrowLeft");
    expect(window.location.search).toMatch(/^\?step=\d+$/);
  });
});

describe("useStepper live append", () => {
  it("follows the tip", () => {
    const rerender = mount(events.slice(0, 18), true);
    const tip = tipPos();
    expect(h.current.state.pos).toBe(tip);
    rerender(events.slice(0, 25), true);
    expect(h.current.state.pos).toBe(tipPos());
    expect(tipPos()).toBeGreaterThan(tip);
    expect(h.current.atLatest).toBe(true);
  });
  it("stays put when stepped back and newCount grows", () => {
    const rerender = mount(events.slice(0, 18), true);
    key("ArrowLeft");
    const held = h.current.state.beats[h.current.state.pos].id;
    expect(h.current.newCount).toBe(1);
    rerender(events.slice(0, 25), true);
    const n1 = h.current.newCount;
    expect(h.current.state.beats[h.current.state.pos].id).toBe(held);
    expect(n1).toBeGreaterThan(1);
    rerender(events, true);
    expect(h.current.newCount).toBeGreaterThan(n1);
    expect(h.current.atLatest).toBe(false);
    act(() => h.current.goLive());
    expect(h.current.newCount).toBe(0);
  });
});
