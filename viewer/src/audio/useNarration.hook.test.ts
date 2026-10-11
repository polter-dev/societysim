// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Event } from "../types/events";
import fixture from "./g0043.narrator.json";
import { useNarration } from "./useNarration";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const events = fixture.events as unknown as Event[];
const bankJson = readFileSync(join(__dirname, "../../public/narration-bank/index.json"), "utf-8");
// the first death reveal of g0043 and its narrator clip
const reveal = fixture.clips.find((c) => c.text.startsWith("The wolves struck."))!;

let played: string[];
let audios: { onended: (() => void) | null }[];
let fetched: string[];
type Hook = ReturnType<typeof useNarration>;
let hook: Hook;
let root: Root;

type Props = { url: string | null; seq: number; last?: number; events: Event[] };
function Probe(p: Props) {
  hook = useNarration(p.url, { first: p.seq, last: p.last ?? p.seq }, "villager", p.events);
  return null;
}
// the bank path hashes with crypto.subtle, which resolves outside the microtask queue
const render = async (p: Props) => {
  await act(async () => root.render(createElement(Probe, p)));
  await act(() => new Promise((r) => setTimeout(r, 50)));
};

beforeEach(() => {
  played = [];
  audios = [];
  fetched = [];
  vi.stubGlobal(
    "Audio",
    class {
      src = "";
      onended = null;
      onerror = null;
      play() {
        played.push(this.src);
        return Promise.resolve();
      }
      pause() {}
      constructor() {
        audios.push(this);
      }
    },
  );
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      fetched.push(url);
      if (url === "narration-bank/index.json") return new Response(bankJson);
      if (url.endsWith("own.audio/index.json"))
        return new Response(JSON.stringify({ game_id: "x", clips: [{ seq: reveal.seq, category: "narrator", file: "own.mp3" }] }));
      return new Response("", { status: 404 });
    }),
  );
  root = createRoot(document.createElement("div"));
});
afterEach(() => {
  act(() => root.unmount());
  vi.unstubAllGlobals();
});

describe("useNarration", () => {
  it("a game without its own audio plays narrator lines from the bank, fetching nothing per game", async () => {
    await render({ url: null, seq: reveal.seq, events });
    expect(hook.available).toBe(true);
    await act(async () => hook.setOn(true));
    expect(played).toEqual([`narration-bank/${reveal.file}`]);
    expect(fetched).toEqual(["narration-bank/index.json"]);
  });

  it("a game with its own audio keeps it", async () => {
    await render({ url: "https://x.test/own.audio/index.json", seq: reveal.seq, events });
    await act(async () => hook.setOn(true));
    expect(played).toEqual(["https://x.test/own.audio/own.mp3"]);
  });

  it("an own index that fails to load falls back to the bank", async () => {
    await render({ url: "https://x.test/missing.audio/index.json", seq: reveal.seq, events });
    await act(async () => hook.setOn(true));
    expect(played).toEqual([`narration-bank/${reveal.file}`]);
  });

  it("live: new events do not restart the beat's clip", async () => {
    const upTo = events.filter((e) => e.seq <= reveal.seq);
    await render({ url: null, seq: reveal.seq, events: upTo });
    await act(async () => hook.setOn(true));
    await render({ url: null, seq: reveal.seq, events: events.filter((e) => e.seq <= reveal.seq + 5) });
    expect(played).toEqual([`narration-bank/${reveal.file}`]);
  });

  it("live: a new line in the playing beat queues after the clip instead of restarting it", async () => {
    const after = fixture.clips[fixture.clips.indexOf(reveal) + 1];
    const range = { url: null, seq: reveal.seq, last: after.seq };
    await render({ ...range, events: events.filter((e) => e.seq <= reveal.seq) });
    await act(async () => hook.setOn(true));
    expect(played).toEqual([`narration-bank/${reveal.file}`]);
    await render({ ...range, events: events.filter((e) => e.seq <= after.seq) });
    expect(played).toEqual([`narration-bank/${reveal.file}`]); // still on the first clip
    await act(async () => audios[0].onended?.());
    expect(played).toEqual([`narration-bank/${reveal.file}`, `narration-bank/${after.file}`]);
  });
});
