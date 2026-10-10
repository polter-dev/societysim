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

/** Our own wording per server error code. The server's `message` is never shown. */
export const BY_CODE: Record<string, string> = {
  rate_limited: "Slow down a little and try again in a minute.",
  too_many_games_today: "Too many games today. Try again tomorrow, or watch a replay.",
  active_game_exists: "You already have a game running. Finish it or wait for it to end.",
  at_capacity: "The server is full right now. Try again in a few minutes, or watch a replay.",
  bot_check_failed: "Couldn't verify you're human. Please retry the check.",
  forbidden: "You can't start a game from here. Watch a replay instead.",
  budget_exhausted: "Today's budget is used up. Watch a replay instead.",
  play_disabled: "Play is paused right now. Watch a replay instead.",
  invalid_request: "That request wasn't valid. Check your settings and try again.",
  not_your_turn: "It's not your turn.",
  game_not_found: "That game wasn't found. It may have ended.",
  unauthorized: "Your seat session has expired.",
  payload_too_large: "That was too long. Shorten it and try again.",
};

const BY_STATUS: Record<number, string> = {
  401: BY_CODE.unauthorized,
  403: BY_CODE.bot_check_failed,
  404: BY_CODE.game_not_found,
  409: BY_CODE.not_your_turn,
  413: BY_CODE.payload_too_large,
  422: BY_CODE.invalid_request,
  429: BY_CODE.rate_limited,
  503: "Play is closed right now (paused or out of budget). Watch a replay instead.",
};

export const friendlyMessage = (status: number, code = ""): string =>
  BY_CODE[code] ??
  BY_STATUS[status] ??
  (status >= 500 ? "The game server had a problem. Try again." : `Request failed (${status}).`);

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
    let code = "";
    try {
      const c = ((await res.json()) as { error?: unknown }).error;
      if (typeof c === "string") code = c;
    } catch {
      /* no JSON body */
    }
    throw new PlayError(res.status, friendlyMessage(res.status, code));
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
