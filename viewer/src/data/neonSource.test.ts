import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import fixture from "../../../fixtures/sample_game.jsonl?raw";
import labelsText from "../../../fixtures/sample_game.labels.jsonl?raw";
import { Game } from "../App";
import type { Event } from "../types/events";
import { resetNeonAuth } from "./neonAuth";
import { neonSource, withFallback } from "./neonSource";
import { parseLog } from "./parse";
import type { GameSource } from "./source";

const AUTH = "https://auth.test/neonauth";
const API = "https://api.test/rest/v1";
const ID = "fixture-0001";
const full = parseLog(fixture).events;
const MODELS = Object.values(full[0].meta.seats!).map((s) => s.model);
const labelRows = labelsText.trim().split("\n").map((l) => ({ label: JSON.parse(l) }));

interface GameRow {
  status: string;
  models: unknown;
  agents?: string | null;
  winner?: string | null;
  rows: Event[];
}

/**
 * A tiny PostgREST stand-in: games, events (with `alias:event->field` selects and the
 * `event->>visibility` filter), labels and leaderboard. Records every Data API URL asked for.
 */
function fakeNeon(games: Record<string, GameRow>, endMode: "ok" | "deny" | "hang" = "ok") {
  const asked: string[] = [];
  const json = (v: unknown) => new Response(JSON.stringify(v));
  vi.stubGlobal("fetch", async (input: string) => {
    if (input === `${AUTH}/token/anonymous`)
      return json({ token: "jwt", expires_at: new Date(Date.now() + 3_600_000).toISOString() });
    asked.push(input);
    const url = new URL(input);
    const p = url.searchParams;
    const table = url.pathname.split("/").at(-1);
    const id = p.get("game_id")?.replace(/^eq\./, "");
    if (table === "games") {
      const all = Object.entries(games);
      if (id) return json(all.filter(([g]) => g === id).map(([, g]) => ({ status: g.status, models: g.models })));
      if (p.get("status") === "eq.finished")
        return json(all.filter(([, g]) => g.status === "finished" && g.agents !== "scripted").map(([g]) => ({ game_id: g })));
      return json(all.map(([g, v]) => ({ game_id: g, models: v.models, status: v.status, winner: v.winner ?? null })));
    }
    if (table === "events_public" && p.get("type") === "eq.game_end") {
      if (endMode === "deny") return new Response("", { status: 403 });
      if (endMode === "hang") return new Promise<Response>(() => {});
      const ids = p.get("game_id")!.replace(/^in\.\(|\)$/g, "").split(",");
      return json(
        Object.entries(games)
          .filter(([g, v]) => v.status === "finished" && ids.includes(g))
          .map(([g, v]) => ({ game_id: g, day: v.rows.find((e) => e.type === "game_end")?.day })),
      );
    }
    if (table === "events_public") {
      let rows = games[id!]?.rows ?? [];
      const after = p.get("seq")?.replace(/^gt\./, "");
      if (after !== undefined) rows = rows.filter((e) => e.seq > Number(after));
      const vis = p.get("event->>visibility")?.replace(/^eq\./, "");
      if (vis) rows = rows.filter((e) => e.visibility === vis);
      const select = p.get("select")!;
      if (select === "event") return json(rows.map((e) => ({ event: e })));
      const fields = select.split(",").map((f) => f.match(/^(\w+):event->(\w+)$/)!.slice(1));
      return json(rows.map((e) => Object.fromEntries(fields.map(([alias, f]) => [alias, e[f as keyof Event]]))));
    }
    if (table === "labels_public") return json(id === ID ? labelRows : []);
    if (table === "leaderboard")
      return json([
        { model: "a", seat_games: 14, win_rate: "0.571" },
        { model: "b", seat_games: 7, win_rate: null },
      ]);
    return new Response("", { status: 404 });
  });
  return asked;
}

const running = (n = 30): GameRow => ({ status: "running", models: MODELS, rows: full.slice(0, n) });
const secrets = full
  .flatMap((e) => [e.private_reasoning, e.visibility !== "public" ? e.public_text : null])
  .filter((s): s is string => !!s && s.length >= 12);

