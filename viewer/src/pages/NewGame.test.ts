// .ts, not .tsx: vite.config.ts only collects src/**/*.test.ts.
import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { SetupForm } from "../components/SetupForm/SetupForm";
import type { ModelsCatalog } from "../settings/setup";

const schema = JSON.parse(readFileSync(new URL("../../../schema/game_setup.schema.json", import.meta.url), "utf8"));
const model = (name: string, available: boolean) => ({
  name, display_name: name, provider: "p", sponsor: null, price_tier: "low", structured: "json",
  structured_ok: true, color: null, available, unavailable_reason: available ? null : "no key",
});
const catalog: ModelsCatalog = {
  human: "human",
  default_lineup: ["a", "b"],
  models: [model("a", true), model("b", false)],
};

describe("New game page form", () => {
  it("shows the setup JSON, the play command and a download button", () => {
    const html = renderToStaticMarkup(createElement(SetupForm, { assets: { catalog, schema } }));
    expect(html).toContain("Download setup.json");
    expect(html).toContain("societysim play --setup setup.json");
  });
});
