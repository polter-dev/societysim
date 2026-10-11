import { useCallback, useEffect, useReducer } from "react";
import type { Event } from "../types/events";
import { atLatest, currentSeq, initStepper, newCount, parseStep, stepperReducer, withStep } from "./stepper";
import type { StepperState } from "./stepper";

const KEYS: Record<string, "next" | "prev" | "first" | "last"> = {
  ArrowRight: "next",
  ArrowLeft: "prev",
  Home: "first",
  End: "last",
};

/**
 * Stepper state for a (possibly growing) event log.
 * `events` may grow between renders when `live`; new events are appended and followed while at the tip.
 */
export function useStepper(events: Event[], live = false) {
  const [state, dispatch] = useReducer(
    stepperReducer,
    undefined,
    (): StepperState => initStepper(events, { live, seq: parseStep(window.location.search) }),
  );

  // live append: feed only events we haven't seen
  useEffect(() => {
    if (events.length > state.events.length) dispatch({ type: "append", events: events.slice(state.events.length) });
  }, [events, state.events.length]);
  useEffect(() => {
    if (!live) dispatch({ type: "finish" });
  }, [live]);

  // URL <- state (replace, not push). Omit the param at the live tip so the link keeps following.
  const seq = currentSeq(state);
  const follow = state.live && atLatest(state);
  useEffect(() => {
    const search = withStep(window.location.search, follow ? null : seq);
    if (search !== window.location.search) {
      window.history.replaceState(null, "", window.location.pathname + search + window.location.hash);
    }
  }, [seq, follow]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const type = KEYS[e.key];
      // defaultPrevented: a focused control (e.g. a ThemedSelect's Home/End) already used the key.
      if (!type || e.defaultPrevented || e.altKey || e.ctrlKey || e.metaKey) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
      e.preventDefault();
      dispatch({ type });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const goLive = useCallback(() => dispatch({ type: "last" }), []);
  return { state, dispatch, goLive, atLatest: atLatest(state), newCount: newCount(state) };
}
