// .ts, not .tsx: vite.config.ts only collects src/**/*.test.ts.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import fixture from "../../../../fixtures/sample_game.jsonl?raw";
import { parseLog } from "../../data/parse";
import { deriveScene, type Mode } from "../../scene/deriveScene";
import type { Event } from "../../types/events";
import { ChatLog, chatGroups } from "./ChatLog";

const events = parseLog(fixture).events;
const render = (idx: number, mode: Mode) => renderToStaticMarkup(createElement(ChatLog, { scene: deriveScene(events, idx, mode) }));
// Escaped the way renderToStaticMarkup escapes text, so substring checks line up.
const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#x27;");
// Long enough to be unique: short wolf lines like "Dev." also occur inside public statements.
const distinct = (xs: (string | null)[]) => xs.flatMap((s) => (s && s.length > 20 ? [esc(s)] : []));
const wolfTexts = distinct(events.filter((e) => e.type === "wolf_chat").map((e) => e.public_text));
const privateTexts = distinct(events.map((e) => e.private_reasoning));
const last = events.length - 1;

describe("ChatLog on the fixture", () => {
  it("villager mode never shows the wolf channel or any private field", () => {
    const html = render(last, "villager");
    expect(wolfTexts.length).toBeGreaterThan(5);
    expect(privateTexts.length).toBeGreaterThan(20);
    for (const t of wolfTexts) expect(html).not.toContain(t);
    for (const t of privateTexts) expect(html).not.toContain(t);
    expect(html).not.toMatch(/Wolf channel|data-channel="wolves"/);
    expect(html).toContain(esc(events[15].public_text!)); // public statements still there
  });

  it("narrator mode shows the wolf channel, marked, but still no private reasoning", () => {
    const html = render(last, "narrator");
    for (const t of wolfTexts) expect(html).toContain(t);
    expect(html).toContain('data-channel="wolves"');
    expect(html).toContain("Wolf channel");
    for (const t of privateTexts) expect(html).not.toContain(t);
  });

  it("lists statements in log order under day/phase headers, up to the current event", () => {
    const groups = chatGroups(deriveScene(events, 69, "narrator"));
    expect(groups.map((g) => g.title)).toEqual(["Night 1", "Day 1", "Night 2", "Day 2"]);
    const seqs = groups.flatMap((g) => g.entries.map((e) => e.seq));
    expect(seqs).toEqual([...seqs].sort((a, b) => a - b));
    expect(seqs.at(-1)).toBe(69);
    expect(chatGroups(deriveScene(events, 69, "villager")).map((g) => g.title)).toEqual(["Day 1", "Day 2"]);
  });

  it("marks the newest entry as current, and shows an empty state before anyone speaks", () => {
    expect(render(16, "villager")).toMatch(/<li data-seq="16"[^>]*aria-current="true"/);
    expect(render(16, "villager").match(/aria-current/g)).toHaveLength(1);
    expect(render(1, "villager")).toContain("No one has spoken yet.");
  });
});

describe("ChatLog edge cases", () => {
  const base = { schema_version: 2, game_id: "g", t: 1, day: 1, phase: "day", type: "statement", visibility: "public", target: null };
  const ev = (o: Record<string, unknown>) =>
    ({ ...base, actor: null, public_text: null, private_reasoning: null, suspicion: null, meta: {}, ...o }) as unknown as Event;
  const log = [
    ev({ seq: 0, type: "game_start", phase: "setup", meta: { seats: { p0: { name: "Ann", model: "m", color: "#000", role: "wolf" } } } }),
    ev({ seq: 1, actor: "p0", public_text: '<img src=x onerror="alert(1)"><script>alert(2)</script>' }),
    ev({ seq: 2, actor: "p0", public_text: "a very long speech", meta: { truncated: true } }),
    ev({ seq: 3, actor: "p0", public_text: "(silent)", meta: { fallback: true } }),
  ];
  const html = renderToStaticMarkup(createElement(ChatLog, { scene: deriveScene(log, 3, "villager") }));

  it("renders model text as plain text, never HTML", () => {
    expect(html).not.toMatch(/<img|<script/);
    expect(html).toContain("&lt;img src=x onerror=&quot;alert(1)&quot;&gt;&lt;script&gt;");
  });

  it("flags truncated statements and silent fallbacks", () => {
    expect(html).toMatch(/a very long speech… <small data-truncated[^>]*>\(truncated\)/);
    expect(html).toMatch(/<em data-fallback[^>]*>\(silent\) <small>no valid reply/);
  });
});
