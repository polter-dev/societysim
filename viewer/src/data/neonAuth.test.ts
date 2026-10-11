import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getAnonToken, parseExpiresAt, neonFetch, resetNeonAuth } from "./neonAuth";

const AUTH = "https://auth.test/neonauth";
const API = "https://api.test/rest/v1";
const tokenRes = (token: string, ms: number) =>
  new Response(JSON.stringify({ token, expires_at: new Date(Date.now() + ms).toISOString() }));

let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  vi.useFakeTimers();
  vi.stubEnv("VITE_NEON_AUTH_URL", AUTH);
  vi.stubEnv("VITE_NEON_DATA_API_URL", API);
  resetNeonAuth();
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("getAnonToken", () => {
  it("caches the token", async () => {
    fetchMock.mockImplementation(async () => tokenRes("t1", 3_600_000));
    expect(await getAnonToken()).toBe("t1");
    expect(await getAnonToken()).toBe("t1");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toBe(`${AUTH}/token/anonymous`);
  });

  it("refreshes about 60 s before expiry", async () => {
    fetchMock
      .mockImplementationOnce(async () => tokenRes("t1", 3_600_000))
      .mockImplementationOnce(async () => tokenRes("t2", 3_600_000));
    await getAnonToken();
    vi.advanceTimersByTime(3_600_000 - 61_000);
    expect(await getAnonToken()).toBe("t1");
    vi.advanceTimersByTime(2_000);
    expect(await getAnonToken()).toBe("t2");
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("shares one in-flight fetch between concurrent callers", async () => {
    fetchMock.mockImplementation(async () => tokenRes("t1", 3_600_000));
    const out = await Promise.all([getAnonToken(), getAnonToken(), getAnonToken()]);
    expect(out).toEqual(["t1", "t1", "t1"]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("gives a clear error when the env var is missing", async () => {
    vi.stubEnv("VITE_NEON_AUTH_URL", "");
    await expect(getAnonToken()).rejects.toThrow("VITE_NEON_AUTH_URL");
  });
});

describe("neonFetch", () => {
  it("adds the bearer header and base url", async () => {
    fetchMock.mockImplementation(async (url: string) =>
      url.startsWith(AUTH) ? tokenRes("t1", 3_600_000) : new Response("[]"),
    );
    const res = await neonFetch("/games?select=*", { headers: { Accept: "application/json" } });
    expect(res.status).toBe(200);
    const [url, init] = fetchMock.mock.calls[1];
    expect(url).toBe(`${API}/games?select=*`);
    const h = new Headers(init.headers);
    expect(h.get("Authorization")).toBe("Bearer t1");
    expect(h.get("Accept")).toBe("application/json");
  });

  it("refreshes once and retries on 401", async () => {
    let n = 0;
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url.startsWith(AUTH)) return tokenRes(`t${++n}`, 3_600_000);
      const auth = new Headers(init?.headers).get("Authorization");
      return new Response("", { status: auth === "Bearer t2" ? 200 : 401 });
    });
    const res = await neonFetch("/events");
    expect(res.status).toBe(200);
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  it("does not retry more than once", async () => {
    fetchMock.mockImplementation(async (url: string) =>
      url.startsWith(AUTH) ? tokenRes("t", 3_600_000) : new Response("", { status: 401 }),
    );
    const res = await neonFetch("/events");
    expect(res.status).toBe(401);
    expect(fetchMock).toHaveBeenCalledTimes(4);
  });

  it("errors clearly when the data API url is missing", async () => {
    vi.stubEnv("VITE_NEON_DATA_API_URL", "");
    await expect(neonFetch("/games")).rejects.toThrow("VITE_NEON_DATA_API_URL");
  });
});

describe("parseExpiresAt", () => {
  it("handles epoch seconds, ms, numeric strings and ISO", () => {
    expect(parseExpiresAt(1791685280)).toBe(1791685280000);
    expect(parseExpiresAt(1791685280000)).toBe(1791685280000);
    expect(parseExpiresAt("1791685280")).toBe(1791685280000);
    expect(parseExpiresAt("2026-10-10T12:00:00Z")).toBe(Date.parse("2026-10-10T12:00:00Z"));
  });
  it("returns null for missing or garbage", () => {
    for (const v of [undefined, null, "", "nope", -5, 0, {}]) expect(parseExpiresAt(v)).toBeNull();
  });
});

describe("getAnonToken expiry fallback", () => {
  it.each([[undefined], ["garbage"]])("uses a short TTL when expires_at is %s", async (bad) => {
    fetchMock.mockImplementation(async () => new Response(JSON.stringify({ token: "t1", expires_at: bad })));
    expect(await getAnonToken()).toBe("t1");
    expect(await getAnonToken()).toBe("t1");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(5 * 60_000);
    await getAnonToken();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("accepts epoch seconds like the live endpoint", async () => {
    const sec = Math.floor((Date.now() + 3_600_000) / 1000);
    fetchMock.mockImplementation(async () => new Response(JSON.stringify({ token: "t1", expires_at: sec })));
    await getAnonToken();
    await getAnonToken();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
