import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import fixture from "../../../fixtures/sample_game.jsonl?raw";
import { LiveBadge } from "../components/LiveBadge/LiveBadge";
import { parseLog } from "../data/parse";
import type { GameSource, LoadedGame } from "../data/source";
import type { EventMeta } from "../types/events";
import { FINISH_TRIES, SLOW_TRIES, liveTick } from "./useLiveGame";

const full = parseLog(fixture).events;
const ID = full[0].game_id;
const pub = full.filter((e) => e.visibility === "public");
const liveLog = (n: number): LoadedGame => ({ events: pub.slice(0, n), errors: [], live: true, partial: true });

/** A source whose game has `shown` public events so far; `status` and `fullLog` are what loadGame returns once it ends. */
function fake(opts: { shown: () => number; status?: () => string; fullLog?: () => LoadedGame }) {
  const calls = { poll: 0, status: 0, load: 0 };
  const source: GameSource = {
    listGames: async () => [],
    pollEvents: async (_id, after) => {
      calls.poll++;
      return pub.slice(0, opts.shown()).filter((e) => e.seq > after);
    },
    gameStatus: async () => {
      calls.status++;
      return opts.status?.() ?? "running";
    },
    loadGame: async () => {
      calls.load++;
      return opts.fullLog?.() ?? { events: full, errors: [] };
    },
  };
  return { source, calls };
}

describe("liveTick", () => {
  it("replaces startup seat placeholders with public names without losing events or exposing roles", async () => {
    const roster = { ...full[0], visibility: "public" as const, meta: { seats: Object.fromEntries(Object.entries(full[0].meta.seats!).map(([id, p]) => [id, { model: p.model, color: p.color, name: `Seat ${Number(id.slice(1)) + 1}` }])) } as unknown as EventMeta };
    const named = { ...roster, meta: full[0].meta };
    const { source } = fake({ shown: () => 8, fullLog: () => ({ events: [named, ...pub], errors: [], live: true, partial: true }) });
    const log = { ...liveLog(5), events: [roster, ...pub.slice(0, 5)] };
    const next = await liveTick(source, ID, { log, endTries: 0 });
    expect(next.log.events.slice(1)).toEqual(pub.slice(0, 8));
    for (const [id, p] of Object.entries(full[0].meta.seats!)) {
      expect(next.log.events[0].meta.seats![id].name).toBe(p.name);
      expect(next.log.events[0].meta.seats![id]).not.toHaveProperty("role");
    }
    const again = await liveTick(source, ID, next);
    expect(again.log).toBe(next.log);
  });

  it("appends new public events and keeps going", async () => {
    const { source, calls } = fake({ shown: () => 8 });
    const s = await liveTick(source, ID, { log: liveLog(5), endTries: 0 });
    expect(s.log.events).toEqual(pub.slice(0, 8));
    expect(s.log.live).toBe(true);
    expect(calls.load).toBe(0);
  });

  it("returns the same log when nothing changed and the game still runs", async () => {
    const { source, calls } = fake({ shown: () => 5 });
    const start = { log: liveLog(5), endTries: 0 };
    expect((await liveTick(source, ID, start)).log).toBe(start.log);
    expect(calls.status).toBe(1);
  });

  it("swaps in the full log once game_end arrives", async () => {
    const { source } = fake({ shown: () => pub.length });
    const s = await liveTick(source, ID, { log: liveLog(5), endTries: 0 });
    expect(s.log.events).toEqual(full);
    expect(s.log.live).toBeFalsy();
    expect(s.log.watchedLive).toBe(true);
  });

  it("swaps in the full log when the status leaves running", async () => {
    const { source } = fake({ shown: () => 5, status: () => "failed" });
    expect((await liveTick(source, ID, { log: liveLog(5), endTries: 0 })).log.events).toEqual(full);
  });

  it("waits for a backfill, then gives up and keeps the public events", async () => {
    const { source } = fake({ shown: () => pub.length, fullLog: () => ({ ...liveLog(3), live: false }) });
    let s = { log: liveLog(5), endTries: 0 };
    for (let i = 1; i < FINISH_TRIES + SLOW_TRIES; i++) {
      s = await liveTick(source, ID, s);
      expect(s.log.live).toBe(true);
      expect(s.log.finishing).toBe(true);
      expect(s.endTries).toBe(i);
    }
    s = await liveTick(source, ID, s);
    expect(s.log.live).toBe(false);
    expect(s.log.events).toEqual(pub);
    expect(s.log.finishing).toBe(false);
  });

  it("loads the full log when the backfill lands after the fast polls", async () => {
    let ready = false;
    const { source } = fake({
      shown: () => pub.length,
      fullLog: () => (ready ? { events: full, errors: [] } : { ...liveLog(3), live: false }),
    });
    let s = { log: liveLog(5), endTries: 0 };
    for (let i = 0; i < FINISH_TRIES + 3; i++) s = await liveTick(source, ID, s);
    expect(s.log.live).toBe(true);
    ready = true;
    s = await liveTick(source, ID, s);
    expect(s.log.events).toEqual(full);
    expect(s.log.live).toBeFalsy();
    expect(s.log.finishing).toBe(false);
    expect(s.log.watchedLive).toBe(true);
  });
});

describe("LiveBadge", () => {
  const html = (newCount: number) => renderToStaticMarkup(createElement(LiveBadge, { newCount }));
  it("shows Live at the tip and the count once you step back", () => {
    expect(html(0)).toBe('<span class="live-badge" role="status">● Live</span>');
    expect(html(3)).toContain("● Live, 3 new");
  });
});
