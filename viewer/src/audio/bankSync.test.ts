import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const src = join(__dirname, "../../../narration/bank");
const shipped = join(__dirname, "../../public/narration-bank");

describe("shipped narration bank", () => {
  it("matches narration/bank: same files and same index entries", () => {
    expect(readdirSync(shipped).sort()).toEqual(readdirSync(src).sort());
    const idx = (d: string) => JSON.parse(readFileSync(join(d, "index.json"), "utf-8"));
    expect(idx(shipped)).toEqual(idx(src));
  });
});
