import { describe, expect, it, vi } from "vitest";
import { retryLoad, type GameSource, type LoadedGame } from "./source";

const game: LoadedGame = { events: [], errors: [] };
const source = (loadGame: GameSource["loadGame"]): GameSource => ({ listGames: async () => [], loadGame, pollEvents: async () => [] });

describe("retryLoad", () => {
  it("keeps trying until a just-started game's row shows up", async () => {
    const load = vi.fn().mockRejectedValueOnce(new Error("unknown game visitor-1")).mockRejectedValueOnce(new Error("unknown game visitor-1")).mockResolvedValue(game);
    await expect(retryLoad(source(load), 5, 0).loadGame("visitor-1")).resolves.toBe(game);
    expect(load).toHaveBeenCalledTimes(3);
  });

  it("gives up with the last error after the allowed tries", async () => {
    const load = vi.fn().mockRejectedValue(new Error("unknown game visitor-2"));
    await expect(retryLoad(source(load), 3, 0).loadGame("visitor-2")).rejects.toThrow(/unknown game/);
    expect(load).toHaveBeenCalledTimes(3);
  });
});
