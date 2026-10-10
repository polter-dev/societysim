import type { Board } from "../pages/Leaderboard";
import type { Event, EventMeta } from "../types/events";
import { parseLabels, type Label } from "./labels";
import { neonFetch } from "./neonAuth";
import { parseLog, type ParsedLog } from "./parse";
import type { GameSource, GameSummary, LoadedGame } from "./source";

/**
 * GameSource over the Neon Data API (docs/DATA_CONTRACT.md "Neon Data API"), through neonFetch
 * (anonymous JWT). Hidden information: while a game runs, only its public events are requested,
 * and only their public fields (`private_reasoning` and `suspicion` never leave the database).
 * The full log is read once the game has ended. Events come from the `events_public` view: the
 * anonymous role can't read `events`, and the view enforces the same hiding in the database (#323).
 */
export interface NeonSource extends GameSource {
  loadBoard(): Promise<Board>;
}

// Every Event field except private_reasoning and suspicion, read out of the jsonb row.
const PUBLIC_FIELDS = ["schema_version", "game_id", "seq", "t", "day", "phase", "type", "visibility", "actor", "target", "public_text", "meta"];
const PUBLIC_SELECT = PUBLIC_FIELDS.map((f) => `${f}:event->${f}`).join(",");
const SEAT_COLORS = ["#4f8ef7", "#f59e0b", "#10b981", "#ef4444", "#8b5cf6", "#ec4899", "#14b8a6", "#f97316", "#84cc16", "#64748b"];

const gid = (id: string) => `game_id=eq.${encodeURIComponent(id)}`;

async function get<T>(path: string): Promise<T[]> {
  const res = await neonFetch(`/${path}`);
  if (!res.ok) throw new Error(`Neon ${path.split("?")[0]}: HTTP ${res.status}`);
  return (await res.json()) as T[];
}

const toLog = (rows: unknown[]): ParsedLog => parseLog(rows.map((r) => JSON.stringify(r)).join("\n"));

/** Public events after `afterSeq`, redacted on the server side by the select. */
async function publicEvents(id: string, afterSeq = -1): Promise<Event[]> {
  const rows = await get<Record<string, unknown>>(
    `events_public?select=${PUBLIC_SELECT}&${gid(id)}&event->>visibility=eq.public&seq=gt.${afterSeq}&order=seq`,
  );
  // Belt and braces: drop anything that isn't public and blank the private fields.
  const events = toLog(rows.map((r) => ({ ...r, private_reasoning: null, suspicion: null }))).events;
  return events.filter((e) => e.visibility === "public");
}

/**
 * A stand-in game_start for a public-only log, which has none (the real one is narrator-only and
 * holds every role). Seats come from games.models plus any seat the events mention; no roles.
 */
export function publicSeats(gameId: string, models: unknown, events: Event[]): Event {
  const list = Array.isArray(models) ? models : [];
  const ids = new Set(list.map((_, i) => `p${i}`));
  for (const e of events) [e.actor, e.target].forEach((s) => s && ids.add(s));
  const sorted = [...ids].sort((a, b) => Number(a.slice(1)) - Number(b.slice(1)));
  const seats = Object.fromEntries(
    sorted.map((id) => {
      const n = Number(id.slice(1));
      const model = typeof list[n] === "string" ? (list[n] as string) : "unknown";
      return [id, { name: `Seat ${n + 1}`, model, color: SEAT_COLORS[n % SEAT_COLORS.length] }];
    }),
  );
  return {
    schema_version: events[0]?.schema_version ?? 3,
    game_id: gameId,
    seq: Math.min(0, (events[0]?.seq ?? 1) - 1),
    t: 0,
    day: 0,
    phase: "setup",
    type: "game_start",
    visibility: "public",
    actor: null,
    target: null,
    public_text: null,
    private_reasoning: null,
    suspicion: null,
    meta: { seats } as unknown as EventMeta, // role left out on purpose
  };
}

