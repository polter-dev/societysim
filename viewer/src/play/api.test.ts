// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LEVELS, buildAction, playerLabel } from "./action";
import { BY_CODE, PlayError, TOKEN_PREFIX, getLimits, getMe, postAction, startGame } from "./api";
import type { PendingAction } from "./types";

const TOKEN = "tok-secret-123";
const json = (body: unknown, status = 200) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } }));
let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

describe("api client", () => {
  it("starts a game and keeps the token in sessionStorage only", async () => {
    fetchMock.mockReturnValue(json({ game_id: "g1", seat: "p0", seat_token: TOKEN }));
    const g = await startGame({ setup: { mode: "basic", seed: 0 }, turnstile_token: "t", watch_only: false });
    expect(g.game_id).toBe("g1");
    expect(sessionStorage.getItem(TOKEN_PREFIX + "g1")).toBe(TOKEN);
    expect(localStorage.length).toBe(0);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/games");
    expect(init.method).toBe("POST");
    expect(init.headers.Authorization).toBeUndefined();
  });

  it("sends the token as a Bearer header and never in a URL or log", async () => {
    sessionStorage.setItem(TOKEN_PREFIX + "g1", TOKEN);
    const log = vi.spyOn(console, "log").mockImplementation(() => {});
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const err = vi.spyOn(console, "error").mockImplementation(() => {});
    fetchMock.mockImplementation(() => json({ accepted: true }));
    await getMe("g1");
    await postAction("g1", { private_reasoning: { thoughts: "", suspicion: [] }, action: { vote: "p1" } });
    for (const [url, init] of fetchMock.mock.calls) {
      expect(url).not.toContain(TOKEN);
      expect(init.headers.Authorization).toBe(`Bearer ${TOKEN}`);
      expect(String(init.body ?? "")).not.toContain(TOKEN);
    }
    expect(fetchMock.mock.calls[0][0]).toBe("/api/games/g1/me");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/games/g1/action");
    for (const spy of [log, warn, err]) expect(JSON.stringify(spy.mock.calls)).not.toContain(TOKEN);
    log.mockRestore();
    warn.mockRestore();
    err.mockRestore();
  });

  it("does not send a token to /api/limits", async () => {
    fetchMock.mockReturnValue(json({ play_enabled: true, remaining_today: 3 }));
    expect(await getLimits()).toEqual({ play_enabled: true, remaining_today: 3 });
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBeUndefined();
  });

  it.each(Object.entries(BY_CODE))("maps error code %s to our own text, not the server's message", async (code, text) => {
    fetchMock.mockReturnValue(json({ error: code, message: "SERVER WORDING" }, 400));
    const e = await getLimits().catch((x) => x);
    expect(e).toBeInstanceOf(PlayError);
    expect(e.message).toBe(text);
    expect(e.message).not.toContain("SERVER");
  });

  it("covers every code the server returns", () => {
    expect(Object.keys(BY_CODE).sort()).toEqual(
      [
        "rate_limited", "too_many_games_today", "active_game_exists", "at_capacity", "bot_check_failed",
        "forbidden", "budget_exhausted", "play_disabled", "invalid_request", "not_your_turn",
        "game_not_found", "unauthorized", "payload_too_large",
      ].sort(),
    );
  });

  it.each([
    [429, /slow down/i],
    [403, /verify you're human/i],
    [503, /closed right now/i],
    [409, /not your turn/i],
  ])("falls back to status %i when there is no error code", async (status, re) => {
    fetchMock.mockReturnValue(json({ detail: "x" }, status));
    const e = await getLimits().catch((x) => x);
    expect(e.status).toBe(status);
    expect(e.message).toMatch(re);
  });

  it("reports a network failure", async () => {
    fetchMock.mockRejectedValue(new TypeError("boom"));
    await expect(getLimits()).rejects.toThrow(/can't reach/i);
  });
});

const pending = (kind: PendingAction["kind"]): PendingAction => ({
  kind,
  targets: ["p1", "p2"],
  others: ["p1", "p2", "p3"],
  deadline: 0,
});

describe("buildAction", () => {
  it("statement: no ratings means no scratchpad at all", () => {
    const r = buildAction(pending("statement"), { text: " hi ", accuse: ["p1"] });
    expect(r.ok && r.body).toEqual({
      action: { text: "hi", accuse: ["p1"], claim_role: null, seer_report: null },
    });
  });

  it("sends only the players the visitor rated, never a default", () => {
    const r = buildAction(pending("vote"), { target: "p1", suspicion: { p3: "suspicious", p2: "trust", p1: undefined } });
    expect(r.ok && r.body).toEqual({
      private_reasoning: { suspicion: [{ player: "p2", p: LEVELS.trust }, { player: "p3", p: LEVELS.suspicious }] },
      action: { vote: "p1" },
    });
  });

  it("rejects empty, over-long and control-only statements", () => {
    expect(buildAction(pending("statement"), { text: "" }).ok).toBe(false);
    expect(buildAction(pending("statement"), { text: "\u0007\n" }).ok).toBe(false);
    expect(buildAction(pending("statement"), { text: "x".repeat(281) }).ok).toBe(false);
    expect(buildAction(pending("statement"), { text: "x".repeat(280) }).ok).toBe(true);
  });

  it("rejects targets and accusations outside the allowed list", () => {
    expect(buildAction(pending("vote"), { target: "p3" }).ok).toBe(false);
    expect(buildAction(pending("vote"), {}).ok).toBe(false);
    expect(buildAction(pending("statement"), { text: "hi", accuse: ["p3"] }).ok).toBe(false);
    const r = buildAction(pending("vote"), { target: "p2" });
    expect(r.ok && r.body.action).toEqual({ vote: "p2" });
  });

  it("wolf chat and night actions use their shapes", () => {
    const w = buildAction(pending("wolf_chat"), { text: "kill p1", target: "p1" });
    expect(w.ok && w.body.action).toEqual({ message: "kill p1", proposed_target: "p1" });
    for (const k of ["seer", "doctor", "lone_wolf"] as const) {
      const r = buildAction(pending(k), { target: "p1" });
      expect(r.ok && r.body.action).toEqual({ target: "p1" });
    }
  });

  it("labels players by name", () => {
    const players = [{ seat: "p1", name: "Ava", alive: true }];
    expect(playerLabel(players, "p1")).toBe("Ava (p1)");
    expect(playerLabel(players, "abstain")).toBe("Abstain");
  });
});
