import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { GlobalBoard } from "../../data/globalBoard";
import { GlobalLeaderboard } from "./GlobalLeaderboard";

const board: GlobalBoard = {
  detectors: [
    { model: "a", village_games: 7, suspicion_gap: 0.45, votes: 8, wolf_vote_rate: 0.5, ranked: true },
    { model: "few", village_games: 2, suspicion_gap: 0.9, votes: 3, wolf_vote_rate: 1, ranked: false },
  ],
  liars: [
    {
      model: "y",
      wolf_games: 6,
      suspicion_received: 0.3,
      survival_rate: 0.833,
      votes_received_per_game: 1,
      self_tag_lie_voters: 0,
      self_tag_lie_success: null,
      jev_lie_voters: 4,
      jev_lie_success: 0.75,
      ranked: true,
    },
  ],
  failed: [],
};

const render = (props: Partial<React.ComponentProps<typeof GlobalLeaderboard>> = {}) =>
  renderToStaticMarkup(React.createElement(GlobalLeaderboard, { board, ...props }));

describe("GlobalLeaderboard", () => {
  it("shows the Detectors tab by default, with unranked rows marked", () => {
    const html = render();
    expect(html).toContain('aria-selected="true">Detectors');
    expect(html).toContain("<td>0.45</td><td>50% (n=8)</td>");
    expect(html).toMatch(/<tr data-ranked="false"><th scope="row">.*few/);
    expect(html).not.toContain("Lie success");
  });

  it("shows the Liars tab", () => {
    const html = render({ initialTab: "liars" });
    expect(html).toContain('aria-selected="true">Liars');
    expect(html).toContain("<td>30%</td><td>83%</td><td>1.00</td><td>n/a (n=0)</td><td>75% (n=4)</td>");
  });

  it("loading, empty and error states", () => {
    expect(render({ board: null })).toContain("Loading the leaderboard");
    expect(render({ board: { detectors: [], liars: [], failed: [] } })).toContain("No finished games yet.");
    const failed = render({ error: "HTTP 503" });
    expect(failed).toContain('role="alert"');
    expect(failed).toContain("<td>0.45</td>"); // the last board stays up
  });

  it("a failed tab shows a plain error and the other tab still renders", () => {
    const liarsDown = { ...board, liars: [], failed: ["liars" as const] };
    const d = render({ board: liarsDown });
    expect(d).toContain("<td>0.45</td>");
    expect(d).not.toContain('role="alert"');
    const l = render({ board: liarsDown, initialTab: "liars" });
    expect(l).toContain("load the liars right now");
    expect(l).not.toContain("503");
    const detectorsDown = { ...board, detectors: [], failed: ["detectors" as const] };
    expect(render({ board: detectorsDown })).toContain("load the detectors right now");
    expect(render({ board: detectorsDown, initialTab: "liars" })).toContain("83%");
  });
});