/** games.models: a list of model names in seat order (older rows may map seats to names). */
function modelNames(m: unknown): string[] | undefined {
  if (typeof m !== "object" || m === null) return undefined;
  const names = Object.values(m)
    .map((v) => (typeof v === "object" && v !== null ? (v as { model?: unknown }).model : v))
    .filter((v): v is string => typeof v === "string");
  return [...new Set(names)].sort();
}

export function neonSource(): NeonSource {
  const gameRow = async (id: string) => {
    const [row] = await get<{ status: string | null; models: unknown }>(`games?select=status,models&${gid(id)}`);
    if (!row) throw new Error(`unknown game ${id}`);
    return row;
  };

  return {
    // Ids, models and status only: roles and winners stay in the database.
    listGames: async () =>
      (await get<{ game_id: string; models: unknown; status: string | null }>(
        "games?select=game_id,models,status&order=started_at.desc.nullslast",
      )).map((g): GameSummary => ({ id: g.game_id, models: modelNames(g.models), live: g.status === "running" })),

    loadGame: async (id): Promise<LoadedGame> => {
      const row = await gameRow(id);
      const live = row.status === "running";
      if (live) {
        const events = await publicEvents(id);
        return { events: [publicSeats(id, row.models, events), ...events], errors: [], live: true, partial: true };
      }
      const log = toLog((await get<{ event: unknown }>(`events_public?select=event&${gid(id)}&order=seq`)).map((r) => r.event));
      // A visitor game that never reached game_end keeps its public-only rows (no game_start).
      if (log.events.some((e) => e.type === "game_start")) return log;
      return { ...log, events: [publicSeats(id, row.models, log.events), ...log.events], partial: true };
    },

    pollEvents: (id, afterSeq) => publicEvents(id, afterSeq),

    gameStatus: async (id) => (await gameRow(id)).status,

    loadLabels: async (id): Promise<Label[]> => {
      try {
        // Labels name roles; labels_public has none for an unfinished game, but never ask while it runs.
        if ((await gameRow(id)).status === "running") return [];
        const rows = await get<{ label: unknown }>(`labels_public?select=label&${gid(id)}&order=idx`);
        return parseLabels(rows.map((r) => JSON.stringify(r.label)).join("\n"));
      } catch {
        return []; // same as a missing labels file
      }
    },

    loadBoard: async () => {
      const [rows, finished] = await Promise.all([
        get<{ model: string; seat_games: number | string; win_rate: number | string | null }>(
          "leaderboard?select=model,seat_games,win_rate&order=win_rate.desc.nullslast",
        ),
        // Same games the view counts: finished, not scripted.
        get<unknown>("games?select=game_id&status=eq.finished&or=(agents.is.null,agents.neq.scripted)"),
      ]);
      return {
        schema: 1,
        games: finished.length,
        label_sources: [],
        wolf_side_advantage: null,
        models: rows.map((r) => ({
          model: r.model,
          games: null,
          seats: Number(r.seat_games),
          win_rate: r.win_rate == null ? null : Number(r.win_rate), // numeric may arrive as a string
          win_ci: null,
          wolf_win_rate: null,
          village_win_rate: null,
          openskill: null,
          detection_auc: null,
          detection_brier: null,
          invalid_output_rate: null,
          cost_usd_per_game: null,
          lie_rate: {},
        })),
      };
    },
  };
}

/** Neon first; on any error, the static source. Labels come from whichever served the game. */
export function withFallback(primary: NeonSource, fallback: GameSource): NeonSource {
  const servedBy = new Map<string, Promise<GameSource>>();
  return {
    ...primary,
    listGames: () => primary.listGames().catch(() => fallback.listGames()),
    loadGame: (id) => {
      let src: GameSource = primary;
      const game = primary.loadGame(id).catch(() => {
        src = fallback;
        return fallback.loadGame(id);
      });
      servedBy.set(id, game.then(() => src, () => src));
      return game;
    },
    loadLabels: async (id) => (await (servedBy.get(id) ?? primary)).loadLabels?.(id) ?? [],
    // Narration mp3s are static files only (a running game has none).
    audioIndexUrl: fallback.audioIndexUrl,
  };
}
