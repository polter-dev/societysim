// Typed client for the Play API (#280). The seat token lives in sessionStorage only and is sent
// only as a Bearer header: never in a URL, never logged.
import type { ActionBody, Limits, Me, StartGame, StartedGame } from "./types";

export const TOKEN_PREFIX = "societysim.seat_token.";

export class PlayError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
    this.name = "PlayError";
  }
}

const FRIENDLY: Record<number, string> = {
  429: "Too many games today. Try again tomorrow, or watch a replay.",
  403: "Couldn't verify you're human. Please retry the check.",
  409: "It's not your turn.",
  401: "Your seat session has expired.",
};

/** 503 is either "play is paused" or "today's budget is used up"; the server's detail text says which. */
function unavailable(detail: string): string {
  if (/budget|closed|today/i.test(detail)) return "Today's budget is used up. Watch a replay instead.";
  if (/paus|disabled/i.test(detail)) return "Play is paused right now. Watch a replay instead.";
  return "Play is closed right now (paused or out of budget). Watch a replay instead.";
}

export const friendlyMessage = (status: number, detail = ""): string =>
  (status === 503 ? unavailable(detail) : FRIENDLY[status]) ?? (status >= 500 ? "The game server had a problem. Try again." : `Request failed (${status}).`);

const baseUrl = () => ((import.meta.env.VITE_PLAY_API_URL as string | undefined) ?? "").replace(/\/$/, "");

export const saveToken = (gameId: string, token: string) => {
  try {
    sessionStorage.setItem(TOKEN_PREFIX + gameId, token);
  } catch {
    /* storage blocked: the visitor loses the seat on reload */
  }
};
export const loadToken = (gameId: string): string | null => {
  try {
    return sessionStorage.getItem(TOKEN_PREFIX + gameId);
  } catch {
    return null;
  }
};
export const clearToken = (gameId: string) => {
  try {
    sessionStorage.removeItem(TOKEN_PREFIX + gameId);
  } catch {
    /* ignore */
  }
};

async function call<T>(path: string, init: { method?: string; body?: unknown; token?: string | null } = {}): Promise<T> {
  const headers: Record<string, string> = {};
  if (init.body !== undefined) headers["Content-Type"] = "application/json";
  if (init.token) headers.Authorization = `Bearer ${init.token}`;
  let res: Response;
  try {
    res = await fetch(baseUrl() + path, {
      method: init.method ?? "GET",
      headers,
      body: init.body === undefined ? undefined : JSON.stringify(init.body),
    });
  } catch {
    throw new PlayError(0, "Can't reach the game server.");
  }
  if (!res.ok) {
    let detail = "";
    try {
      const d = ((await res.json()) as { detail?: unknown }).detail;
      if (typeof d === "string") detail = d;
    } catch {
      /* no JSON body */
    }
    throw new PlayError(res.status, friendlyMessage(res.status, detail));
  }
  return (await res.json()) as T;
}

/** Start a game. A seat token in the reply is stored for the game. */
export async function startGame(body: StartGame): Promise<StartedGame> {
  const g = await call<StartedGame>("/api/games", { method: "POST", body });
  if (g.seat_token) saveToken(g.game_id, g.seat_token);
  return g;
}
export const getMe = (gameId: string) =>
  call<Me>(`/api/games/${encodeURIComponent(gameId)}/me`, { token: loadToken(gameId) });
export const postAction = (gameId: string, body: ActionBody) =>
  call<{ accepted: true }>(`/api/games/${encodeURIComponent(gameId)}/action`, {
    method: "POST",
    body,
    token: loadToken(gameId),
  });
export const getLimits = () => call<Limits>("/api/limits");
