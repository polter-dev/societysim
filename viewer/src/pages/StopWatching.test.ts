// @vitest-environment jsdom
// Stop watching: watch token storage, the button and the pagehide path. Mocked server only.
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TOKEN_PREFIX, WATCH_PREFIX, startGame } from "../play/api";
import { PlayDock } from "./Play";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const me = {
  game_id: "g1", status: "running", winner: null, seat: "p0", name: "You", role: "villager", alive: true,
  day: 1, phase: "day", observation: "", pending_action: null, players: [],
};
let root: Root;
let box: HTMLElement;
let fetchMock: ReturnType<typeof vi.fn>;

const settle = async () => {
  for (let i = 0; i < 6; i++) await act(async () => void (await Promise.resolve()));
};
const render = async (el: Parameters<Root["render"]>[0]) => {
  act(() => root.render(el));
  await settle();
};
const leaves = () => fetchMock.mock.calls.filter(([u]) => String(u).endsWith("/leave"));

beforeEach(() => {
  vi.stubEnv("VITE_PLAY_API_URL", "https://play.example.test");
  fetchMock = vi.fn(async () => new Response(JSON.stringify({ accepted: true })));
  vi.stubGlobal("fetch", fetchMock);
  box = document.createElement("div");
  document.body.appendChild(box);
  root = createRoot(box);
});
afterEach(() => {
  act(() => root.unmount());
  box.remove();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  sessionStorage.clear();
});

describe("stop watching", () => {
  it("stores the watch token in sessionStorage when a watch game starts", async () => {
    fetchMock.mockImplementation(async () => new Response(JSON.stringify({ game_id: "w1", seat: null, seat_token: null, watch_token: "wt" })));
    await startGame({ setup: { mode: "basic", seed: 0 }, turnstile_token: "t", watch_only: true });
    expect(sessionStorage.getItem(WATCH_PREFIX + "w1")).toBe("wt");
    expect(sessionStorage.getItem(TOKEN_PREFIX + "w1")).toBeNull();
  });

  it("button calls /leave with the watch token and returns to the mode picker", async () => {
    sessionStorage.setItem(WATCH_PREFIX + "w1", "wt");
    const go = vi.fn();
    await render(createElement(PlayDock, { gameId: "w1", go }));
    expect(box.textContent).toContain("Stop watching");
    box.querySelector<HTMLButtonElement>("button.watch-stop")!.click();
    await settle();
    expect(leaves()).toHaveLength(1);
    expect(leaves()[0][0]).toBe("https://play.example.test/api/games/w1/leave");
    expect(leaves()[0][1].headers.Authorization).toBe("Bearer wt");
    expect(go).toHaveBeenCalledWith("?page=modes");
    expect(sessionStorage.getItem(WATCH_PREFIX + "w1")).toBeNull();
  });

  it("still returns to the picker when /leave fails", async () => {
    fetchMock.mockImplementation(async () => new Response("{}", { status: 500 }));
    sessionStorage.setItem(WATCH_PREFIX + "w1", "wt");
    const go = vi.fn();
    await render(createElement(PlayDock, { gameId: "w1", go }));
    box.querySelector<HTMLButtonElement>("button.watch-stop")!.click();
    await settle();
    expect(go).toHaveBeenCalledWith("?page=modes");
  });

  it("a reload (pagehide) never leaves: neither watch nor seated", async () => {
    sessionStorage.setItem(WATCH_PREFIX + "w1", "wt");
    await render(createElement(PlayDock, { gameId: "w1" }));
    window.dispatchEvent(new Event("pagehide"));
    expect(leaves()).toHaveLength(0);
    expect(sessionStorage.getItem(WATCH_PREFIX + "w1")).toBe("wt");
  });

  it("a seated player never gets the stop-watching button", async () => {
    sessionStorage.setItem(TOKEN_PREFIX + "g1", "tok");
    sessionStorage.setItem(WATCH_PREFIX + "g1", "wt");
    fetchMock.mockImplementation(async () => new Response(JSON.stringify(me)));
    await render(createElement(PlayDock, { gameId: "g1" }));
    window.dispatchEvent(new Event("pagehide"));
    expect(leaves()).toHaveLength(0);
    expect(box.textContent).not.toContain("Stop watching");
  });
});
