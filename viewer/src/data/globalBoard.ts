// Global leaderboard (#344): the Neon views global_detectors and global_liars
// (docs/DATA_CONTRACT.md "Global leaderboard views"), parsed, sorted and polled.
import { useEffect, useState } from "react";
import { neonFetch } from "./neonAuth";

export interface DetectorRow {
  model: string;
  village_games: number;
  suspicion_gap: number | null;
  votes: number;
  wolf_vote_rate: number | null;
  ranked: boolean;
}

export interface LiarRow {
  model: string;
  wolf_games: number;
  suspicion_received: number | null;
  survival_rate: number | null;
  votes_received_per_game: number | null;
  self_tag_lie_voters: number;
  self_tag_lie_success: number | null;
  jev_lie_voters: number;
  jev_lie_success: number | null;
  ranked: boolean;
}

export interface GlobalBoard {
  detectors: DetectorRow[];
  liars: LiarRow[];
  /** Tabs whose view failed to load; their rows are empty. */
  failed: BoardTab[];
}

export type BoardTab = "detectors" | "liars";
export const BOARD_TABS: readonly { id: BoardTab; label: string }[] = [
  { id: "detectors", label: "Detectors" },
  { id: "liars", label: "Liars" },
];

/** How often the board is re-read while the tab is visible. */
export const POLL_MS = 45_000;

type Raw = Record<string, unknown>;
// numeric columns may arrive as strings ("0.571").
const num = (v: unknown): number | null => (v === null || v === undefined || v === "" ? null : Number(v));
const int = (v: unknown): number => Number(v ?? 0);

export const parseDetector = (r: Raw): DetectorRow => ({
  model: String(r.model),
  village_games: int(r.village_games),
  suspicion_gap: num(r.suspicion_gap),
  votes: int(r.votes),
  wolf_vote_rate: num(r.wolf_vote_rate),
  ranked: r.ranked === true,
});

export const parseLiar = (r: Raw): LiarRow => ({
  model: String(r.model),
  wolf_games: int(r.wolf_games),
  suspicion_received: num(r.suspicion_received),
  survival_rate: num(r.survival_rate),
  votes_received_per_game: num(r.votes_received_per_game),
  self_tag_lie_voters: int(r.self_tag_lie_voters),
  self_tag_lie_success: num(r.self_tag_lie_success),
  jev_lie_voters: int(r.jev_lie_voters),
  jev_lie_success: num(r.jev_lie_success),
  ranked: r.ranked === true,
});

/** Ranked rows first; then by `key` (best first, nulls last); then by model name. */
function rank<T extends { model: string; ranked: boolean }>(rows: T[], key: (r: T) => number | null, higherIsBetter: boolean): T[] {
  return [...rows].sort((a, b) => {
    if (a.ranked !== b.ranked) return a.ranked ? -1 : 1;
    const x = key(a);
    const y = key(b);
    if (x !== y) {
      if (x === null) return 1;
      if (y === null) return -1;
      return higherIsBetter ? y - x : x - y;
    }
    return a.model.localeCompare(b.model);
  });
}

/** Best detector: the largest suspicion gap (wolves rated higher than everyone else). */
export const sortDetectors = (rows: DetectorRow[]) => rank(rows, (r) => r.suspicion_gap, true);
/** Best liar: the least suspicion received as a wolf. */
export const sortLiars = (rows: LiarRow[]) => rank(rows, (r) => r.suspicion_received, false);

async function get(path: string): Promise<Raw[]> {
  const res = await neonFetch(`/${path}`);
  if (!res.ok) throw new Error(`Neon ${path.split("?")[0]}: HTTP ${res.status}`);
  return (await res.json()) as Raw[];
}

export async function loadGlobalBoard(): Promise<GlobalBoard> {
  // Each view loads on its own: one failing must not blank the other tab.
  const [d, l] = await Promise.allSettled([get("global_detectors?select=*"), get("global_liars?select=*")]);
  if (d.status === "rejected" && l.status === "rejected") throw d.reason;
  return {
    detectors: d.status === "fulfilled" ? sortDetectors(d.value.map(parseDetector)) : [],
    liars: l.status === "fulfilled" ? sortLiars(l.value.map(parseLiar)) : [],
    failed: [d.status === "rejected" && "detectors", l.status === "rejected" && "liars"].filter((x): x is BoardTab => !!x),
  };
}

// --- display: the columns each tab shows, so the component only renders --------------------

