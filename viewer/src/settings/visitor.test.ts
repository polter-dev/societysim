// Visitor play setup (#356): allowed models, Play/Watch drives the human seat, friendly names.
import { describe, expect, it } from "vitest";
import { allowedModels } from "../play/allowed";
import { friendlyMessage, safeDetail } from "../play/api";
import {
  type CatalogModel,
  type ModelsCatalog,
  defaultState,
  humanSeat,
  joinAs,
  labelFor,
  modelLabel,
  restrictCatalog,
  toSetupJson,
  validate,
} from "./setup";

const model = (name: string, display_name = name, provider = "Acme"): CatalogModel => ({
  name,
  display_name,
  provider,
  sponsor: null,
  price_tier: "low",
  structured: "json_object",
  structured_ok: true,
  color: null,
  available: true,
  unavailable_reason: null,
});
// gemini-flash is in the default lineup but the server does not allow it.
const catalog: ModelsCatalog = {
  human: "human",
  default_lineup: ["qwen", "gemini-flash", "nano"],
  models: [model("qwen"), model("gemini-flash", "Gemini 2.5 Flash", "Google"), model("nano")],
};
const allowed = ["qwen", "nano"];

describe("restrictCatalog", () => {
  it("keeps only allowed models and builds defaults from them", () => {
    const c = restrictCatalog(catalog, allowed);
    expect(c.models.map((m) => m.name)).toEqual(["qwen", "nano"]);
    expect(c.default_lineup).toEqual(["qwen", "nano"]);
    const s = { ...defaultState(c), mode: "advanced" as const };
    expect(new Set(s.seats)).toEqual(new Set(["qwen", "nano"]));
    expect(validate(s, c)).toEqual([]);
  });
  it("falls back to the allowed catalog models when no lineup entry is allowed", () => {
    expect(restrictCatalog(catalog, ["nano"]).default_lineup).toEqual(["nano"]);
    expect(restrictCatalog({ ...catalog, default_lineup: ["gemini-flash"] }, ["nano"]).default_lineup).toEqual(["nano"]);
  });
  it("leaves the catalog alone without a list", () => {
    expect(restrictCatalog(catalog, null)).toBe(catalog);
  });
  it("reads the list from a limits reply", () => {
    expect(allowedModels({ allowed_models: ["a"] })).toEqual(["a"]);
    expect(allowedModels({ play_enabled: true })).toBeNull();
    expect(allowedModels({ allowed_models: [1] })).toBeNull();
    expect(allowedModels(null)).toBeNull();
  });
});

describe("joinAs", () => {
  const c = restrictCatalog(catalog, allowed);
  it("Play in Advanced puts one human at p0 (no 422)", () => {
    const s = joinAs({ ...defaultState(c), mode: "advanced" }, true, c);
    expect(humanSeat(s)).toBe("p0");
    const j = toSetupJson(s);
    expect(j.human_seat).toBe("p0");
    expect(j.seats?.filter((x) => x.model === "human")).toHaveLength(1);
  });
  it("keeps a human the visitor already moved", () => {
    const s = joinAs({ ...defaultState(c), mode: "advanced" }, true, c);
    const moved = { ...s, seats: s.seats.map((m, i) => (i === 0 ? "qwen" : i === 4 ? "human" : m)) };
    expect(humanSeat(joinAs(moved, true, c))).toBe("p4");
  });
  it("Watch removes the human in both modes", () => {
    const withHuman = joinAs({ ...defaultState(c), mode: "advanced" }, true, c);
    const s = joinAs(withHuman, false, c);
    expect(humanSeat(s)).toBeNull();
    expect(toSetupJson(s).human_seat).toBeUndefined();
    expect(toSetupJson(s).seats?.some((x) => x.model === "human")).toBe(false);
    expect(toSetupJson(joinAs(defaultState(c), false, c)).human_seat).toBeUndefined();
    expect(toSetupJson(joinAs(defaultState(c), true, c)).human_seat).toBe("p0");
  });
});

describe("labels", () => {
  it("shows display name and provider, not the id", () => {
    expect(modelLabel(catalog.models[1])).toBe("Gemini 2.5 Flash · Google");
    expect(labelFor(catalog, "gemini-flash")).toBe("Gemini 2.5 Flash · Google");
    expect(labelFor(catalog, "unknown-id")).toBe("unknown-id");
  });
});

describe("invalid_request detail", () => {
  it("shows the server's plain detail", () => {
    const msg = friendlyMessage(400, "invalid_request", "not available in visitor games: x; allowed: a, b");
    expect(msg).toBe("That request wasn't valid: not available in visitor games: x; allowed: a, b");
  });
  it("drops anything that looks like a stack or provider text", () => {
    expect(safeDetail("Traceback (most recent call last)")).toBe("");
    expect(safeDetail("litellm.BadRequestError: boom")).toBe("");
    expect(safeDetail("see https://provider.example/key")).toBe("");
    expect(safeDetail("x".repeat(400))).toBe("");
    expect(friendlyMessage(400, "invalid_request", "Traceback")).toMatch(/^That request wasn't valid\. /);
  });
  it("leaves other codes on our wording", () => {
    expect(friendlyMessage(429, "rate_limited", "anything")).toMatch(/Slow down/);
  });
});
