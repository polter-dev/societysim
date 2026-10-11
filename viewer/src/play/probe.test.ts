// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BY_CODE, OFFLINE_MESSAGE, probePlay } from "./api";

const json = (body: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

describe("probePlay", () => {
  it("treats a non-JSON placeholder (HTTP 206) as offline without leaking text", async () => {
    fetchMock.mockImplementation(() => Promise.resolve(new Response("<html>paused</html>", { status: 206 })));
    expect(await probePlay()).toEqual({ kind: "offline", message: OFFLINE_MESSAGE });
  });
  it("is offline on network error and on timeout", async () => {
    fetchMock.mockRejectedValue(new TypeError("fail"));
    expect((await probePlay()).kind).toBe("offline");
    fetchMock.mockImplementation(
      (_u: string, i: { signal: AbortSignal }) =>
        new Promise((_r, rej) => i.signal.addEventListener("abort", () => rej(new Error("abort")))),
    );
    expect((await probePlay(10)).kind).toBe("offline");
  });
  it("is offline on JSON of the wrong shape or an unknown error", async () => {
    fetchMock.mockImplementation(() => json({ hello: 1 }));
    expect((await probePlay()).kind).toBe("offline");
    fetchMock.mockImplementation(() => json({ error: "boom" }, 500));
    expect((await probePlay()).kind).toBe("offline");
  });
  it("maps closed states to plain messages", async () => {
    for (const code of ["play_disabled", "budget_exhausted", "at_capacity"]) {
      fetchMock.mockImplementation(() => json({ error: code, message: "raw" }, 503));
      expect(await probePlay()).toEqual({ kind: "closed", code, message: BY_CODE[code] });
    }
    fetchMock.mockImplementation(() => json({ play_enabled: false, remaining_today: 3 }));
    expect((await probePlay()).kind).toBe("closed");
    fetchMock.mockImplementation(() => json({ play_enabled: true, remaining_today: 0 }));
    expect(await probePlay()).toMatchObject({ kind: "closed", code: "too_many_games_today" });
  });
  it("is online with limits", async () => {
    fetchMock.mockImplementation(() => json({ play_enabled: true, remaining_today: 4 }));
    expect(await probePlay()).toEqual({ kind: "online", limits: { play_enabled: true, remaining_today: 4 } });
  });
});
