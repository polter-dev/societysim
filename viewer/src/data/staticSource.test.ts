import { afterEach, describe, expect, it, vi } from "vitest";
import fixture from "../../../fixtures/sample_game.jsonl?raw";
import labelsText from "../../../fixtures/sample_game.labels.jsonl?raw";
import { indexSource, type IndexEntry } from "./staticSource";

const entry: IndexEntry = {
  game_id: "fixture-0001",
  file: "sample_game.jsonl",
  seed: 42,
  days: 3,
  winner: "wolves",
  seats: {
    p0: { name: "Ava", model: "qwen3-235b", role: "seer" },
    p1: { name: "Ben", model: "llama-4-maverick", role: "villager" },
    p2: { name: "Cleo", model: "qwen3-235b", role: "wolf" },
  },
};

/** Serve these bodies by absolute URL; anything else is a 404. Returns the URLs asked for. */
function serve(files: Record<string, string>): string[] {
  const asked: string[] = [];
  vi.stubGlobal("fetch", async (url: string) => {
    asked.push(url);
    const body = files[url];
    return body === undefined ? new Response("", { status: 404 }) : new Response(body);
  });
  return asked;
}

afterEach(() => vi.unstubAllGlobals());

const base = "https://host.test/space/?page=x";
const site = "https://host.test/space/data/";

describe("indexSource", () => {
  it("lists games without roles and loads logs and labels relative to the index", async () => {
    const asked = serve({
      [`${site}report/index.json`]: JSON.stringify([entry]),
      [`${site}sample_game.jsonl`]: fixture,
      [`${site}sample_game.labels.jsonl`]: labelsText,
    });
    const src = indexSource("data/report/index.json", base);

    const games = await src.listGames();
    expect(games).toEqual([
      { id: "fixture-0001", days: 3, winner: "wolves", models: ["llama-4-maverick", "qwen3-235b"] },
    ]);
    expect(JSON.stringify(games)).not.toMatch(/seer|villager|"wolf"|Ava/);

    const log = await src.loadGame("fixture-0001");
    expect(log.errors).toEqual([]);
    expect(log.events).toHaveLength(118);
    expect((await src.loadLabels!("fixture-0001")).length).toBeGreaterThan(0);
    expect(asked.filter((u) => u.endsWith("index.json"))).toHaveLength(1);
  });

  it("surfaces a missing index and unknown games as errors", async () => {
    serve({});
    await expect(indexSource("data/report/index.json", base).listGames()).rejects.toThrow(/HTTP 404/);
    serve({ [`${site}report/index.json`]: "[]" });
    await expect(indexSource("data/report/index.json", base).loadGame("nope")).rejects.toThrow(/unknown game/);
  });
});
