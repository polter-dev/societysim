// @vitest-environment jsdom
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import fixture from "./__fixtures__/modes_game.jsonl?raw";
import { Replay } from "./App";
import { parseLog } from "./data/parse";
import type { Mode } from "./scene/deriveScene";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const events = parseLog(fixture).events;
const INDEX = "http://x.test/g.audio/index.json";
// seq 8 is a public death_reveal (own beat), 6-7 is the night kill beat, 2-5 are wolf-only events.
const clips = [
  { seq: 6, category: "narrator", speaker: "narrator", voice: "narrator_night", text: "night", file: "night.mp3" },
  { seq: 7, category: "narrator", speaker: "narrator", voice: "narrator", text: "save", file: "save.mp3" },
  { seq: 5, category: "wolf_chat", speaker: "p2", voice: "m", text: "wolf", file: "wolf.mp3" },
  { seq: 8, category: "narrator", speaker: "narrator", voice: "narrator", text: "dawn", file: "dawn.mp3" },
  { seq: 8, category: "highlight", speaker: "narrator", voice: "narrator", text: "hl", file: "hl.mp3" },
];

let root: Root;
let box: HTMLElement;
let played: string[];
let audio: { onended: (() => void) | null } | null;
/** Finish the clip that is playing, so the player moves on to the next one. */
const finishClips = () => act(() => { for (let i = 0; i < 5; i++) audio?.onended?.(); });

function mount(mode: Mode = "villager") {
  act(() =>
    root.render(
      createElement(Replay, { events, labels: [], mode, selected: null, onSelect: () => {}, audioUrl: INDEX }),
    ),
  );
}
const label = () => box.querySelector(".step-label")!.textContent;
const click = (sel: string) => act(() => void (box.querySelector(sel) as HTMLElement).click());
const key = (k: string, target: EventTarget = window) =>
  act(() => void target.dispatchEvent(new KeyboardEvent("keydown", { key: k, bubbles: true, cancelable: true })));
const flush = () => act(async () => void (await new Promise((r) => setTimeout(r, 0))));

beforeEach(() => {
  played = [];
  audio = null;
  window.history.replaceState(null, "", "/");
  vi.stubGlobal("fetch", async () => ({ ok: true, json: async () => ({ game_id: "g", clips }) }));
  vi.stubGlobal(
    "Audio",
    class {
      onended: (() => void) | null = null;
      constructor() {
        audio = this;
      }
      onerror = null;
      _src = "";
      set src(v: string) {
        this._src = v;
        played.push(v.split("/").pop()!);
      }
      get src() {
        return this._src;
      }
      play() {}
      pause() {}
    },
  );
  box = document.createElement("div");
  document.body.append(box);
  root = createRoot(box);
});
afterEach(() => {
  act(() => root.unmount());
  box.remove();
  vi.unstubAllGlobals();
});

