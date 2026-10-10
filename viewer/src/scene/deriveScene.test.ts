import { describe, expect, it } from "vitest";
import fixture from "../../../fixtures/sample_game.jsonl?raw";
import { parseLog } from "../data/parse";
import type { Event } from "../types/events";
import { deriveScene, type Mode } from "./deriveScene";

const events = parseLog(fixture).events;
const at = (seq: number, mode: Mode) => deriveScene(events, seq, mode); // fixture seq == index
const roles = (seq: number, mode: Mode) =>
  Object.fromEntries(at(seq, mode).players.map((p) => [p.id, p.role]));
const dead = (seq: number) =>
  at(seq, "narrator")
    .players.filter((p) => !p.alive)
    .map((p) => p.id);

const ALL_ROLES = {
  p0: "seer",
  p1: "villager",
  p2: "wolf",
  p3: "doctor",
  p4: "villager",
  p5: "wolf",
  p6: "villager",
};
const HIDDEN = { p0: null, p1: null, p2: null, p3: null, p4: null, p5: null, p6: null };

describe("deriveScene on the fixture", () => {
  it("seats come from game_start; narrator sees every role, villager none", () => {
    const s = at(0, "narrator");
    expect(s.players.map((p) => `${p.id}:${p.name}`)).toEqual([
      "p0:Ava",
      "p1:Ben",
      "p2:Cleo",
      "p3:Dev",
      "p4:Elif",
      "p5:Finn",
      "p6:Gia",
    ]);
    expect(roles(0, "narrator")).toEqual(ALL_ROLES);
    expect(roles(0, "villager")).toEqual(HIDDEN);
    expect(at(0, "villager").current).toBeNull(); // game_start is narrator-only
  });

  it("wolf chat is in the narrator log only", () => {
    const n = at(9, "narrator");
    const v = at(9, "villager");
    expect(n.current?.type).toBe("wolf_proposal");
    expect(n.log.filter((e) => e.type === "wolf_chat")).toHaveLength(4);
    expect(v.current).toBeNull();
    expect(v.log.map((e) => e.type)).toEqual(["phase_start"]);
    expect([v.day, v.phase]).toEqual([1, "night"]);
  });

  it("seer and doctor actions are narrator-only", () => {
    expect(at(10, "narrator").current?.type).toBe("seer_check");
    expect(at(12, "narrator").current?.type).toBe("doctor_save");
    expect(at(10, "villager").current).toBeNull();
    expect(at(12, "villager").current).toBeNull();
  });

  it("a saved night kill kills nobody", () => {
    expect(dead(13)).toEqual([]);
    expect(at(13, "villager").current?.public_text).toBe("Nobody died last night.");
  });

  it("tracks the speaker and strips private fields in villager mode", () => {
    const n = at(17, "narrator");
    const v = at(17, "villager");
    expect([n.speaker, v.speaker]).toEqual(["p5", "p5"]);
    expect(n.current?.private_reasoning).toBeTruthy();
    expect(n.current?.suspicion?.length).toBeGreaterThan(0);
    expect(v.current?.private_reasoning).toBeNull();
    expect(v.current?.suspicion).toBeNull();
    expect(at(14, "villager").speaker).toBeNull(); // Day 1 phase_start
  });

  it("collects the day's accusations", () => {
    expect(at(20, "villager").accusations).toEqual([{ actor: "p5", target: "p0", seq: 18 }]);
    expect(at(42, "narrator").accusations).toHaveLength(9);
  });

  it("invalid_output is narrator-only and does not change the speaker", () => {
    expect(at(25, "narrator").current?.type).toBe("invalid_output");
    expect(at(25, "villager").current).toBeNull();
    expect(at(25, "narrator").speaker).toBe("p2");
  });

  it("counts votes live, with a fallback abstain", () => {
    const s = at(50, "villager");
    expect(s.phase).toBe("vote");
    expect(s.speaker).toBeNull();
    expect(s.votes.map((v) => [v.actor, v.target, v.fallback])).toEqual([
      ["p0", "p2", false],
      ["p1", "p2", false],
      ["p2", "p0", false],
      ["p3", null, true],
    ]);
    expect(s.tally).toEqual({ p2: 2, p0: 1 });
  });

  it("a tied vote eliminates nobody and keeps the tally", () => {
    expect(at(54, "villager").tally).toEqual({ p2: 3, p0: 3 });
    expect(dead(54)).toEqual([]);
  });

  it("a new day clears accusations, votes and tally", () => {
    const s = at(55, "villager");
    expect([s.accusations, s.votes, s.tally]).toEqual([[], [], {}]);
  });

  it("a night death reveals only the dead player's role in villager mode", () => {
    expect(dead(67)).toEqual(["p0"]);
    expect(roles(67, "villager")).toEqual({ ...HIDDEN, p0: "seer" });
    expect(at(67, "villager").players[0]).toMatchObject({ alive: false, revealed: true });
  });

  it("an elimination kills and reveals, with the logged tally", () => {
    const s = at(104, "villager");
    expect(dead(104)).toEqual(["p0", "p6"]);
    expect(roles(104, "villager")).toEqual({ ...HIDDEN, p0: "seer", p6: "villager" });
    expect(s.tally).toEqual({ p6: 4, p5: 2 });
  });

  it("the last night's death is recorded before game_end", () => {
    expect(dead(116)).toEqual(["p0", "p3", "p6"]);
    expect(roles(116, "villager")).toEqual({ ...HIDDEN, p0: "seer", p3: "doctor", p6: "villager" });
    expect(at(116, "villager").winner).toBeNull();
  });

  it("game_end reveals every role in both modes", () => {
    for (const mode of ["narrator", "villager"] as const) {
      expect(at(117, mode).winner).toBe("wolves");
      expect(roles(117, mode)).toEqual(ALL_ROLES);
    }
  });

  it("clamps idx and is pure", () => {
    const before = JSON.stringify(events);
    expect(deriveScene(events, 999, "villager").seq).toBe(117);
    expect(deriveScene(events, -5, "narrator").seq).toBe(0);
    expect(JSON.stringify(events)).toBe(before);
  });
});

// Logs from `uv run societysim play --config configs/selfplay_smoke.yaml` (scripted, no keys) land in
// runs/ (gitignored). When present locally, sweep them too; CI only has the fixture.
const local = import.meta.glob<string>("../../../runs/**/*.jsonl", {
  query: "?raw",
  import: "default",
  eager: true,
});
const logs: [string, Event[]][] = [
  ["fixture", events],
  ...Object.entries(local).map(([f, t]): [string, Event[]] => [f, parseLog(t).events]),
];

describe.each(logs)("villager-mode invariants: %s", (_name, evs) => {
  it("never shows a living player's role, hidden events or private fields before game_end", () => {
    for (let i = 0; i < evs.length; i++) {
      const s = deriveScene(evs, i, "villager");
      const over = s.winner !== null;
      for (const p of s.players) if (p.alive && !over) expect(p.role).toBeNull();
      for (const e of s.log) {
        expect(e.visibility).toBe("public");
        expect(e.private_reasoning).toBeNull();
        expect(e.suspicion).toBeNull();
      }
    }
  });

  it("narrator mode counts one death per death event", () => {
    const deaths = evs.filter(
      (e) => (e.type === "death_reveal" || e.type === "elimination") && e.target,
    ).length;
    const s = deriveScene(evs, evs.length - 1, "narrator");
    expect(s.players.filter((p) => !p.alive)).toHaveLength(deaths);
    expect(s.winner).not.toBeNull();
  });
});
