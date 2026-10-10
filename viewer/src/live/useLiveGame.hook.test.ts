// @vitest-environment jsdom
// jsdom can't import the repo-root fixture, so this uses the viewer's own (with a game_end added).
import { act, createElement, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import fixture from "../__fixtures__/modes_game.jsonl?raw";
import { parseLog } from "../data/parse";
import type { GameSource, LoadedGame } from "../data/source";
import type { Event } from "../types/events";
import { useLiveGame } from "./useLiveGame";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const base = parseLog(fixture).events;
const last = base.at(-1)!;
const end: Event = { ...last, seq: last.seq + 1, type: "game_end", visibility: "public", actor: null, target: null, meta: { winner: "draw" } };
const full = [...base, end];
const pub = full.filter((e) => e.visibility === "public");
const liveLog = (n: number): LoadedGame => ({ events: pub.slice(0, n), errors: [], live: true, partial: true });

function fake(opts: { shown: () => number }) {
  const calls = { poll: 0 };
  const source: GameSource = {
    listGames: async () => [],
    pollEvents: async (_id, after) => {
      calls.poll++;
      return pub.slice(0, opts.shown()).filter((e) => e.seq > after);
    },
    gameStatus: async () => "running",
    loadGame: async () => ({ events: full, errors: [] }),
  };
  return { source, calls };
}

describe("useLiveGame", () => {
  let root: Root;
  let box: HTMLElement;
  beforeEach(() => {
    vi.useFakeTimers();
    box = document.createElement("div");
    root = createRoot(box);
  });
  afterEach(() => {
    act(() => root.unmount());
    vi.useRealTimers();
  });

  it("polls every interval until the game ends", async () => {
    let shown = 5;
    const { source, calls } = fake({ shown: () => shown });
    const seen: LoadedGame[] = [];
    const C = () => {
      const [log, setLog] = useState<LoadedGame | null>(liveLog(5));
      useLiveGame(source, log, setLog, 1000);
      if (log) seen.push(log);
      return null;
    };
    act(() => root.render(createElement(C)));
    shown = 9;
    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(seen.at(-1)!.events.length).toBe(9);
    shown = pub.length;
    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(seen.at(-1)!.events).toEqual(full);
    const polls = calls.poll;
    await act(() => vi.advanceTimersByTimeAsync(5000));
    expect(calls.poll).toBe(polls); // stopped
  });

  it("does nothing for a finished game", async () => {
    const { source, calls } = fake({ shown: () => 5 });
    const C = () => {
      const [log, setLog] = useState<LoadedGame | null>({ events: full, errors: [] });
      useLiveGame(source, log, setLog, 1000);
      return null;
    };
    act(() => root.render(createElement(C)));
    await act(() => vi.advanceTimersByTimeAsync(5000));
    expect(calls.poll).toBe(0);
  });
});
