// @vitest-environment jsdom
// Play wiring with a mocked Play API (fetch stub). No real server is ever called.
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TOKEN_PREFIX } from "../play/api";
import PlayPage, { PlayDock, PlayNavLink } from "./Play";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const me = {
  game_id: "g1", status: "running", winner: null, seat: "p0", name: "You", role: "villager", alive: true,
  day: 1, phase: "day", observation: "Day 1. Nothing has happened yet.", pending_action: null, players: [],
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

beforeEach(() => {
  fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url);
    if (u.endsWith("/api/limits")) return new Response(JSON.stringify({ play_enabled: true, remaining_today: 5 }));
    if (u.endsWith("/api/games") && init?.method === "POST")
      return new Response(JSON.stringify({ game_id: "visitor-1", seat: "p0", seat_token: "tok" }));
    if (u.endsWith("/me")) return new Response(JSON.stringify(me));
    return new Response("{}", { status: 404 });
  });
  vi.stubGlobal("fetch", fetchMock);
  (globalThis as { turnstile?: unknown }).turnstile = {
    render: (_el: unknown, o: { callback: (t: string) => void }) => (o.callback("ts-token"), "w1"),
  };
  vi.stubEnv("VITE_TURNSTILE_SITE_KEY", "site");
  box = document.createElement("div");
  document.body.appendChild(box);
  root = createRoot(box);
});
afterEach(() => {
  act(() => root.unmount());
  box.remove();
  delete (globalThis as { turnstile?: unknown }).turnstile;
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  sessionStorage.clear();
});

const page = (go = vi.fn()) =>
  createElement(PlayPage, { catalogUrl: "c.json", schemaUrl: "s.json", gamesHref: "?", gameHref: (id: string) => `?game=${id}`, go });

