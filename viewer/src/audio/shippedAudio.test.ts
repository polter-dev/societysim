import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { NARRATED_GAMES, parseIndex, siblingAudioIndexUrl } from "./narration";

// public/<game_id>.audio/ is copied to the site root at build time, beside <game_id>.jsonl,
// so the deploy serves narration for games whose published dataset has no audio (#512).
const publicDir = join(__dirname, "../../public");
const dirs = readdirSync(publicDir).filter((d) => d.endsWith(".audio"));

describe("shipped narration", () => {
  it("includes the demo game", () => {
    expect(dirs).toContain("mixed_v2_10p-g0043.audio");
  });

  it.each(dirs)("%s is narrator-only with every clip present", (dir) => {
    const gameId = dir.replace(/\.audio$/, "");
    const index = parseIndex(JSON.parse(readFileSync(join(publicDir, dir, "index.json"), "utf-8")));
    expect(index?.gameId).toBe(gameId);
    expect(index?.clips.length).toBeGreaterThan(0);
    // speech, wolf_chat and highlight clips are paid and can carry hidden information
    for (const c of index!.clips) {
      expect(c.category).toBe("narrator");
      expect(existsSync(join(publicDir, dir, c.file))).toBe(true);
      expect(statSync(join(publicDir, dir, c.file)).size).toBeGreaterThan(1000);
    }
    // the viewer finds it from the log URL
    expect(siblingAudioIndexUrl(`https://x.test/${gameId}.jsonl`)).toBe(`https://x.test/${dir}/index.json`);
  });

  it("NARRATED_GAMES lists exactly the shipped audio dirs", () => {
    expect([...NARRATED_GAMES].sort()).toEqual(dirs.map((d) => d.replace(/\.audio$/, "")).sort());
  });

  it("default site: no URL for a game without narration, so nothing is fetched", () => {
    expect(siblingAudioIndexUrl("https://x.test/mixed_v2_10p-g0000.jsonl", true)).toBeNull();
    expect(siblingAudioIndexUrl("https://x.test/mixed_v2_10p-g0043.jsonl", true)).toBe(
      "https://x.test/mixed_v2_10p-g0043.audio/index.json",
    );
  });

  it("custom sources (?base, ?index, ?log) keep the URL for any game", () => {
    expect(siblingAudioIndexUrl("https://x.test/local_batch-g0000.jsonl")).toBe(
      "https://x.test/local_batch-g0000.audio/index.json",
    );
    expect(siblingAudioIndexUrl("https://x.test/notes.txt")).toBeNull();
  });
});
