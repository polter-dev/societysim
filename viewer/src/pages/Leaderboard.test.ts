// .ts, not .tsx: vite.config.ts only collects src/**/*.test.ts.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import LeaderboardPage, { type Board, type BoardRow, GlobalSection, LeaderboardTable } from "./Leaderboard";

const row = (over: Partial<BoardRow>): BoardRow => ({
  model: "m",
  games: 4,
  win_rate: null,
  win_ci: null,
  wolf_win_rate: null,
  village_win_rate: null,
  openskill: null,
  detection_auc: null,
  detection_brier: null,
  invalid_output_rate: null,
  cost_usd_per_game: null,
  lie_rate: { self_tag: null, jev: null },
  ...over,
});
const rating = (ordinal: number) => ({ mu: 25, sigma: 8, ordinal });
const board: Board = {
  schema: 1,
  games: 2,
  label_sources: ["self_tag", "jev"],
  wolf_side_advantage: { overall: rating(-20) },
  models: [
    row({ model: "alpha", win_rate: 0.75, win_ci: [0.3, 0.95], wolf_win_rate: 1, openskill: rating(2.91) }),
    row({ model: "beta" }),
  ],
};

describe("LeaderboardTable", () => {
  it("renders win rate with its Wilson CI and OpenSkill overall and by side", () => {
    const html = renderToStaticMarkup(
      createElement(LeaderboardTable, {
        board,
        bySide: { wolf: { alpha: rating(1.5) }, village: { beta: rating(-3) } },
      }),
    );
    expect(html).toContain("75% (30% to 95%)");
    expect(html).toContain("<td>2.91</td>");
    expect(html).toContain("<td>1.50</td>");
    expect(html).toContain("<td>-3.00</td>");
    expect(html.indexOf("alpha")).toBeLessThan(html.indexOf("beta"));
    expect(html).toContain("Wolf-side advantage: mu 25.00");
  });

  it("shows n/a for missing values instead of zero", () => {
    const html = renderToStaticMarkup(createElement(LeaderboardTable, { board: { ...board, models: [row({})] } }));
    expect(html).not.toMatch(/>0%|>0\.00/);
    expect(html.match(/n\/a/g)!.length).toBe(8);
  });
});

describe("LeaderboardTable profile links", () => {
  it("links each model name to its profile page when given profileHref", () => {
    const html = renderToStaticMarkup(
      createElement(LeaderboardTable, { board, profileHref: (m: string) => `?page=model&model=${m}` }),
    );
    expect(html).toContain('<a href="?page=model&amp;model=alpha">alpha</a></th>');
    expect(renderToStaticMarkup(createElement(LeaderboardTable, { board }))).not.toContain("<a ");
  });
});

describe("Leaderboard page global board", () => {
  it("mounts the global board, even before the model table loads", () => {
    const html = renderToStaticMarkup(
      createElement(LeaderboardPage, { leaderboardUrl: "x.json", metricsUrl: "y.json", globalBoard: true }),
    );
    expect(html).toContain("Loading leaderboard...");
    expect(html).toContain("Global rankings");
    expect(html).toContain('class="global-leaderboard"');
    expect(html).toContain("Loading the leaderboard");
  });

  it("without Neon vars it explains and renders no board", () => {
    const html = renderToStaticMarkup(createElement(GlobalSection, { enabled: false }));
    expect(html).toContain("need live data");
    expect(html).not.toContain("global-leaderboard");
  });
});

describe("LeaderboardTable ranking (#395)", () => {
  it("sorts rows by the shown metric and uses display names", () => {
    const b: Board = {
      ...board,
      models: [
        row({ model: "gemma4-26b", openskill: rating(1) }),
        row({ model: "llama4-maverick", openskill: rating(5) }),
        row({ model: "gpt-5-mini" }),
      ],
    };
    const html = renderToStaticMarkup(createElement(LeaderboardTable, { board: b }));
    expect(html.indexOf("Llama 4 Maverick")).toBeLessThan(html.indexOf("Gemma 4 26B"));
    expect(html.indexOf("Gemma 4 26B")).toBeLessThan(html.indexOf("GPT-5 mini"));
  });

  it("ranks by win rate when no model has OpenSkill, and scrolls inside its own box", () => {
    const b: Board = { ...board, models: [row({ model: "alpha", win_rate: 0.2 }), row({ model: "zeta", win_rate: 0.9 })] };
    const html = renderToStaticMarkup(createElement(LeaderboardTable, { board: b }));
    expect(html.indexOf("zeta")).toBeLessThan(html.indexOf("alpha"));
    expect(html).toContain("overflow-x:auto");
  });

  it("shows the short model name with the full id on hover", () => {
    const b = { ...board, models: [row({ model: "llama4-maverick", win_rate: 0.5 })] };
    const html = renderToStaticMarkup(createElement(LeaderboardTable, { board: b }));
    expect(html).toContain('title="llama4-maverick"');
    expect(html).toContain("Llama 4 Maverick");
  });
});
