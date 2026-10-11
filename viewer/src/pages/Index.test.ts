// .ts, not .tsx: vite.config.ts only collects src/**/*.test.ts.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { summarize } from "../data/staticSource";
import { GameList } from "./Index";

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
  it("links each game into the replay and shows models and days", () => {
    const html = render(false);
    expect(html).toContain('href="?game=g1"');
    expect(html).toContain("m-a, m-b");
    expect(html).toContain("<td>3</td>");
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

  it("says so when the index is empty", () => {
    expect(renderToStaticMarkup(createElement(GameList, { games: [], href: () => "" }))).toContain("No games");
  });
});
