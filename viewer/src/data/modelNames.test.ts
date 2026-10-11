import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { modelName } from "./modelNames";

describe("modelName", () => {
  it("shortens known ids and passes unknown ones through", () => {
    expect(modelName("llama4-maverick")).toBe("Llama 4 Maverick");
    expect(modelName("some-new-model")).toBe("some-new-model");
  });

  it("has a name for every model in configs/models.yaml", () => {
    const yaml = readFileSync(new URL("../../../configs/models.yaml", import.meta.url), "utf8");
    const ids = [...yaml.matchAll(/^ {2}- name: (\S+)/gm)].map((m) => m[1]);
    expect(ids.length).toBeGreaterThan(5);
    for (const id of ids) expect(modelName(id), id).not.toBe(id);
  });
});
