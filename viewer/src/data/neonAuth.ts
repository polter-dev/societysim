// Anonymous JWT for the Neon Data API (docs/DATA_CONTRACT.md "Neon Data API").
// Never log the token.

const REFRESH_MARGIN_MS = 60_000;
const FALLBACK_TTL_MS = 5 * 60_000;

/** Accepts epoch seconds, epoch ms, numeric strings or ISO strings; null if unusable. */
export function parseExpiresAt(v: unknown): number | null {
  let n: number;
  if (typeof v === "number") n = v;
  else if (typeof v === "string" && v.trim() !== "") {
    n = Number(v);
    if (Number.isNaN(n)) n = Date.parse(v);
    else if (n < 0) return null;
    else n = n < 1e11 ? n * 1000 : n;
    return Number.isFinite(n) ? n : null;
  } else return null;
  if (!Number.isFinite(n) || n <= 0) return null;
  return n < 1e11 ? n * 1000 : n;
}

let cached: { token: string; expiresAt: number } | null = null;
let inflight: Promise<string> | null = null;

function env(name: "VITE_NEON_AUTH_URL" | "VITE_NEON_DATA_API_URL"): string {
  const v = import.meta.env[name] as string | undefined;
  if (!v) throw new Error(`${name} is not set; Neon live mode needs it`);
  return v.replace(/\/+$/, "");
}

async function fetchToken(): Promise<string> {
  const res = await fetch(`${env("VITE_NEON_AUTH_URL")}/token/anonymous`);
  if (!res.ok) throw new Error(`Neon auth failed: HTTP ${res.status}`);
  const body = (await res.json()) as { token?: string; expires_at?: unknown };
  if (!body.token) throw new Error("Neon auth returned no token");
  const expiresAt =
    parseExpiresAt(body.expires_at) ?? Date.now() + FALLBACK_TTL_MS;
  cached = { token: body.token, expiresAt };
  return body.token;
}

export function getAnonToken(force = false): Promise<string> {
  if (!force && cached && Date.now() < cached.expiresAt - REFRESH_MARGIN_MS) {
    return Promise.resolve(cached.token);
  }
  inflight ??= fetchToken().finally(() => {
    inflight = null;
  });
  return inflight;
}

export function resetNeonAuth(): void {
  cached = null;
  inflight = null;
}

/** fetch against VITE_NEON_DATA_API_URL + path with a bearer token; refreshes once on 400/401. */
export async function neonFetch(
  path: string,
  init: RequestInit = {},
): Promise<Response> {
  const url = env("VITE_NEON_DATA_API_URL") + path;
  const send = async (force: boolean) => {
    const headers = new Headers(init.headers);
    headers.set("Authorization", `Bearer ${await getAnonToken(force)}`);
    return fetch(url, { ...init, headers });
  };
  const res = await send(false);
  return res.status === 400 || res.status === 401 ? send(true) : res;
}
