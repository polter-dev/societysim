import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it, vi } from "vitest";
import type { Event } from "../types/events";
import { bankIndex, clipFile, loadBankFiles, NARRATOR_VOICES, narratorClips, narratorLine } from "./bank";
import fixture from "./g0043.narrator.json";

const root = join(__dirname, "../../..");
const bankDir = join(__dirname, "../../public/narration-bank");
const bankJson = JSON.parse(readFileSync(join(bankDir, "index.json"), "utf-8")) as { clips: { file: string; text: string }[] };
const bankFiles = new Set(bankJson.clips.map((c) => c.file));
const events = fixture.events as unknown as Event[];

const ev = (over: Partial<Event>): Event =>
  ({ schema_version: 3, game_id: "g", seq: 1, t: 0, day: 1, phase: "day", type: "phase_start", visibility: "public",
    actor: null, target: null, public_text: null, private_reasoning: null, suspicion: null, meta: {}, ...over }) as Event;

describe("phrase bank narrator", () => {
  it("builds g0043's 23 narrator clips with the Python file names (analysis/narrate.py)", async () => {
    const clips = await narratorClips(events);
    expect(fixture.clips).toHaveLength(23);
    expect(clips.map((c) => ({ seq: c.seq, voice: c.voice, text: c.text, file: c.file }))).toEqual(
      fixture.clips.map(({ seq, voice, text, file }) => ({ seq, voice, text, file })),
    );
  });

  it("uses the narrator voice ids from configs/voices.yaml", () => {
    const yaml = readFileSync(join(root, "configs/voices.yaml"), "utf-8");
    expect(yaml).toMatch(new RegExp(`^\\s+day: ${NARRATOR_VOICES.narrator}\\b`, "m"));
    expect(yaml).toMatch(new RegExp(`^\\s+night: ${NARRATOR_VOICES.narrator_night}\\b`, "m"));
  });

  it("names files sha1(text + voice_id).mp3, matching every bank entry", async () => {
    for (const c of bankJson.clips as { file: string; text: string; voice_id: string }[]) {
      expect(await clipFile(c.text, c.voice_id)).toBe(c.file);
    }
  });

  it("ships the bank: every indexed clip is in public/narration-bank", () => {
    const mp3s = new Set(readdirSync(bankDir).filter((f) => f.endsWith(".mp3")));
    expect(mp3s).toEqual(bankFiles);
  });

  it("g0043 from the bank: every narrator line is in it", async () => {
    expect((await bankIndex(events, bankFiles)).clips).toHaveLength(23);
  });

  it("says nothing private: no line for non-public events, wolf chat or speech", () => {
    const names = { p1: "Ben" };
    expect(narratorLine(ev({ type: "death_reveal", visibility: "wolves", target: "p1" }), names)).toBeNull();
    expect(narratorLine(ev({ type: "night_kill", visibility: "wolves", target: "p1" }), names)).toBeNull();
    expect(narratorLine(ev({ type: "wolf_chat", visibility: "wolves", public_text: "kill Ben" }), names)).toBeNull();
    expect(narratorLine(ev({ type: "statement", actor: "p1", public_text: "I am the Seer" }), names)).toBeNull();
    expect(narratorLine(ev({ type: "doctor_save", visibility: "doctor", target: "p1" }), names)).toBeNull();
    for (const e of events.filter((e) => e.visibility !== "public")) expect(narratorLine(e, {})).toBeNull();
  });

  it("says a death role only when the reveal event carries it", () => {
    const names = { p1: "Ben" };
    expect(narratorLine(ev({ type: "death_reveal", phase: "night", target: "p1" }), names)).toEqual([
      "narrator_night",
      "The wolves struck. Ben was found dead.",
    ]);
    expect(narratorLine(ev({ type: "death_reveal", phase: "night", target: "p1", meta: { role: "seer" } }), names)).toEqual([
      "narrator_night",
      "The wolves struck. Ben, the Seer, was found dead.",
    ]);
    expect(narratorLine(ev({ type: "elimination", target: "p1" }), names)?.[1]).toBe("The village voted out Ben.");
    expect(narratorLine(ev({ type: "elimination", target: "p1", meta: { role: "wolf" } }), names)?.[1]).toBe(
      "The village voted out Ben, a wolf.",
    );
  });

  it("skips a line whose name the bank lacks, keeps the rest", async () => {
    const game = [
      ev({ seq: 0, type: "game_start", visibility: "narrator", phase: "setup", day: 0, meta: { seats: { p1: { name: "Zed", model: "x", color: "#000", role: "villager" } } } }),
      ev({ seq: 1, phase: "night" }),
      ev({ seq: 2, type: "death_reveal", phase: "night", target: "p1" }),
    ];
    const clips = (await bankIndex(game, bankFiles)).clips;
    expect(clips.map((c) => c.text)).toEqual(["Night 1. The village sleeps."]);
  });

  it("loads the bank index once; a failed fetch is retried", async () => {
    const bad = vi.fn(async () => new Response("", { status: 404 }));
    expect((await loadBankFiles(bad as typeof fetch, "x")).size).toBe(0);
    const good = vi.fn(async () => new Response(JSON.stringify({ clips: [{ file: "a.mp3" }] })));
    expect(await loadBankFiles(good as typeof fetch, "x")).toEqual(new Set(["a.mp3"]));
    expect(await loadBankFiles(good as typeof fetch, "x")).toEqual(new Set(["a.mp3"]));
    expect(good).toHaveBeenCalledTimes(1);
  });
});
