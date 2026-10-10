import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import fixture from "../../../../fixtures/sample_game.jsonl?raw";
import { parseLog } from "../../data/parse";
import { deriveScene } from "../../scene/deriveScene";
import { seatPosition } from "../Table/Table";
import { Arrows } from "./Arrows";

const events = parseLog(fixture).events;
const render = (seq: number, mode: "narrator" | "villager" = "villager") =>
  renderToStaticMarkup(React.createElement(Arrows, { scene: deriveScene(events, seq, mode) }));

describe("Arrows", () => {
  it("uses the square table overlay and seatPosition endpoints", () => {
    const html = render(41);
    expect(html).toContain('viewBox="0 0 100 100"');
    expect(html).toContain('position:absolute');
    expect(html).toContain(`x1="${seatPosition(1, 7).x + 7 * (seatPosition(2, 7).x - seatPosition(1, 7).x) / Math.hypot(seatPosition(2, 7).x - seatPosition(1, 7).x, seatPosition(2, 7).y - seatPosition(1, 7).y)}"`);
    expect(html).toContain('aria-label="Ben accused Cleo"');
  });

  it("groups repeat accusations by actor and target and increases stroke width", () => {
    const scene = deriveScene(events, 41, "villager");
    scene.accusations.push({ actor: "p1", target: "p2", seq: 999 });
    const html = renderToStaticMarkup(React.createElement(Arrows, { scene }));
    expect(html.match(/aria-label="Ben accused Cleo 2 times"/g)).toHaveLength(1);
    expect(html).toMatch(/data-kind="accusation" data-count="2"[^>]*><line[^>]*stroke-width="1.05"/);
  });

  it("reveals every vote at the result event, including the forced abstain mark", () => {
    expect(render(53)).not.toContain('data-kind="vote"');
    expect(render(53)).not.toContain('data-kind="abstain"');
    const html = render(54);
    expect(html.match(/data-kind="vote"/g)).toHaveLength(6);
    expect(html).toContain('aria-label="Dev was forced to abstain"');
    expect(html).toContain('data-fallback="true"');
  });

  it("does not show previous-day arrows or night wolf targets in villager mode", () => {
    const html = render(57);
    expect(html).not.toContain("accused");
    expect(html).not.toContain('data-kind="vote"');
    expect(html).not.toContain('data-kind="abstain"');
    expect(html).not.toContain("p0");
  });
});
