// .ts, not .tsx: vite.config.ts only collects src/**/*.test.ts.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { parseDeceptionMatrix } from "../data/deception";
import { fixture } from "../components/DeceptionMatrix/fixture";
import { headlines, ResearchView, type Summary } from "./Research";

const summary: Summary = {
  games: 12,
  unfinished_games: 1,
  cost_usd: 1.234,
  wolf_win_share: 0.25,
  village_win_share: null,
  top_openskill: { model: "llama4-maverick", ordinal: 2.9 },
  top_wolf_win: { model: "gemini-flash", win_rate: 0.5, seats: 4 },
};

describe("Research", () => {
  it("formats headlines and tolerates nulls", () => {
    const h = Object.fromEntries(headlines(summary));
    expect(h["Games"]).toBe("12 (1 unfinished)");
    expect(h["Wolf wins"]).toBe("25%");
    expect(h["Village wins"]).toBe("n/a");
    expect(h["Top OpenSkill"]).toBe("Llama 4 Maverick (2.90)");
    expect(h["Best wolf"]).toBe("Gemini Flash (50%)");
    expect(h["Cost"]).toBe("$1.23");
  });

  it("renders headlines with the matrix", () => {
    const matrix = parseDeceptionMatrix(structuredClone(fixture));
    const html = renderToStaticMarkup(createElement(ResearchView, { summary, matrix }));
    expect(html).toContain("Top OpenSkill");
    expect(html).toContain("deception-matrix");
    expect(html).toContain("95% CI");
  });

  it("shows n/a for null, missing or non-finite numbers", () => {
    const h = Object.fromEntries(
      headlines({ ...summary, cost_usd: null, games: undefined, top_openskill: { model: "m", ordinal: null } } as unknown as Summary),
    );
    expect(h["Cost"]).toBe("n/a");
    expect(h["Games"]).toBe("n/a (1 unfinished)");
    expect(h["Top OpenSkill"]).toContain("(n/a)");
    expect(Object.values(h).join(" ")).not.toMatch(/NaN|undefined|null/);
  });

  it("keeps the headlines when the matrix is absent", () => {
    const html = renderToStaticMarkup(createElement(ResearchView, { summary, matrix: null }));
    expect(html).toContain("Top OpenSkill");
    expect(html).toContain("Matrix unavailable");
    expect(html).not.toContain('role="alert"');
  });

  it("keeps the headlines and reports a malformed matrix below them", () => {
    const html = renderToStaticMarkup(createElement(ResearchView, { summary, matrix: null, matrixError: "bad shape" }));
    expect(html.indexOf("Top OpenSkill")).toBeLessThan(html.indexOf("bad shape"));
    expect(html).toContain('role="alert"');
  });
});
