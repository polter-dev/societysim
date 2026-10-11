import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ModelAvatar, monogram } from "./ModelAvatar";

const html = (props: Parameters<typeof ModelAvatar>[0]) => renderToStaticMarkup(createElement(ModelAvatar, props));

describe("ModelAvatar", () => {
  it("renders a lazy, sized, decorative portrait", () => {
    const out = html({ model: "gemma4-26b", label: "Gemma 4 26B · Google", size: 40 });
    expect(out).toContain("avatars/gemma4-26b.webp");
    expect(out).toContain('alt=""');
    expect(out).toContain('loading="lazy"');
    expect(out).toContain('width="40"');
  });
  it("maps the AMD alias and the human seat to their portraits", () => {
    expect(html({ model: "amd-strix-gpt-oss-20b" })).toContain("avatars/amd-gpt-oss-20b.webp");
    expect(html({ model: "human" })).toContain("avatars/human.webp");
  });
  it("marks eliminated players", () => {
    expect(html({ model: "gpt-5-mini", alive: false })).toContain('data-alive="false"');
  });
  it("falls back to the model's initial, never 'S'", () => {
    expect(html({ model: "mystery-model" })).toContain(">M<");
    expect(monogram("human")).toBe("You");
  });
});
