// @vitest-environment jsdom
import { act, createElement } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it } from "vitest";
import type { Event } from "../types/events";
import { publishLiveEvents, useLiveEvents } from "./liveEvents";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const ev = (game_id: string) => [{ game_id, seq: 0 }] as unknown as Event[];

describe("useLiveEvents", () => {
  it("returns the published events for its game, and nothing for another game", () => {
    const seen: number[] = [];
    function Probe({ id }: { id: string }) {
      seen.push(useLiveEvents(id).length);
      return null;
    }
    const box = document.createElement("div");
    const root = createRoot(box);
    act(() => root.render(createElement(Probe, { id: "visitor-1" })));
    act(() => publishLiveEvents(ev("visitor-1")));
    act(() => publishLiveEvents(ev("visitor-2")));
    expect(seen.at(-1)).toBe(0);
    act(() => publishLiveEvents(ev("visitor-1")));
    expect(seen.at(-1)).toBe(1);
    act(() => root.unmount());
  });
});
