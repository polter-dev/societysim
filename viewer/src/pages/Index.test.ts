// .ts, not .tsx: vite.config.ts only collects src/**/*.test.ts.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { summarize } from "../data/staticSource";
import { GameList, gamesCount, filterGames } from "./Index";

const games = [
  summarize({
    game_id: "g1",
    file: "g1.jsonl",
    seed: 1,
    days: 3,
    winner: "wolves",
    seats: { p0: { name: "Ava", model: "m-a", role: "seer" }, p1: { name: "Ben", model: "m-b", role: "wolf" } },
  }),
];
const render = (showWinners: boolean) =>
  renderToStaticMarkup(createElement(GameList, { games, href: (id: string) => `?game=${id}`, showWinners }));

describe("GameList", () => {
  it('filters by game id, readable model name and live status without searching spoilers', () => {
    const rows = [{id:'village-1',models:['llama4-maverick'],live:true,winner:'wolves'}, {id:'batch-2',models:['gemini-flash']}];
    expect(filterGames(rows, 'Llama 4', 'all')).toEqual([rows[0]]);
    expect(filterGames(rows, ' batch ', 'recorded')).toEqual([rows[1]]);
    expect(filterGames(rows, '', 'live')).toEqual([rows[0]]);
    expect(filterGames(rows, 'wolves', 'all')).toEqual([]);
  });
  it("links each game into the replay and shows models and days", () => {
    const html = render(false);
    expect(html).toContain('href="?game=g1"');
    expect(html).toMatch(/<li title="m-a">.*m-a<\/li><li title="m-b">.*m-b<\/li>/);
    expect(html).toContain("<td>3</td>");
  });

  it("shows a dash, not n/a, when days are unknown", () => {
    const html = renderToStaticMarkup(createElement(GameList, { games: [{ id: "g8", live: true }], href: () => "" }));
    expect(html).toContain("<td>—</td>");
  });

  it("shows display names with the raw ids on hover", () => {
    const html = renderToStaticMarkup(
      createElement(GameList, { games: [{ id: "g7", models: ["llama4-maverick", "gemini-flash"] }], href: () => "" }),
    );
    expect(html).toMatch(/<li title="llama4-maverick">.*Llama 4 Maverick<\/li>/);
    expect(html).toMatch(/<li title="gemini-flash">.*Gemini Flash<\/li>/);
  });

  it("hides the winner until asked and never shows roles", () => {
    const hidden = render(false);
    expect(hidden).not.toContain("wolves");
    expect(hidden).toContain("hidden");
    expect(render(true)).toContain("wolves");
    for (const html of [hidden, render(true)]) expect(html).not.toMatch(/seer|Ava|Ben/);
  });

  it("says n/a, not none, when the source gives no winner or days", () => {
    const html = renderToStaticMarkup(
      createElement(GameList, { games: [{ id: "g9", models: ["m"] }], href: () => "", showWinners: true }),
    );
    expect(html).not.toContain("none");
    expect(html).not.toContain("?");
    expect(html).toContain("n/a");
    const draw = renderToStaticMarkup(
      createElement(GameList, { games: [{ id: "g9", winner: null }], href: () => "", showWinners: true }),
    );
    expect(draw).toContain("none");
  });

  it("tags the winner pill so live, hidden and decided games look different", () => {
    expect(render(false)).toContain('data-winner="hidden"');
    expect(render(true)).toContain('data-winner="wolves"');
    const live = renderToStaticMarkup(
      createElement(GameList, { games: [{ id: "g2", live: true, winner: "village" }], href: () => "", showWinners: true }),
    );
    expect(live).toContain('data-winner="live"');
    expect(live).not.toContain("village");
  });

  it("counts games and live games for the heading", () => {
    expect(gamesCount([])).toBe("0 games");
    expect(gamesCount([{ id: "a" }])).toBe("1 game");
    expect(gamesCount([{ id: "a", live: true }, { id: "b" }])).toBe("2 games, 1 live");
  });

  it("says so when the index is empty", () => {
    expect(renderToStaticMarkup(createElement(GameList, { games: [], href: () => "" }))).toContain("No games");
  });
});
