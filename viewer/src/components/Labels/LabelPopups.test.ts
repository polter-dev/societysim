// .ts, not .tsx: vite.config.ts only collects src/**/*.test.ts.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import fixture from "../../../../fixtures/sample_game.jsonl?raw";
import labelsText from "../../../../fixtures/sample_game.labels.jsonl?raw";
import { fetchLabels, parseLabels, siblingLabelsUrl, type LabelSource } from "../../data/labels";
import { parseLog } from "../../data/parse";
import { deriveScene, type Mode } from "../../scene/deriveScene";
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
