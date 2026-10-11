import { useEffect, useRef, type Dispatch, type SetStateAction } from "react";
import type { GameSource, LoadedGame } from "../data/source";

/** Fast ticks to wait after the end for the full log to land (a visitor game is backfilled after game_end). */
export const FINISH_TRIES = 5;
/** Then this many slower ticks (one per SLOW_MS, about 2 minutes) before giving up on the backfill. */
export const SLOW_TRIES = 24;
export const SLOW_MS = 5000;

export interface LiveState {
  log: LoadedGame;
  /** Ticks spent waiting for the full log since the game ended. */
  endTries: number;
}

/**
 * One poll of a live game. New public events are appended. Once the game has ended (a game_end
 * arrived, or the status left `running`), the full log is loaded and replaces the public one.
 * While the full log is missing the log is `finishing`. A game whose full log never shows up
 * (a forfeited visitor game) stops being live after FINISH_TRIES + SLOW_TRIES ticks and keeps its
 * public events.
 */
export async function liveTick(source: GameSource, id: string, s: LiveState): Promise<LiveState> {
  const last = s.log.events.at(-1)?.seq ?? -1;
  const more = (await source.pollEvents(id, last)).filter((e) => e.seq > last);
  let log = more.length ? { ...s.log, events: [...s.log.events, ...more] } : s.log;
  // The games row can arrive before game_start publishes its public names.
  // Refresh only that incomplete roster; keep the accumulated events and replay position.
  const start = log.events.find((e) => e.type === "game_start");
  if (log.partial && start && Object.values(start.meta.seats ?? {}).some((p) => /^Seat \d+$/.test(p.name))) {
    const snapshot = await source.loadGame(id).catch(() => null);
    const roster = snapshot?.events.find((e) => e.type === "game_start");
    if (roster?.visibility === "public") {
      const seats = { ...start.meta.seats };
      let changed = false;
      for (const [seat, player] of Object.entries(roster.meta.seats ?? {})) {
        if (seats[seat] && /^Seat \d+$/.test(seats[seat].name) && !/^Seat \d+$/.test(player.name)) {
          seats[seat] = { ...seats[seat], name: player.name };
          changed = true;
        }
      }
      if (changed) log = { ...log, events: log.events.map((e) => e === start ? { ...e, meta: { ...e.meta, seats } } : e) };
    }
  }
  let ended = s.endTries > 0 || more.some((e) => e.type === "game_end");
  if (!ended && !more.length && source.gameStatus) ended = (await source.gameStatus(id)) !== "running";
  if (!ended) return { log, endTries: 0 };
  const full = await source.loadGame(id);
  if (!full.live && !full.partial) return { log: { ...full, watchedLive: true, finishing: false }, endTries: 0 };
  const endTries = s.endTries + 1;
  if (endTries >= FINISH_TRIES + SLOW_TRIES) return { log: { ...log, live: false, finishing: false }, endTries };
  return { log: log.finishing ? log : { ...log, finishing: true }, endTries };
}

/**
 * Polls `source` every `intervalMs` while `log.live` (only a source's loadGame sets it), updating
 * the log through `setLog`. A failed poll is retried on the next tick.
 */
export function useLiveGame(
  source: GameSource | undefined,
  log: LoadedGame | null,
  setLog: Dispatch<SetStateAction<LoadedGame | null>>,
  intervalMs = 2000,
) {
  const logRef = useRef(log);
  logRef.current = log;
  const gameId = log?.live ? (log.events[0]?.game_id ?? null) : null;
  const live = !!(source && gameId);

  useEffect(() => {
    if (!live || !source || !gameId || !logRef.current) return;
    // The effect owns the log from here until the game is no longer live.
    let s: LiveState = { log: logRef.current, endTries: 0 };
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      try {
        const next = await liveTick(source, gameId, s);
        if (stopped) return;
        if (next.log !== s.log) setLog(next.log);
        s = next;
        if (!next.log.live) return;
      } catch {
        // network or auth hiccup: try again next tick
      }
      if (!stopped) timer = setTimeout(tick, s.endTries >= FINISH_TRIES ? Math.max(intervalMs, SLOW_MS) : intervalMs);
    };
    timer = setTimeout(tick, intervalMs);
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [live, source, gameId, setLog, intervalMs]);
}