beforeEach(() => {
  vi.stubEnv("VITE_NEON_AUTH_URL", AUTH);
  vi.stubEnv("VITE_NEON_DATA_API_URL", API);
  resetNeonAuth();
});
afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("neonSource, finished games", () => {
  it("loads the whole log and its labels", async () => {
    fakeNeon({ [ID]: { status: "finished", models: MODELS, rows: full } });
    const src = neonSource();
    const game = await src.loadGame(ID);
    expect(game.events).toEqual(full);
    expect(game.live).toBeFalsy();
    expect(game.partial).toBeFalsy();
    expect((await src.loadLabels!(ID)).length).toBe(labelRows.length);
  });

  it("lists games with models, a live flag and finished winners only, never roles", async () => {
    const asked = fakeNeon({
      [ID]: { status: "finished", models: MODELS, rows: full, winner: "wolves" },
      g2: { ...running(), winner: "villagers" },
    });
    const games = await neonSource().listGames();
    expect(asked[0]).toBe(`${API}/games?select=game_id,models,status,winner&order=started_at.desc.nullslast`);
    expect(games.find((g) => g.id === "g2")).not.toHaveProperty("winner");
    expect(games.find((g) => g.id === ID)).toEqual({
      id: ID,
      models: [...new Set(MODELS)].sort(),
      live: false,
      winner: "wolves",
      days: full.find((e) => e.type === "game_end")!.day,
    });
    expect(games.find((g) => g.id === "g2")).not.toHaveProperty("days");
  });

  it("lists games with no days when the game_end lookup is denied", async () => {
    fakeNeon({ [ID]: { status: "finished", models: MODELS, rows: full, winner: "wolves" } }, "deny");
    const games = await neonSource().listGames();
    expect(games).toHaveLength(1);
    expect(games[0].days).toBeUndefined();
  });

  it("lists games with no days when the game_end lookup hangs", async () => {
    vi.useFakeTimers();
    try {
      fakeNeon({ [ID]: { status: "finished", models: MODELS, rows: full, winner: "wolves" } }, "hang");
      const pending = neonSource().listGames();
      await vi.advanceTimersByTimeAsync(2500);
      const games = await pending;
      expect(games[0].id).toBe(ID);
      expect(games[0].days).toBeUndefined();
    } finally {
      vi.useRealTimers();
    }
  });

  it("scopes the days lookup to finished ids and skips it when none finished", async () => {
    let asked = fakeNeon({ [ID]: { status: "finished", models: MODELS, rows: full }, g2: running() });
    await neonSource().listGames();
    expect(asked.at(-1)).toBe(`${API}/events_public?select=game_id,day&type=eq.game_end&game_id=in.(${ID})&limit=1`);
    asked = fakeNeon({ g2: running() });
    await neonSource().listGames();
    expect(asked.some((u) => u.includes("game_end"))).toBe(false);
  });

  it("builds the board from the leaderboard view and counts finished non-scripted games", async () => {
    const asked = fakeNeon({
      a: { status: "finished", models: [], rows: [] },
      b: { status: "finished", models: [], rows: [], agents: "scripted" },
      c: running(),
    });
    const board = await neonSource().loadBoard();
    expect(board.games).toBe(1);
    expect(board.models[0]).toMatchObject({ model: "a", seats: 14, win_rate: 0.571, games: null });
    expect(board.models[1].win_rate).toBeNull();
    expect(asked).toContain(`${API}/games?select=game_id&status=eq.finished&or=(agents.is.null,agents.neq.scripted)`);
  });
});

