// @vitest-environment jsdom
import { act, createElement } from "react";
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
  const seats = box.querySelector('input[type="number"]') as HTMLInputElement;
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
