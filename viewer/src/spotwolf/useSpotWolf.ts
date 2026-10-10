import { useCallback, useEffect, useState } from "react";
import type { Event } from "../types/events";
import {
  WOLF_COUNT,
  buildResults,
  isLocked,
  selectableSeats,
  lockIfRevealed,
  toggleMark,
  type GuessState,
  type Results,
} from "./guesses";
import { loadGuesses, saveGuesses } from "./storage";

/** What the component renders. Before the reveal it holds public information only: no roles, no results. */
export interface SpotWolfView {
  seats: { id: string; name: string; marked: boolean; alive: boolean }[];
  maxMarks: number;
  locked: boolean;
  /** Null until the game_end event has been reached. */
  results: Results | null;
}

/** Pure: the view for one beat. Remount the hook with key={gameId} to switch games. */
export function spotWolfView(events: Event[], idx: number, state: GuessState): SpotWolfView {
  return {
    seats: selectableSeats(events, idx, state.marks).map((s) => ({ ...s, marked: state.marks.includes(s.id) })),
    maxMarks: WOLF_COUNT,
    locked: isLocked(state, events, idx),
    results: buildResults(events, idx, state),
  };
}

export function useSpotWolf(events: Event[], idx: number) {
  const gameId = events[0]?.game_id ?? "";
  const [state, setState] = useState(() => loadGuesses(gameId));

  useEffect(() => setState((s) => lockIfRevealed(s, events, idx)), [events, idx]);
  useEffect(() => saveGuesses(state), [state]);

  const toggle = useCallback((seat: string) => setState((s) => toggleMark(s, events, idx, seat)), [events, idx]);
  return { view: spotWolfView(events, idx, state), toggle };
}
