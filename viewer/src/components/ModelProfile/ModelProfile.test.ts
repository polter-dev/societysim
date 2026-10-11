// .ts, not .tsx: vite.config.ts only collects src/**/*.test.ts.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { profileFromMetrics } from "../../data/models";
import { metrics } from "./fixture";
import { ModelProfile } from "./ModelProfile";

const render = (model: string, minN?: number) =>
  renderToStaticMarkup(createElement(ModelProfile, { profile: profileFromMetrics(metrics, model)!, minN }));

describe("ModelProfile", () => {
  it("shows the headline numbers, voice and a placeholder avatar", () => {
    const html = render("gemini-flash");
    expect(html).toContain("<h2>gemini-flash</h2>");
    expect(html).toContain("100% (21% to 100%)");
    expect(html).toContain("<dt>Games</dt><dd>1 (1 seat)</dd>");
    const two = { ...profileFromMetrics(metrics, "gemini-flash")!, games: 1, seats: 2 };
    expect(renderToStaticMarkup(createElement(ModelProfile, { profile: two }))).toContain("1 (2 seats)");
    expect(html).toContain("<dt>Lie rate (self_tag)</dt><dd>50%</dd>");
    expect(html).toContain("<dt>Lie rate (jev)</dt><dd>n/a</dd>");
    expect(html).toContain("<dd>1.6 s</dd>");
    expect(html).toContain('data-voice-id="cgSgspJ2msm6clMCkdW9"');
    expect(html).not.toContain("(default voice)");
    expect(html).toContain("avatars/gemini-flash.webp");
  });

  it("lists who it fools in order, marking thin rows", () => {
    const html = render("gemini-flash", 5);
    expect(html).toContain("Who gemini-flash fools (self_tag)");
    expect(html.indexOf("deepseek-v3.1")).toBeLessThan(html.indexOf("gpt-oss-120b"));
    expect(html).toContain("83% (34% to 100%)");
    const rows = html.match(/<tr[^>]*><th scope="row">[^<]*/g) ?? [];
    expect(rows.filter((r) => r.includes("data-below-threshold"))).toEqual([
      '<tr data-below-threshold="true"><th scope="row">gpt-oss-120b',
    ]);
    expect(html).toContain("Who fools gemini-flash (self_tag): none.");
    expect(html).toContain("Who gemini-flash fools (jev): none.");
  });

  it("shows n/a for missing numbers and flags a default voice", () => {
    const html = render("llama-4-maverick");
    expect(html).toContain("<dt>Wolf win rate</dt><dd>n/a</dd>");
    expect(html).toContain("<dt>OpenSkill ordinal</dt><dd>n/a</dd>");
    expect(html).toContain("<dt>Median latency</dt><dd>n/a</dd>");
    expect(html).toContain("(default voice)");
    expect(html).toContain("Who fools llama-4-maverick (self_tag)");
  });
});
