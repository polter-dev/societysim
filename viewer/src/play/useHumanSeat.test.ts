// @vitest-environment jsdom
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TOKEN_PREFIX } from "./api";
import type { Me } from "./types";
import { useHumanSeat } from "./useHumanSeat";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const baseMe = (pending: Me["pending_action"]): Me => ({
  game_id: "g1",
  status: "running",
  winner: null,
  seat: "p0",
  name: "You",
  role: "villager",
  alive: true,
  day: 1,
  phase: "day",
  observation: null,
  pending_action: pending,
  players: [],
});
const vote = { kind: "vote" as const, targets: ["p1", "p2"], others: ["p1", "p2"], deadline: Date.now() / 1000 + 30 };

let root: Root;
let box: HTMLElement;
let h: { current: ReturnType<typeof useHumanSeat> };
let fetchMock: ReturnType<typeof vi.fn>;
let state: Me;

beforeEach(() => {
  vi.useFakeTimers();
  sessionStorage.setItem(TOKEN_PREFIX + "g1", "tok");
  state = baseMe(vote);
  fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      state = baseMe(null);
      return new Response(JSON.stringify({ accepted: true }), { status: 200 });
    }
    return new Response(JSON.stringify(state), { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  box = document.createElement("div");
  root = createRoot(box);
  h = { current: undefined as never };
  const C = () => {
    h.current = useHumanSeat("g1", 1000);
    return null;
  };
  act(() => root.render(createElement(C)));
});
afterEach(() => {
  act(() => root.unmount());
  vi.useRealTimers();
  vi.unstubAllGlobals();
  sessionStorage.clear();
});

const flush = async () => {
  for (let i = 0; i < 5; i++) await act(async () => void (await vi.advanceTimersByTimeAsync(0)));
};

describe("useHumanSeat", () => {
  it("shows a pending action, accepts a submit, then clears it", async () => {
    await flush();
    expect(h.current.pending?.kind).toBe("vote");
    expect(h.current.secondsLeft).toBeGreaterThan(0);

    let ok = true;
    await act(async () => {
      ok = await h.current.submit({ target: "p9" });
    });
    expect(ok).toBe(false);
    expect(h.current.errors.length).toBeGreaterThan(0);
    expect(fetchMock.mock.calls.some(([, i]) => i?.method === "POST")).toBe(false);

    await act(async () => {
      ok = await h.current.submit({ target: "p1" });
    });
    expect(ok).toBe(true);
    expect(h.current.pending).toBeNull();
    const post = fetchMock.mock.calls.find(([, i]) => i?.method === "POST");
    expect(JSON.parse(post![1].body).action).toEqual({ vote: "p1" });
    expect(post![1].headers.Authorization).toBe("Bearer tok");
  });

  it("picks up a new pending action on the next poll", async () => {
    await flush();
    state = baseMe(null);
    await act(async () => void (await vi.advanceTimersByTimeAsync(1000)));
    await flush();
    expect(h.current.pending).toBeNull();
    state = baseMe(vote);
    await act(async () => void (await vi.advanceTimersByTimeAsync(1000)));
    await flush();
    expect(h.current.pending?.kind).toBe("vote");
  });

  it("surfaces a friendly error when the turn was missed (409)", async () => {
    await flush();
    fetchMock.mockImplementation(async () => new Response("{}", { status: 409 }));
    await act(async () => void (await h.current.submit({ target: "p1" })));
    expect(h.current.error).toMatch(/not your turn/i);
  });

  it("stops polling once the game is over", async () => {
    await flush();
    state = { ...baseMe(null), status: "finished", winner: "villagers" };
    await act(async () => void (await vi.advanceTimersByTimeAsync(1000)));
    await flush();
    expect(h.current.me?.status).toBe("finished");
    const n = fetchMock.mock.calls.length;
    await act(async () => void (await vi.advanceTimersByTimeAsync(5000)));
    expect(fetchMock.mock.calls.length).toBe(n);
  });
});
