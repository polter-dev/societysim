// @vitest-environment jsdom
// #700: Load batch keeps you on the page you were on.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { BatchSourceForm } from "./BatchSourceForm";

const fields = (page: string | null, model: string | null = null) => {
  const html = renderToStaticMarkup(createElement(BatchSourceForm, { base: "https://x.test/", page, model }));
  const form = new DOMParser().parseFromString(html, "text/html").querySelector("form")!;
  return [...form.querySelectorAll("input")].map((i) => [i.name, i.getAttribute("value") ?? ""]);
};

describe("BatchSourceForm", () => {
  it("keeps the page", () => {
    expect(fields("research")).toEqual([["page", "research"], ["base", "https://x.test/"]]);
  });
  it("keeps the model on a profile page", () => {
    expect(fields("model", "gemini-flash")).toEqual([["page", "model"], ["model", "gemini-flash"], ["base", "https://x.test/"]]);
  });
  it("sends the games list to Games, not home", () => {
    expect(fields(null)[0]).toEqual(["page", "games"]);
    expect(fields("games")[0]).toEqual(["page", "games"]);
  });
});
