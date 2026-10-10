// .ts, not .tsx: vite.config.ts only collects src/**/*.test.ts.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import fixture from "../../fixtures/sample_game.jsonl?raw";
import labelsText from "../../fixtures/sample_game.labels.jsonl?raw";
import { Game } from "./App";
import { parseLabels } from "./data/labels";
import { parseLog } from "./data/parse";
import type { Mode } from "./scene/deriveScene";
import type { Label } from "./types/events";

const events = parseLog(fixture).events;
const labels = parseLabels(labelsText) as Label[];
const seats = Object.keys(events.find((e) => e.type === "game_start")!.meta.seats!);

const unescape = (h: string) =>
  h.replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");
const render = (idx: number, mode: Mode, selected: string | null) =>
  unescape(
    renderToStaticMarkup(
      createElement(Game, { events, labels, idx, mode, selected, onSelect: () => {}, onSeqChange: () => {} }),
    ),
  );

// Text a villager may see; a private string that also appears in it can't prove a leak.
const publicText = events.filter((e) => e.visibility === "public").map((e) => e.public_text ?? "").join("\n");
const secrets = [
  ...events.map((e) => e.private_reasoning),
  ...events.filter((e) => e.visibility !== "public").map((e) => e.public_text),
  ...events.map((e) => (typeof e.meta.raw === "string" ? e.meta.raw : null)),
  ...labels.map((l) => l.detail),
].filter((s): s is string => !!s && s.length >= 12 && !publicText.includes(s));

/** Seats whose role a villager knows after events[0..idx]. */
function revealedAt(idx: number): Set<string> {
  const out = new Set<string>();
  for (const e of events.slice(0, idx + 1)) {
    if (e.visibility !== "public") continue;
    if ((e.type === "death_reveal" || e.type === "elimination") && e.target && e.meta.role) out.add(e.target);
    if (e.type === "game_end") Object.keys(e.meta.roles ?? {}).forEach((id) => out.add(id));
  }
  return out;
}

/** Everything private that made it into the composed page. */
function leaks(html: string, idx: number): string[] {
  const found = secrets.filter((s) => html.includes(s)).map((s) => `secret: ${s.slice(0, 40)}`);
  if (html.includes("<meter") || html.includes("Suspicion")) found.push("suspicion");
  if (html.includes("Private reasoning") || html.includes("Wolf chat") || html.includes("Wolf channel"))
    found.push("private section");
  if (html.includes("data-label-type")) found.push("lie label");
  const revealed = revealedAt(idx);
  for (const [, seat, body] of html.matchAll(/<li data-seat="(p\d+)"[^>]*>(.*?)<\/li>/g)) {
    if (!revealed.has(seat) && !body.includes("role unknown")) found.push(`role: ${seat}`);
  }
  return found;
}

describe("App villager view", () => {
  it("shows nothing private at any event, whoever is selected", () => {
    expect(secrets.length).toBeGreaterThan(30);
    const found = new Set<string>();
    events.forEach((_, idx) => {
      for (const selected of [null, ...seats]) leaks(render(idx, "villager", selected), idx).forEach((l) => found.add(l));
    });
    expect([...found]).toEqual([]);
  });

  it("the same checks catch narrator content (so the villager test can fail)", () => {
    const idx = events.findIndex((e) => e.seq === 19); // Finn's false Seer claim, labelled at this seq
    const found = leaks(render(idx, "narrator", "p5"), idx);
    for (const kind of ["secret", "suspicion", "private section", "lie label", "role"])
      expect(found.some((f) => f.startsWith(kind))).toBe(true);
  });

  it("renders every part of the composed view", () => {
    const idx = events.findIndex((e) => e.seq === 54); // the day 1 tie
    const html = render(idx, "villager", "p0");
    expect(html.match(/data-seat=/g)).toHaveLength(seats.length);
    for (const part of ["Accusations and revealed votes", "Chat log", "Vote tally", "Inspector: Ava", "Game timeline"])
      expect(html).toContain(part);
  });
});
