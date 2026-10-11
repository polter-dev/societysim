// .ts, not .tsx: vite.config.ts only collects src/**/*.test.ts.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import fixture from "../../../../fixtures/sample_game.jsonl?raw";
import labelsText from "../../../../fixtures/sample_game.labels.jsonl?raw";
import { fetchLabels, parseLabels, siblingLabelsUrl, type LabelSource } from "../../data/labels";
import { parseLog } from "../../data/parse";
import { deriveScene, type Mode } from "../../scene/deriveScene";
import { buildBeats } from "../../state/stepper";
import { Table } from "../Table/Table";
import { LabelPopups, visibleLabels } from "./LabelPopups";

const events = parseLog(fixture).events;
const labels = parseLabels(labelsText);
const idxOfSeq = (seq: number) => events.findIndex((e) => e.seq === seq);
const html = (idx: number, mode: Mode, sources?: LabelSource[]) => {
  const scene = deriveScene(events, idx, mode);
  return renderToStaticMarkup(
    createElement(LabelPopups, { scene, labels, mode, sources }, createElement(Table, { scene })),
  );
};

describe("labels data", () => {
  it("parses the fixture and the sibling url", () => {
    expect(labels).toHaveLength(8);
    expect(labels[1]).toMatchObject({ label: "false_role_claim", actor: "p5", source_seq: 19 });
    expect(siblingLabelsUrl("runs/b/g1.jsonl")).toBe("runs/b/g1.labels.jsonl");
  });
  it("rejects a bad line with its number", () => {
    expect(() => parseLabels('{"actor":"p0"}')).toThrow(/line 1/);
    expect(() => parseLabels("\n{nope")).toThrow(/line 2/);
  });
  it("a missing labels file gives []", async () => {
    vi.stubGlobal("fetch", async () => new Response("nope", { status: 404 }));
    expect(await fetchLabels("x.labels.jsonl")).toEqual([]);
    vi.stubGlobal("fetch", async () => new Response("<html>", { status: 200 }));
    expect(await fetchLabels("x.labels.jsonl")).toEqual([]);
    vi.stubGlobal("fetch", async () => {
      throw new Error("offline");
    });
    expect(await fetchLabels("x.labels.jsonl")).toEqual([]);
    vi.unstubAllGlobals();
  });
});

describe("LabelPopups", () => {
  it("shows type, actor, source, confidence and detail at source_seq, anchored to the actor", () => {
    const h = html(idxOfSeq(19), "narrator");
    expect(h).toContain('data-label-for="p5"');
    expect(h).toContain("false role claim");
    expect(h).toContain("self tag");
    expect(h).toContain("100% confidence");
    expect(h).toContain("Finn claimed Seer; he is a wolf.");
    expect(h).toContain('data-seat="p5"'); // Table still rendered inside
  });
  it("shows nothing before or after the source event", () => {
    expect(html(idxOfSeq(18), "narrator")).not.toContain("data-label-for");
    expect(html(idxOfSeq(21), "narrator")).not.toContain("data-label-for");
  });
  it("filters by source", () => {
    expect(html(idxOfSeq(98), "narrator", ["engine"])).toContain("mis elimination");
    expect(html(idxOfSeq(98), "narrator", ["jev"])).not.toContain("data-label-for");
  });
  it("villager mode never shows labels, even at the source event", () => {
    for (const l of labels) {
      const idx = idxOfSeq(l.source_seq);
      expect(visibleLabels(labels, deriveScene(events, idx, "villager"), "villager")).toEqual([]);
      expect(html(idx, "villager")).not.toContain("data-label-for");
    }
  });
  it("no labels renders just the table", () => {
    const scene = deriveScene(events, 0, "narrator");
    const h = renderToStaticMarkup(
      createElement(LabelPopups, { scene, labels: [], mode: "narrator" }, createElement(Table, { scene })),
    );
    expect(h).toContain('data-seat="p0"');
  });
});

describe("LabelPopups over a stepper beat", () => {
  const beatOf = (evs: typeof events, seq: number) =>
    buildBeats(evs).find((b) => b.firstSeq <= seq && seq <= b.lastSeq)!;
  const beatHtml = (evs: typeof events, seq: number, mode: Mode) => {
    const b = beatOf(evs, seq);
    const scene = deriveScene(evs, b.endIdx, mode);
    const range = { first: b.firstSeq, last: b.lastSeq };
    return {
      html: renderToStaticMarkup(createElement(LabelPopups, { scene, labels, mode, range })),
      shown: visibleLabels(labels, scene, mode, undefined, range),
    };
  };

  it("a vote beat shows the label on a vote that is not its last event", () => {
    const b = beatOf(events, 98);
    expect(b.lastSeq).toBeGreaterThan(102); // the beat ends on the elimination
    const { html: h, shown } = beatHtml(events, 98, "narrator");
    expect(shown.map((l) => l.source_seq)).toEqual([98, 99, 101, 102]);
    expect(h.match(/data-label-type="mis_elimination"/g)).toHaveLength(4);
  });

  it("a claim followed by a retry still shows its label", () => {
    const retry = { ...events[idxOfSeq(25)], seq: 20 }; // an invalid_output right after the role_claim at 19
    expect(retry.type).toBe("invalid_output");
    const evs = [...events.slice(0, idxOfSeq(19) + 1), retry];
    expect(beatOf(evs, 19).lastSeq).toBe(20);
    expect(beatHtml(evs, 19, "narrator").html).toContain("false role claim");
  });

  it("villager mode shows nothing for any beat", () => {
    for (const l of labels) {
      const { html: h, shown } = beatHtml(events, l.source_seq, "villager");
      expect(shown).toEqual([]);
      expect(h).not.toContain("data-label-for");
    }
  });

  it("without a range only the shown event matches", () => {
    const scene = deriveScene(events, idxOfSeq(104), "narrator");
    expect(visibleLabels(labels, scene, "narrator")).toEqual([]);
  });
});
