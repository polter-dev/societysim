import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { loadGlobalBoard, parseDetector, parseLiar, sortDetectors, sortLiars, type DetectorRow, type LiarRow } from "./globalBoard";
import { resetNeonAuth } from "./neonAuth";

const AUTH = "https://auth.test/neonauth";
const API = "https://api.test/rest/v1";

// Rows as PostgREST sends them: bigint as numbers, numeric as strings, nulls for no data.
const DETECTORS = [
  { model: "few", village_games: 2, suspicion_gap: "0.900", votes: 3, wolf_vote_rate: "1.000", ranked: false },
  { model: "b", village_games: 9, suspicion_gap: "0.200", votes: 12, wolf_vote_rate: "0.250", ranked: true },
  { model: "a", village_games: 7, suspicion_gap: "0.450", votes: 8, wolf_vote_rate: "0.500", ranked: true },
  { model: "none", village_games: 6, suspicion_gap: null, votes: 0, wolf_vote_rate: null, ranked: true },
];
const LIARS = [
  { model: "x", wolf_games: 5, suspicion_received: "0.600", survival_rate: "0.400", votes_received_per_game: "2.200", self_tag_lie_voters: 10, self_tag_lie_success: "0.500", jev_lie_voters: 0, jev_lie_success: null, ranked: true },
  { model: "y", wolf_games: 6, suspicion_received: "0.300", survival_rate: "0.833", votes_received_per_game: "1.000", self_tag_lie_voters: 0, self_tag_lie_success: null, jev_lie_voters: 4, jev_lie_success: "0.750", ranked: true },
  { model: "z", wolf_games: 1, suspicion_received: "0.050", survival_rate: "1.000", votes_received_per_game: "0.000", self_tag_lie_voters: 0, self_tag_lie_success: null, jev_lie_voters: 0, jev_lie_success: null, ranked: false },
];

describe("parse", () => {
  it("reads numeric strings as numbers and keeps nulls", () => {
    expect(parseDetector(DETECTORS[0])).toEqual<DetectorRow>({
      model: "few",
      village_games: 2,
      suspicion_gap: 0.9,
      votes: 3,
      wolf_vote_rate: 1,
      ranked: false,
    });
    const y = parseLiar(LIARS[1]);
    expect(y).toMatchObject<Partial<LiarRow>>({ suspicion_received: 0.3, self_tag_lie_success: null, jev_lie_success: 0.75, jev_lie_voters: 4 });
  });
});

describe("sort", () => {
  it("detectors: ranked first, largest gap first, no data last", () => {
    expect(sortDetectors(DETECTORS.map(parseDetector)).map((r) => r.model)).toEqual(["a", "b", "none", "few"]);
  });

  it("liars: ranked first, least suspicion first; a 1-game model can't top the board", () => {
    expect(sortLiars(LIARS.map(parseLiar)).map((r) => r.model)).toEqual(["y", "x", "z"]);
  });
});

describe("loadGlobalBoard", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_NEON_AUTH_URL", AUTH);
    vi.stubEnv("VITE_NEON_DATA_API_URL", API);
    resetNeonAuth();
  });
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
  });

  it("reads both views and sorts them", async () => {
    const asked: string[] = [];
    vi.stubGlobal("fetch", async (url: string) => {
      if (url === `${AUTH}/token/anonymous`)
        return new Response(JSON.stringify({ token: "jwt", expires_at: new Date(Date.now() + 3_600_000).toISOString() }));
      asked.push(url);
      return new Response(JSON.stringify(url.includes("global_detectors") ? DETECTORS : LIARS));
    });
    const board = await loadGlobalBoard();
    expect(asked.sort()).toEqual([`${API}/global_detectors?select=*`, `${API}/global_liars?select=*`]);
    expect(board.detectors.map((r) => r.model)).toEqual(["a", "b", "none", "few"]);
    expect(board.liars[0]).toMatchObject({ model: "y", survival_rate: 0.833 });
  });

  it.each([
    ["global_liars", "liars"],
    ["global_detectors", "detectors"],
  ] as const)("a failing %s view fails only its own tab", async (broken, tab) => {
    vi.stubGlobal("fetch", async (url: string) => {
      if (url === `${AUTH}/token/anonymous`)
        return new Response(JSON.stringify({ token: "jwt", expires_at: new Date(Date.now() + 3_600_000).toISOString() }));
      if (url.includes(broken)) return new Response("no", { status: 503 });
      return new Response(JSON.stringify(url.includes("global_detectors") ? DETECTORS : LIARS));
    });
    const board = await loadGlobalBoard();
    expect(board.failed).toEqual([tab]);
    expect(board[tab]).toEqual([]);
    expect(board[tab === "liars" ? "detectors" : "liars"].length).toBeGreaterThan(0);
  });
});