describe("Replay stepping", () => {
  it("opens a finished game at its first beat and steps with the buttons and arrow keys", () => {
    mount();
    expect(label()).toMatch(/^beat 1 \//);
    expect(window.location.search).toBe("?step=0");
    click(".step-next");
    expect(label()).toMatch(/^beat 2 \//);
    key("ArrowRight");
    expect(label()).toMatch(/^beat 3 \//);
    key("ArrowLeft");
    expect(label()).toMatch(/^beat 2 \//);
  });

  it("lands on the beat containing ?step= and rewrites a highlight link's ?seq=", () => {
    window.history.replaceState(null, "", "/?game=g&seq=7&mode=narrator");
    mount("narrator");
    expect(window.location.search).toBe("?game=g&mode=narrator&step=6"); // seq 7 is inside the beat that starts at 6
    expect(box.querySelector(".step-label")!.textContent).not.toMatch(/^beat 1 /);
  });

  it("an arrow key with the timeline focused moves one beat, not two", () => {
    mount();
    const timeline = box.querySelector("section[aria-label='Game timeline']") as HTMLElement;
    key("ArrowRight", timeline);
    expect(label()).toMatch(/^beat 2 \//);
  });

  it("arrow keys, Home and End on the focused slider step by beat (seq-1 is still inside a multi-event beat)", () => {
    window.history.replaceState(null, "", "/?step=6&mode=narrator");
    mount("narrator");
    const slider = box.querySelector("input[aria-label='Event sequence']") as HTMLInputElement;
    const beat = () => Number(label()!.match(/^beat (\d+) \//)![1]);
    const here = beat();
    expect(here).toBeGreaterThan(1);
    const fire = (k: string) => {
      const e = new KeyboardEvent("keydown", { key: k, bubbles: true, cancelable: true });
      act(() => void slider.dispatchEvent(e));
      return e;
    };
    expect(fire("ArrowLeft").defaultPrevented).toBe(true);
    expect(beat()).toBe(here - 1);
    fire("ArrowRight");
    expect(beat()).toBe(here);
    fire("ArrowDown");
    expect(beat()).toBe(here - 1);
    fire("ArrowUp");
    expect(beat()).toBe(here);
    fire("Home");
    expect(beat()).toBe(1);
    fire("End");
    expect(label()).toMatch(/^beat (\d+) \/ \1/);
  });

  it("the timeline slider jumps through the stepper and the share link follows", () => {
    mount();
    const slider = box.querySelector("input[aria-label='Event sequence']") as HTMLInputElement;
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(slider, "8");
      slider.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(window.location.search).toBe("?step=8");
  });
});

describe("Replay narration", () => {
  async function turnOn(mode: Mode) {
    mount(mode);
    await flush();
    act(() => void (box.querySelector(".narration-controls input") as HTMLInputElement).click());
  }

  it("plays every clip in the current beat's seq range on step change, in order", async () => {
    window.history.replaceState(null, "", "/?step=6");
    await turnOn("villager");
    finishClips();
    expect(played).toEqual(["night.mp3", "save.mp3"]);
    click(".step-next"); // seq 8: dawn
    expect(played.at(-1)).toBe("dawn.mp3");
  });

  it("wolf chat and highlight clips play only in the narrator view", async () => {
    window.history.replaceState(null, "", "/?step=2");
    await turnOn("villager");
    expect(played).not.toContain("wolf.mp3");
    act(() => root.unmount());
    root = createRoot(box);
    played = [];
    window.history.replaceState(null, "", "/?step=2");
    await turnOn("narrator");
    const boxes = [...box.querySelectorAll<HTMLInputElement>(".narration-controls input")];
    for (const b of boxes.slice(1)) if (!b.checked) act(() => void b.click());
    expect(played).toContain("wolf.mp3");
  });

  it("shows a disabled narration toggle when the game has no audio", async () => {
    vi.stubGlobal("fetch", async () => ({ ok: false }));
    mount();
    await flush();
    expect(box.querySelector<HTMLInputElement>(".narration-controls input")?.disabled).toBe(true);
  });
});

describe("Replay live", () => {
  const render = (evs: typeof events, extra: { live?: boolean; atEnd?: boolean } = { live: true }) =>
    act(() =>
      root.render(
        createElement(Replay, { events: evs, labels: [], mode: "villager", selected: null, onSelect: () => {}, ...extra }),
      ),
    );
  const badge = () => box.querySelector(".live-badge")?.textContent;
  const beats = () => Number(label()!.split("/ ")[1]);
  const pos = () => Number(label()!.match(/^beat (\d+)/)![1]);

  it("opens at the latest beat, follows new events, and counts them once you step back", () => {
    window.history.replaceState(null, "", "/?game=g");
    render(events.slice(0, 12));
    expect(badge()).toBe("● Live");
    expect(pos()).toBe(beats());
    expect(window.location.search).toBe("?game=g"); // no ?step= while following

    render(events.slice(0, 20));
    expect(pos()).toBe(beats()); // still at the tip

    click(".step-prev");
    const stayed = pos();
    render(events);
    expect(pos()).toBe(stayed);
    const behind = beats() - stayed;
    expect(behind).toBeGreaterThan(1);
    expect(badge()).toBe(`● Live, ${behind} new`);
    expect(box.querySelector(".step-live")!.textContent).toBe(`Back to live (${behind} new)`);

    click(".step-live");
    expect(pos()).toBe(beats());
    expect(badge()).toBe("● Live");
  });

  it("a game that ended while watched opens at its end; a plain replay at its start", () => {
    window.history.replaceState(null, "", "/?game=g");
    render(events, { atEnd: true });
    expect(pos()).toBe(beats());
    expect(badge()).toBeUndefined();
    act(() => root.unmount());
    root = createRoot(box);
    window.history.replaceState(null, "", "/?game=g");
    render(events, {});
    expect(pos()).toBe(1);
  });
});
