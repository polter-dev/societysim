// The Play panel and the live table are separate trees; the table's App owns the polled public
// events. App publishes them here so the your-turn panel can show the same discussion log.
import { useSyncExternalStore } from "react";
import type { Event } from "../types/events";

const EMPTY: Event[] = [];
let current: Event[] = EMPTY;
const listeners = new Set<() => void>();

export function publishLiveEvents(events: Event[]): void {
  if (events === current) return;
  current = events;
  for (const l of listeners) l();
}

/** The events App holds for `gameId`; empty when it holds another game or none yet. */
export function useLiveEvents(gameId: string): Event[] {
  const all = useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => {
        listeners.delete(cb);
      };
    },
    () => current,
  );
  return all[0]?.game_id === gameId ? all : EMPTY;
}
