// @vitest-environment jsdom
import { act, createElement, StrictMode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { SetupAssets } from "../../settings/setup";
import { PlayStart } from "./PlayStart";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const assets: SetupAssets = {
  catalog: {
    human: "human",
    default_lineup: ["m1"],
    models: [
      {
        name: "m1",
        display_name: "M1",
        provider: "x",
        sponsor: null,
        price_tier: "low",
        structured: "json",
        structured_ok: true,
        color: null,
        available: true,
        unavailable_reason: null,
      },
    ],
  },
  schema: {},
};
let root: Root;
let box: HTMLElement;
let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  (globalThis as { turnstile?: unknown }).turnstile = {
    render: (_el: unknown, o: { callback: (t: string) => void }) => {
      o.callback("ts-token");
      return "w1";
    },
  };
  fetchMock = vi.fn(
    async (url: string) =>
      new Response(
        JSON.stringify(
          url.endsWith("/limits")
            ? { play_enabled: true, remaining_today: 5 }
            : { game_id: "g1", seat: "p0", seat_token: "tok" },
        ),
      ),
  );
  vi.stubGlobal("fetch", fetchMock);
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

it("shows verification progress before starting and switches to server loading after the token", async () => {
  let verify: (token: string) => void = () => {};
  (globalThis as { turnstile?: unknown }).turnstile = { render: (_el: unknown, opts: { callback: (token: string) => void }) => { verify = opts.callback; return "waiting"; } };
  fetchMock.mockImplementation(async (url: string) => url.endsWith("/limits") ? new Response(JSON.stringify({ play_enabled: true, remaining_today: 5 })) : new Promise(() => {}));
  await act(async () => root.render(createElement(PlayStart, { onStarted: vi.fn(), modeLocked: true, autoStart: true })));
  expect(box.textContent).toContain("Your invitation awaits");
  expect(box.textContent).not.toMatch(/bot check to join|Checking you in/i);
  expect(box.querySelector(".play-gate-seal, .play-start--gate img:not(.play-arrival__art)")).toBeNull();
  expect(box.querySelector(".play-start__turnstile")).not.toBeNull(); // stays mounted so Cloudflare can show a challenge
  expect(box.querySelectorAll('.play-arrival__steps [data-state="current"]')[0]?.textContent).toBe("Bot check");
  expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/start"))).toBe(false);
  await act(async () => verify("verified"));
  expect(box.textContent).toContain("The village is waking");
  expect(box.querySelector('.play-arrival__steps [data-state="current"]')?.textContent).toBe("Enter the village");
});

const settle = async () => {
  for (let i = 0; i < 5; i++) await act(async () => void (await Promise.resolve()));
};

it("sends an edited Advanced setup to startGame", async () => {
  const onStarted = vi.fn();
  act(() => root.render(createElement(PlayStart, { onStarted, assets })));
  await settle();
  const adv = [...box.querySelectorAll("label")].find((l) => /Advanced/.test(l.textContent ?? ""))!;
  await act(async () => (adv.querySelector("input") as HTMLInputElement).click());
  await act(async () => (box.querySelector('input[type="radio"][name="mode"]:not(:checked)') as HTMLInputElement).click());
  const seats = box.querySelector('input[type="range"]') as HTMLInputElement; // the Seats slider
  const set = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
  await act(async () => {
    set.call(seats, "8");
    seats.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await settle();
  const start = [...box.querySelectorAll("button")].find((b) => /Start game/.test(b.textContent ?? ""))!;
  await act(async () => start.click());
  await settle();
  const post = fetchMock.mock.calls.find(([, i]) => i?.method === "POST")!;
  const body = JSON.parse(post[1].body);
  expect(body.setup.mode).toBe("advanced");
  expect(body.setup.n_players).toBe(8);
  expect(body.turnstile_token).toBe("ts-token");
  expect(onStarted).toHaveBeenCalled();
});

it("starts a selected Watch mode as an all-AI game", async () => {
  act(() => root.render(createElement(PlayStart, { onStarted: vi.fn(), initialMode: "watch" })));
  await settle();
  const radios = box.querySelectorAll<HTMLInputElement>('input[name="play-mode"]');
  expect(radios[1].checked).toBe(true);
  expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  const start = [...box.querySelectorAll("button")].find(b => b.textContent === "Start game")!;
  await act(async () => start.click());
  await settle();
  const post = fetchMock.mock.calls.find(([, init]) => init?.method === "POST")!;
  expect(JSON.parse(post[1].body).watch_only).toBe(true);
});

it("starts Normal once in StrictMode after the bot check, without another Start button", async () => {
  let verify: ((token: string) => void) | undefined;
  (globalThis as { turnstile?: unknown }).turnstile = {
    render: (_el: unknown, options: { callback: (token: string) => void }) => {
      verify = options.callback;
      return "w1";
    },
  };
  const onStarted = vi.fn();
  act(() => root.render(createElement(StrictMode, null, createElement(PlayStart, { onStarted, modeLocked: true, autoStart: true }))));
  await settle();
  expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  expect(box.querySelector("button")).toBeNull();
  await act(async () => verify!("verified-token"));
  await settle();
  expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
  expect(onStarted).toHaveBeenCalledTimes(1);
});

it.each([{ play_enabled: false, remaining_today: 5 }, { play_enabled: true, remaining_today: 0 }])(
  "does not automatically start when limits prohibit play: %j", async (limits) => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify(limits)));
    act(() => root.render(createElement(PlayStart, { onStarted: vi.fn(), autoStart: true, modeLocked: true })));
    await settle();
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false);
  },
);

