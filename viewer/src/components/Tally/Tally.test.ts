import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import fixture from "../../../../fixtures/sample_game.jsonl?raw";
import { parseLog } from "../../data/parse";
import { deriveScene } from "../../scene/deriveScene";
import { Tally } from "./Tally";

const events = parseLog(fixture).events;
const render = (seq: number) =>
  renderToStaticMarkup(React.createElement(Tally, { scene: deriveScene(events, seq, "villager") }));

describe("Tally", () => {
  it("waits for the simultaneous vote reveal", () => {
    expect(render(53)).toContain("Votes pending reveal.");
    expect(render(53)).not.toContain("Cleo: 3");
  });

  it("shows logged tie counts and the forced abstain", () => {
    const html = render(54);
    expect(html).toContain("Ava: 3");
    expect(html).toContain("Cleo: 3");
    expect(html).toContain("Abstain: 1");
    expect(html).toContain("Forced abstain: Dev");
    expect(html).toContain("Tie. No elimination.");
  });

  it("shows all-abstain separately from a tie", () => {
    const scene = deriveScene(events, 54, "villager");
    scene.tally = {};
    scene.votes = scene.votes.map((vote) => ({ ...vote, target: null }));
    const html = renderToStaticMarkup(React.createElement(Tally, { scene }));
    expect(html).toContain("Abstain: 7");
    expect(html).toContain("All abstained. No elimination.");
    expect(html).not.toContain("Tie. No elimination.");
  });

  it("shows elimination and resets for the next day", () => {
    expect(render(104)).toContain("Gia eliminated.");
    expect(render(55)).toContain("Votes pending reveal.");
  });
});
