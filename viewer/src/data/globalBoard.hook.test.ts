// @vitest-environment jsdom
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useGlobalBoard, type GlobalBoard, type GlobalBoardState } from "./globalBoard";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const board = (n: number): GlobalBoard => ({
  detectors: [{ model: `m${n}`, village_games: n, suspicion_gap: 0.5, votes: 1, wolf_vote_rate: 1, ranked: true }],
  liars: [],
  failed: [],
});

describe("useGlobalBoard", () => {
  let root: Root;
  let hidden = false;
  beforeEach(() => {
    vi.useFakeTimers();
    hidden = false;
    Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
    root = createRoot(document.createElement("div"));
  });
  afterEach(() => {
    act(() => root.unmount());
    vi.useRealTimers();
  });

  function mount(load: () => Promise<GlobalBoard>) {
    const seen: GlobalBoardState[] = [];
    const C = () => {
      seen.push(useGlobalBoard(load, 1000));
      return null;
    };
    act(() => root.render(createElement(C)));
    return seen;
  }

  const setHidden = async (h: boolean) => {
    hidden = h;
    await act(async () => void document.dispatchEvent(new Event("visibilitychange")));
  };

  it("loads at once, then every interval", async () => {
    let calls = 0;
    const seen = mount(async () => board(++calls));
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(calls).toBe(1);
    expect(seen.at(-1)!.board!.detectors[0].model).toBe("m1");
    await act(() => vi.advanceTimersByTimeAsync(1000));
    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(calls).toBe(3);
    expect(seen.at(-1)!.board!.detectors[0].model).toBe("m3");
  });

  it("stops while the tab is hidden and reloads when it is shown", async () => {
    let calls = 0;
    mount(async () => board(++calls));
    await act(() => vi.advanceTimersByTimeAsync(0));
    await setHidden(true);
    await act(() => vi.advanceTimersByTimeAsync(10_000));
    expect(calls).toBe(1); // the pending tick saw a hidden tab and stopped
    await setHidden(false);
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(calls).toBe(2);
    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(calls).toBe(3);
  });

  it("keeps the last board on a failed poll and retries", async () => {
    let calls = 0;
    const seen = mount(async () => {
      if (++calls === 2) throw new Error("HTTP 503");
      return board(calls);
    });
    await act(() => vi.advanceTimersByTimeAsync(0));
    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(seen.at(-1)).toMatchObject({ error: "HTTP 503", board: board(1) });
    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(seen.at(-1)).toEqual({ error: null, board: board(3) });
  });

  it("a refresh that fails for one tab keeps that tab's previous rows", async () => {
    let calls = 0;
    const seen = mount(async () =>
      ++calls === 2 ? { ...board(2), detectors: [], failed: ["detectors"] } : board(calls),
    );
    await act(() => vi.advanceTimersByTimeAsync(0));
    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(seen.at(-1)).toEqual({ error: null, board: board(1) });
  });

  it("a tab that fails with no previous rows shows the error", async () => {
    const failed: GlobalBoard = { ...board(1), detectors: [], failed: ["detectors"] };
    const seen = mount(async () => failed);
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(seen.at(-1)!.board).toEqual(failed);
  });
});