export interface Column<T> {
  label: string;
  title: string;
  value: (r: T) => string;
}

const pct = (x: number | null) => (x === null ? "n/a" : `${Math.round(x * 100)}%`);
const dec = (x: number | null) => (x === null ? "n/a" : x.toFixed(2));
const withN = (x: number | null, n: number) => `${pct(x)} (n=${n})`;

export const DETECTOR_COLUMNS: Column<DetectorRow>[] = [
  { label: "Model", title: "", value: (r) => r.model },
  { label: "Games", title: "Seat-games on the village side", value: (r) => String(r.village_games) },
  {
    label: "Suspicion gap",
    title: "Mean suspicion given to wolves minus mean given to everyone else (1 perfect, 0 guessing)",
    value: (r) => dec(r.suspicion_gap),
  },
  { label: "Wolf votes", title: "Share of its day votes that hit a wolf", value: (r) => withN(r.wolf_vote_rate, r.votes) },
];

const JEV_COLUMN = "Lie success (Jev)";

export const LIAR_COLUMNS: Column<LiarRow>[] = [
  { label: "Model", title: "", value: (r) => r.model },
  { label: "Games", title: "Seat-games as a wolf", value: (r) => String(r.wolf_games) },
  { label: "Suspicion", title: "Mean suspicion it got from non-wolves as a wolf (lower is better)", value: (r) => pct(r.suspicion_received) },
  { label: "Survived", title: "Share of its wolf games it was never eliminated or killed", value: (r) => pct(r.survival_rate) },
  { label: "Votes/game", title: "Day votes against it per wolf game", value: (r) => dec(r.votes_received_per_game) },
  {
    label: "Lie success (self-tag)",
    title: "Share of later same-day votes that went the way its self-tagged lies pointed",
    value: (r) => withN(r.self_tag_lie_success, r.self_tag_lie_voters),
  },
  {
    label: JEV_COLUMN,
    title: "The same, for lies Jev labelled",
    value: (r) => withN(r.jev_lie_success, r.jev_lie_voters),
  },
];

/** The liars columns; the Jev one only when some row has a Jev lie (a self-tag-only batch has none). */
export function liarColumns(rows: LiarRow[]): Column<LiarRow>[] {
  const hasJev = rows.some((r) => r.jev_lie_voters > 0 || r.jev_lie_success !== null);
  return hasJev ? LIAR_COLUMNS : LIAR_COLUMNS.filter((c) => c.label !== JEV_COLUMN);
}

// --- polling ---------------------------------------------------------------------------------

export interface GlobalBoardState {
  board: GlobalBoard | null;
  /** The last poll's error; the previous board stays shown. */
  error: string | null;
}

/** A tab that failed this refresh keeps its previous rows; it only shows the error with none. */
function keepPrevious(prev: GlobalBoard | null, next: GlobalBoard): GlobalBoard {
  if (!prev) return next;
  const kept = next.failed.filter((t) => !prev.failed.includes(t));
  return {
    detectors: kept.includes("detectors") ? prev.detectors : next.detectors,
    liars: kept.includes("liars") ? prev.liars : next.liars,
    failed: next.failed.filter((t) => !kept.includes(t)),
  };
}

/**
 * Loads the board now and every `intervalMs` while the page is visible. A hidden tab stops
 * polling; it reloads at once when the tab is shown again.
 */
export function useGlobalBoard(load: () => Promise<GlobalBoard> = loadGlobalBoard, intervalMs = POLL_MS): GlobalBoardState {
  const [state, setState] = useState<GlobalBoardState>({ board: null, error: null });

  useEffect(() => {
    let stopped = false;
    let busy = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      timer = undefined;
      if (stopped || busy || document.hidden) return; // a hidden tab resumes on visibilitychange
      busy = true;
      try {
        const board = await load();
        if (!stopped) setState((s) => ({ board: keepPrevious(s.board, board), error: null }));
      } catch (e) {
        if (!stopped) setState((s) => ({ ...s, error: e instanceof Error ? e.message : String(e) }));
      }
      busy = false;
      if (!stopped && !document.hidden) timer = setTimeout(tick, intervalMs);
    };
    const onVisibility = () => {
      if (!document.hidden && timer === undefined) void tick();
    };
    document.addEventListener("visibilitychange", onVisibility);
    void tick();
    return () => {
      stopped = true;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [load, intervalMs]);

  return state;
}
