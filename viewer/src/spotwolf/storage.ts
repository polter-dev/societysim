import { initGuesses, type GuessState } from "./guesses";

const key = (gameId: string) => `societysim.spotwolf.v1.${gameId}`;

type Store = Pick<Storage, "getItem" | "setItem">;

function defaultStore(): Store | null {
  try {
    return typeof localStorage === "undefined" ? null : localStorage;
  } catch {
    return null;
  }
}

function valid(v: unknown, gameId: string): v is GuessState {
  const s = v as GuessState | null;
  return (
    !!s &&
    s.gameId === gameId &&
    Array.isArray(s.marks) &&
    s.marks.every((m) => typeof m === "string") &&
    Array.isArray(s.history) &&
    (s.lockedSeq === null || typeof s.lockedSeq === "number")
  );
}

/** Saved guesses for this game, or a fresh state. Never throws (private mode, bad JSON, wrong shape). */
export function loadGuesses(gameId: string, store: Store | null = defaultStore()): GuessState {
  try {
    const raw = store?.getItem(key(gameId));
    if (raw) {
      const parsed: unknown = JSON.parse(raw);
      if (valid(parsed, gameId)) return parsed;
    }
  } catch {
    /* fall through */
  }
  return initGuesses(gameId);
}

export function saveGuesses(state: GuessState, store: Store | null = defaultStore()): void {
  try {
    store?.setItem(key(state.gameId), JSON.stringify(state));
  } catch {
    /* quota or blocked storage: guesses just won't persist */
  }
}
