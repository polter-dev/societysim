// .ts, not .tsx: vite.config.ts only collects src/**/*.test.ts.
import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { SetupForm } from "../components/SetupForm/SetupForm";
import {
  type CatalogModel,
  type ModelsCatalog,
  type SetupState,
  defaultState,
  humanSeat,
  schemaErrors,
  setHumanSeat,
  setMode,
  toSetupJson,
  validate,
} from "./setup";

const schema = JSON.parse(readFileSync(new URL("../../../schema/game_setup.schema.json", import.meta.url), "utf8"));
const model = (name: string, available = true): CatalogModel => ({
  name,
  display_name: name,
  provider: "huggingface",
  sponsor: null,
  price_tier: "low",
  structured: "json_object",
  structured_ok: true,
  color: null,
  available,
  unavailable_reason: available ? null : `${name}: AMD_BASE_URL is not set`,
});
const catalog: ModelsCatalog = {
  human: "human",
  default_lineup: ["a", "b", "c"],
  models: [model("a"), model("b"), model("c"), model("down", false)],
};
const adv = (patch: Partial<SetupState> = {}): SetupState => ({ ...defaultState(catalog), mode: "advanced", ...patch });
const seatsOf = (s: SetupState, n = s.nPlayers) => s.seats.slice(0, n);

describe("defaults", () => {
  it("is Basic: 10 seats, 2 wolves, seer and doctor, no human", () => {
    const s = defaultState(catalog);
    expect(s).toMatchObject({ mode: "basic", nPlayers: 10, nWolves: 2, seer: true, doctor: true, basicHuman: false });
    expect(toSetupJson(s)).toEqual({ mode: "basic", seed: 0 });
    expect(validate(s, catalog)).toEqual([]);
  });

  it("Basic human toggle puts one human at p0 with 9 models", () => {
    const s = { ...defaultState(catalog), basicHuman: true };
    expect(toSetupJson(s)).toEqual({ mode: "basic", seed: 0, human_seat: "p0" });
    const a = setMode(s, "advanced");
    expect(seatsOf(a).filter((m) => m === "human")).toHaveLength(1);
    expect(humanSeat(a)).toBe("p0");
  });
});

describe("validate", () => {
  it("n_players must be 7..10", () => {
    expect(validate(adv({ nPlayers: 6 }), catalog)[0]).toContain("n_players");
    expect(validate(adv({ nPlayers: 11 }), catalog)[0]).toContain("n_players");
    expect(validate(adv({ nPlayers: 7, nWolves: 2 }), catalog)).toEqual([]);
  });

  it("wolves must be fewer than the villager side", () => {
    expect(validate(adv({ nPlayers: 8, nWolves: 4 }), catalog)).toEqual([
      "4 wolves need more than 4 villagers; use at most 3 wolves for 8 players",
    ]);
    expect(validate(adv({ nPlayers: 10, nWolves: 4 }), catalog)).toEqual([]);
    expect(validate(adv({ nWolves: 0 }), catalog)[0]).toContain("n_wolves");
  });

  it("allows at most one human", () => {
    const s = adv();
    const seats = s.seats.slice();
    seats[1] = "human";
    seats[4] = "human";
    expect(validate({ ...s, seats }, catalog)).toEqual(["at most one human seat, got p1, p4"]);
  });

  it("rejects unknown and unavailable models", () => {
    const s = adv();
    const seats = s.seats.slice();
    seats[2] = "nope";
    seats[3] = "down";
    const errs = validate({ ...s, seats }, catalog);
    expect(errs[0]).toContain('unknown model "nope" at p2');
    expect(errs[1]).toBe("p3: down is unavailable (down: AMD_BASE_URL is not set)");
  });

  it("ignores seats beyond n_players", () => {
    const s = adv({ nPlayers: 7 });
    const seats = s.seats.slice();
    seats[9] = "nope";
    expect(validate({ ...s, seats }, catalog)).toEqual([]);
  });
});

describe("toSetupJson", () => {
  it("round-trips Basic and Advanced against the schema", () => {
    const cases: SetupState[] = [
      defaultState(catalog),
      { ...defaultState(catalog), basicHuman: true, seed: 7 },
      adv({ nPlayers: 7, nWolves: 3, doctor: false, seed: 42 }),
      setHumanSeat(adv({ nPlayers: 8 }), 5, catalog),
    ];
    for (const s of cases) expect(schemaErrors(toSetupJson(s), schema)).toEqual([]);
  });

  it("Advanced lists each seat once and the human seat matches", () => {
    const j = toSetupJson(setHumanSeat(adv({ nPlayers: 8 }), 5, catalog));
    expect(j.seats?.map((x) => x.seat)).toEqual(["p0", "p1", "p2", "p3", "p4", "p5", "p6", "p7"]);
    expect(j.seats?.[5].model).toBe("human");
    expect(j.human_seat).toBe("p5");
  });

  it("moving the human frees the old seat", () => {
    const s = setHumanSeat(setHumanSeat(adv(), 2, catalog), 6, catalog);
    expect(seatsOf(s).filter((m) => m === "human")).toEqual(["human"]);
    expect(humanSeat(s)).toBe("p6");
    expect(humanSeat(setHumanSeat(s, null, catalog))).toBeNull();
  });

  it("the schema checker catches bad setups", () => {
    expect(schemaErrors({ mode: "basic", n_players: 11 }, schema)).not.toEqual([]);
    expect(schemaErrors({ mode: "basic", extra: 1 }, schema)).toEqual(["$.extra: unknown field"]);
    expect(schemaErrors({ seats: [{ seat: "x1", model: "a" }] }, schema)).not.toEqual([]);
  });
});

describe("SetupForm", () => {
  it("renders Basic with the download button and the play command", () => {
    const out = renderToStaticMarkup(createElement(SetupForm, { assets: { catalog, schema } }));
    expect(out).toContain("Download setup.json");
    expect(out).toContain("societysim play --setup setup.json");
    expect(out).not.toContain("<select");
  });
});