describe("VITE_PLAY_API_URL unset", () => {
  beforeEach(() => vi.stubEnv("VITE_PLAY_API_URL", ""));

  it("shows a disabled coming-soon nav entry, not a link", async () => {
    await render(createElement(PlayNavLink, { href: "?page=play" }));
    expect(box.querySelector("a")).toBeNull();
    expect(box.textContent).toContain("coming soon");
    expect(box.querySelector('[aria-disabled="true"]')).not.toBeNull();
  });

  it("shows coming soon on the play page and makes no network calls", async () => {
    await render(page());
    expect(box.textContent).toContain("coming soon");
    expect(box.querySelector("button")).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("renders no your-turn panel even with a seat token", async () => {
    sessionStorage.setItem(TOKEN_PREFIX + "g1", "tok");
    await render(createElement(PlayDock, { gameId: "g1" }));
    expect(box.innerHTML).toBe("");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("VITE_PLAY_API_URL set", () => {
  beforeEach(() => vi.stubEnv("VITE_PLAY_API_URL", "https://play.example.test"));

  it("shows a real Play link", async () => {
    await render(createElement(PlayNavLink, { href: "?page=play" }));
    expect(box.querySelector("a")?.getAttribute("href")).toBe("?page=play");
    expect(box.textContent).not.toContain("coming soon");
  });

  it("starts a game through the mocked API, stores the token, and opens the game page", async () => {
    const go = vi.fn();
    await render(page(go));
    const start = [...box.querySelectorAll("button")].find((b) => /Start game/.test(b.textContent ?? ""))! as HTMLButtonElement;
    expect(start.disabled).toBe(false);
    await act(async () => start.click());
    await settle();
    const post = fetchMock.mock.calls.find(([, i]) => i?.method === "POST")!;
    expect(post[0]).toBe("https://play.example.test/api/games");
    expect(JSON.parse(post[1].body)).toMatchObject({ turnstile_token: "ts-token", watch_only: false });
    expect(go).toHaveBeenCalledWith("?game=visitor-1");
    expect(sessionStorage.getItem(TOKEN_PREFIX + "visitor-1")).toBe("tok");
  });

  it("shows the friendly message when the server is full", async () => {
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) =>
      String(url).endsWith("/api/limits")
        ? new Response(JSON.stringify({ play_enabled: true, remaining_today: 5 }))
        : init?.method === "POST"
          ? new Response(JSON.stringify({ error: "at_capacity", message: "internal detail" }), { status: 503 })
          : new Response("{}", { status: 404 }),
    );
    const go = vi.fn();
    await render(page(go));
    const start = [...box.querySelectorAll("button")].find((b) => /Start game/.test(b.textContent ?? ""))!;
    await act(async () => start.click());
    await settle();
    expect(box.textContent).toContain("The server is full right now");
    expect(box.textContent).not.toContain("internal detail");
    expect(go).not.toHaveBeenCalled();
  });

  it("shows only the seat's own view in the your-turn panel, via /me", async () => {
    sessionStorage.setItem(TOKEN_PREFIX + "g1", "tok");
    await render(createElement(PlayDock, { gameId: "g1" }));
    expect(box.textContent).toContain("You");
    expect(box.textContent).not.toContain("(p0)");
    expect(box.textContent).toContain("Your role: villager");
    expect(box.textContent).toContain("Nothing has happened yet.");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("https://play.example.test/api/games/g1/me");
    expect(init.headers.Authorization).toBe("Bearer tok");
    expect(String(url)).not.toContain("tok");
  });

  it("resumes the seat after a refresh from the tab's stored token: no new start", async () => {
    // A reload keeps ?game= and sessionStorage; a fresh mount is what the page does after one.
    const vote = { kind: "vote", targets: ["p1"], others: ["p1"], deadline: Date.now() / 1000 + 60 };
    const players = [{ seat: "p0", name: "You", alive: true }, { seat: "p1", name: "Ava", alive: true }];
    fetchMock.mockImplementation(async () => new Response(JSON.stringify({ ...me, phase: "vote", pending_action: vote, players })));
    sessionStorage.setItem(TOKEN_PREFIX + "visitor-1", "tok");
    await render(createElement(PlayDock, { gameId: "visitor-1" }));
    expect(box.textContent).toContain("Your role: villager");
    expect(box.querySelector(".your-turn__form")).not.toBeNull();
    expect(box.textContent).toContain("Ava");
    const api = fetchMock.mock.calls.map(([u]) => String(u)).filter((u) => u.startsWith("https://play.example.test"));
    expect(api).toEqual(["https://play.example.test/api/games/visitor-1/me"]); // no /api/games start
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe("Bearer tok");
    expect(fetchMock.mock.calls.some(([, i]) => i?.method === "POST")).toBe(false);
  });

  it.each([404, 401])("shows the ended state with a start-new link when /me is %i", async (status) => {
    fetchMock.mockImplementation(async () => new Response(JSON.stringify({ error: "game_not_found" }), { status }));
    sessionStorage.setItem(TOKEN_PREFIX + "g1", "tok");
    await render(createElement(PlayDock, { gameId: "g1" }));
    expect(box.textContent).toContain("This game has ended.");
    expect(box.querySelector<HTMLAnchorElement>("a.your-turn__new")?.getAttribute("href")).toBe("?page=modes");
    expect(box.querySelector('[role="alert"]')).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("shows the ended state with a start-new link once the game is over", async () => {
    fetchMock.mockImplementation(async () => new Response(JSON.stringify({ ...me, status: "finished", winner: "villagers" })));
    sessionStorage.setItem(TOKEN_PREFIX + "g1", "tok");
    await render(createElement(PlayDock, { gameId: "g1" }));
    expect(box.textContent).toContain("Game finished: villagers win.");
    expect(box.textContent).toContain("This game has ended.");
    expect(box.querySelector("a.your-turn__new")).not.toBeNull();
  });

  it("gives spectators (no seat token) no panel and no /me call", async () => {
    await render(createElement(PlayDock, { gameId: "g1" }));
    expect(box.innerHTML).toBe("");
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