it("shows a manual retry after a rejected automatic start and waits for a fresh token", async () => {
  let count = 0;
  (globalThis as { turnstile?: unknown }).turnstile = {
    render: (_el: unknown, options: { callback: (token: string) => void }) => {
      options.callback(`token-${++count}`);
      return `w${count}`;
    },
  };
  let posts = 0;
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
    if (url.endsWith("/limits")) return new Response(JSON.stringify({ play_enabled: true, remaining_today: 5 }));
    if (init?.method === "POST" && ++posts === 1) return new Response(JSON.stringify({ error: "bot_check_failed" }), { status: 403 });
    return new Response(JSON.stringify({ game_id: "g1", seat: "p0", seat_token: "tok" }));
  });
  const onStarted = vi.fn();
  act(() => root.render(createElement(PlayStart, { onStarted, autoStart: true, modeLocked: true })));
  await settle();
  expect(posts).toBe(1);
  expect(onStarted).not.toHaveBeenCalled();
  const retry = box.querySelector<HTMLButtonElement>("button")!;
  expect(retry.textContent).toBe("Try again");
  await act(async () => retry.click());
  await settle();
  expect(posts).toBe(2);
  const bodies = fetchMock.mock.calls.filter(([, init]) => init?.method === "POST").map(([, init]) => JSON.parse(init.body).turnstile_token);
  expect(bodies).toEqual(["token-1", "token-2"]);
  expect(onStarted).toHaveBeenCalledTimes(1);
});

it("focuses Try again after a failed bot check, then re-runs the check and starts once", async () => {
  let renders = 0;
  (globalThis as { turnstile?: unknown }).turnstile = {
    render: (_el: unknown, options: { callback: (t: string) => void; "error-callback": () => void }) => {
      if (++renders === 1) options["error-callback"]();
      else options.callback("fresh-token");
      return `w${renders}`;
    },
  };
  const onStarted = vi.fn();
  act(() => root.render(createElement(PlayStart, { onStarted, autoStart: true, modeLocked: true })));
  await settle();
  expect(box.querySelector('[role="alert"]')?.textContent).toBe("Turnstile check failed.");
  const retry = box.querySelector<HTMLButtonElement>("button")!;
  expect(retry.textContent).toBe("Try again");
  expect(retry.disabled).toBe(false);
  expect(document.activeElement).toBe(retry);
  await act(async () => retry.click());
  await settle();
  expect(renders).toBe(2);
  const posts = fetchMock.mock.calls.filter(([, init]) => init?.method === "POST");
  expect(posts.map(([, init]) => JSON.parse(init.body).turnstile_token)).toEqual(["fresh-token"]);
  expect(onStarted).toHaveBeenCalledTimes(1);
});

it("refetches limits from Try again when they failed to load", async () => {
  let limitCalls = 0;
  fetchMock.mockImplementation(async (url: string) => {
    if (url.endsWith("/limits") && ++limitCalls === 1) return new Response("{}", { status: 500 });
    return new Response(JSON.stringify(url.endsWith("/limits") ? { play_enabled: true, remaining_today: 5 } : { game_id: "g1", seat: "p0", seat_token: "tok" }));
  });
  const onStarted = vi.fn();
  act(() => root.render(createElement(PlayStart, { onStarted, autoStart: true, modeLocked: true })));
  await settle();
  expect(onStarted).not.toHaveBeenCalled();
  await act(async () => box.querySelector<HTMLButtonElement>("button")!.click());
  await settle();
  expect(limitCalls).toBe(2);
  expect(onStarted).toHaveBeenCalledTimes(1);
});

it("keeps selected Advanced mode recoverable when its catalog is unavailable", async () => {
  act(() => root.render(createElement(PlayStart, { onStarted: vi.fn(), initialMode: "advanced" })));
  await settle();
  const advanced = box.querySelector<HTMLInputElement>('input[type="checkbox"]')!;
  expect(advanced.checked).toBe(true);
  const start = [...box.querySelectorAll("button")].find(b => b.textContent === "Start game")!;
  expect(start.disabled).toBe(true);
  await act(async () => advanced.click());
  expect(start.disabled).toBe(false);
});

it.each([
  [503, "at_capacity"],
  [429, "rate_limited"],
  [409, "active_game_exists"],
])("gets a fresh Turnstile token after a rejected start (%s %s)", async (status, code) => {
  let n = 0;
  (globalThis as { turnstile?: unknown }).turnstile = {
    render: (_el: unknown, o: { callback: (t: string) => void }) => {
      o.callback(`ts-${++n}`);
      return `w${n}`;
    },
  };
  let rejected = false;
  fetchMock.mockImplementation(async (url: string) => {
    if (url.endsWith("/limits")) return new Response(JSON.stringify({ play_enabled: true, remaining_today: 5 }));
    if (!rejected) {
      rejected = true;
      return new Response(JSON.stringify({ error: code }), { status });
    }
    return new Response(JSON.stringify({ game_id: "g1", seat: "p0", seat_token: "tok" }));
  });
  const onStarted = vi.fn();
  act(() => root.render(createElement(PlayStart, { onStarted, initialMode: "watch" })));
  await settle();
  const start = [...box.querySelectorAll("button")].find((b) => b.textContent === "Start game")!;
  await act(async () => start.click());
  await settle();
  expect(onStarted).not.toHaveBeenCalled();
  expect(n).toBe(2);
  await act(async () => start.click());
  await settle();
  const posts = fetchMock.mock.calls.filter(([, i]) => i?.method === "POST").map(([, i]) => JSON.parse(i.body).turnstile_token);
  expect(posts).toEqual(["ts-1", "ts-2"]);
  expect(onStarted).toHaveBeenCalled();
});