describe("neonSource, running games (hidden information)", () => {
  it("asks only for public fields of public events", async () => {
    const asked = fakeNeon({ [ID]: running() });
    const src = neonSource();
    const game = await src.loadGame(ID);
    await src.pollEvents(ID, game.events.at(-1)!.seq);
    await src.loadLabels!(ID);
    const events = asked.filter((u) => u.includes("/events_public?"));
    expect(events.length).toBe(2);
    for (const u of events) {
      expect(u).toContain("event->>visibility=eq.public");
      expect(u).not.toMatch(/select=event&|private_reasoning|suspicion/);
    }
    expect(asked.some((u) => u.includes("/labels_public?"))).toBe(false);
    expect(asked.some((u) => /\/(events|labels)\?/.test(u))).toBe(false); // anonymous can't read them (#323)
  });

  it("returns no private text and no roles", async () => {
    fakeNeon({ [ID]: running() });
    const game = await neonSource().loadGame(ID);
    expect(game.live).toBe(true);
    expect(game.partial).toBe(true);
    const text = JSON.stringify(game);
    for (const s of secrets) expect(text).not.toContain(s);
    expect(Object.values(game.events[0].meta.seats!).some((s) => "role" in s)).toBe(false);
    // the only roles are public claims (role_claim), never a dead or hidden seat's role
    expect(game.events.filter((e) => e.meta.role).map((e) => e.type)).toEqual(["role_claim"]);
    expect(game.events.every((e) => e.visibility === "public" && e.private_reasoning === null && e.suspicion === null)).toBe(true);
  });

  it("rebuilds the seat list from games.models, with Seat N for a row without names", async () => {
    fakeNeon({ [ID]: running() });
    const [start, next] = (await neonSource().loadGame(ID)).events;
    expect(start.type).toBe("game_start");
    expect(start.seq).toBeLessThan(next.seq);
    expect(start.meta.seats!.p2).toEqual({ name: "Seat 3", model: MODELS[2], color: expect.any(String) });
  });

  // #400: the sink puts [{model, name}] on the games row at game_start; names are public.
  const NAMED = Object.values(full[0].meta.seats!).map(({ model, name }) => ({ model, name }));
  const NAMES = NAMED.map((s) => s.name);

  it("takes the real names from games.models, never a role", async () => {
    fakeNeon({ [ID]: { ...running(), models: NAMED } });
    const { events } = await neonSource().loadGame(ID);
    const seats = events[0].meta.seats!;
    expect(Object.values(seats).map((s) => s.name)).toEqual(NAMES);
    expect(seats.p2).toEqual({ name: NAMED[2].name, model: NAMED[2].model, color: expect.any(String) });
    expect(Object.values(seats).some((s) => "role" in s)).toBe(false);
  });

  it("shows the names on the live table, agents panel and chat, not Seat N", async () => {
    fakeNeon({ [ID]: { ...running(full.length), models: NAMED } });
    const { events } = await neonSource().loadGame(ID);
    const html = renderToStaticMarkup(
      createElement(Game, {
        events,
        labels: [],
        idx: events.length - 1,
        mode: "villager",
        selected: null,
        onSelect: () => {},
        onSeqChange: () => {},
      }),
    );
    for (const name of NAMES) expect(html).toContain(name);
    expect(html).not.toMatch(/Seat \d/);
  });

  it("names the seats of a forfeited visitor game too", async () => {
    const rows = full.filter((e) => e.visibility === "public").slice(0, 10);
    fakeNeon({ [ID]: { status: "failed", models: NAMED, rows } });
    const { events } = await neonSource().loadGame(ID);
    expect(Object.values(events[0].meta.seats!).map((s) => s.name)).toEqual(NAMES);
  });

  it("lists a named row by its models", async () => {
    fakeNeon({ [ID]: { ...running(), models: NAMED } });
    const games = await neonSource().listGames();
    expect(games[0].models).toEqual([...new Set(MODELS)].sort());
  });

  it("renders nothing private, even in narrator mode", async () => {
    fakeNeon({ [ID]: running(60) });
    const { events } = await neonSource().loadGame(ID);
    const html = renderToStaticMarkup(
      createElement(Game, {
        events,
        labels: [],
        idx: events.length - 1,
        mode: "narrator",
        selected: null,
        onSelect: () => {},
        onSeqChange: () => {},
      }),
    );
    for (const s of secrets) expect(html).not.toContain(s);
  });

  it("polls public events after a seq", async () => {
    fakeNeon({ [ID]: running(full.length) });
    const more = await neonSource().pollEvents(ID, 40);
    expect(more.length).toBeGreaterThan(0);
    expect(more.every((e) => e.seq > 40 && e.visibility === "public" && e.private_reasoning === null)).toBe(true);
    expect(more.map((e) => e.seq)).toEqual(full.filter((e) => e.seq > 40 && e.visibility === "public").map((e) => e.seq));
  });

  it("gives a forfeited visitor game (public rows only) a seat list too", async () => {
    const rows = full.filter((e) => e.visibility === "public").slice(0, 10).map((e) => ({ ...e, private_reasoning: null, suspicion: null }));
    fakeNeon({ [ID]: { status: "failed", models: MODELS, rows } });
    const game = await neonSource().loadGame(ID);
    expect(game.partial).toBe(true);
    expect(game.live).toBeFalsy();
    expect(game.events[0].type).toBe("game_start");
    expect(game.events.slice(1)).toEqual(rows);
  });
});

describe("withFallback", () => {
  const fallback: GameSource = {
    listGames: async () => [{ id: "static-1" }],
    loadGame: async () => ({ events: full, errors: [] }),
    pollEvents: async () => [],
    loadLabels: async () => [{ detail: "from static" } as never],
    audioIndexUrl: async (id) => `https://static.test/${id}.audio/index.json`,
  };

  it("takes narration from the static files", async () => {
    expect(await withFallback(neonSource(), fallback).audioIndexUrl!(ID)).toBe(`https://static.test/${ID}.audio/index.json`);
  });

  it("uses the static source when Neon fails", async () => {
    vi.stubGlobal("fetch", async () => new Response("", { status: 503 }));
    const src = withFallback(neonSource(), fallback);
    expect(await src.listGames()).toEqual([{ id: "static-1" }]);
    expect((await src.loadGame(ID)).events).toEqual(full);
    expect(await src.loadLabels!(ID)).toEqual([{ detail: "from static" }]);
  });

  it("keeps Neon when it works", async () => {
    fakeNeon({ [ID]: running() });
    const src = withFallback(neonSource(), fallback);
    expect((await src.loadGame(ID)).live).toBe(true);
    expect(await src.loadLabels!(ID)).toEqual([]);
  });
});
