import { useCallback, useEffect, useState } from "react";
import type { Event } from "../../types/events";
import { nearestSeq, readSeqParam, withSeqParam } from "./seq";

function currentSearch(): string {
  return typeof window === "undefined" ? "" : window.location.search;
}

/** Owns the selected event seq and keeps the shareable URL in sync. */
/** `sync` false (the stepper owns the position and the URL) leaves the address bar alone. */
export function useSeqParam(events: readonly Event[], sync = true): [number | null, (seq: number) => void] {
  const [requested, setRequested] = useState<number | null>(() => {
    const raw = new URLSearchParams(currentSearch()).get("seq");
    return raw !== null && /^-?\d+$/.test(raw) ? Number(raw) : null;
  });
  const seq = requested !== null && events.some((event) => event.seq === requested)
    ? requested
    : readSeqParam(events, currentSearch());

  useEffect(() => {
    if (!sync) return;
    const onPopState = () => setRequested(readSeqParam(events, currentSearch()));
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, [events, sync]);

  useEffect(() => {
    if (!sync || seq === null) return;
    const search = withSeqParam(window.location.search, seq);
    if (search !== window.location.search) {
      window.history.replaceState(window.history.state, "", `${window.location.pathname}${search}${window.location.hash}`);
    }
  }, [seq, sync]);

  const setSeq = useCallback((value: number) => {
    const next = nearestSeq(events, value);
    if (next !== null) setRequested(next);
  }, [events]);
  return [seq, setSeq];
}
