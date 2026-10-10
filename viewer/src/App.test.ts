// .ts, not .tsx: vite.config.ts only collects src/**/*.test.ts.
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import fixture from "../../fixtures/sample_game.jsonl?raw";
import labelsText from "../../fixtures/sample_game.labels.jsonl?raw";
import modesText from "./__fixtures__/modes_game.jsonl?raw";
import { Game } from "./App";
import { parseLabels } from "./data/labels";
import { parseLog } from "./data/parse";
import type { Mode } from "./scene/deriveScene";
import type { Label } from "./types/events";

const unescape = (h: string) =>
  h.replace(/&#x27;/g, "'").replace(/&quot;/g, '"').replace(/&lt;/g, "<").replace(/&gt;/g, ">").replace(/&amp;/g, "&");

// Screen readers read these out, so they count as rendered.
const ariaText = (html: string) => [...html.matchAll(/(?:aria-label|title)="([^"]*)"/g)].map((m) => m[1]).join("\n");

/** One log, rendered through Game. The leak checks run against every event and every selected seat. */
function load(text: string, labelsJsonl: string) {
  const events = parseLog(text).events;
  const labels = parseLabels(labelsJsonl) as Label[];
  const seats = Object.keys(events.find((e) => e.type === "game_start")!.meta.seats!);
  const render = (idx: number, mode: Mode, selected: string | null) =>
    unescape(
      renderToStaticMarkup(
        createElement(Game, { events, labels, idx, mode, selected, onSelect: () => {}, onSeqChange: () => {} }),
      ),
    );

  // Same page with the stepper and narration controls mounted, narration on and every category ticked.
  const everything = { narrator: true, speech: true, wolf_chat: true, highlight: true };
  const renderWithControls = (idx: number, mode: Mode, selected: string | null) =>
    unescape(
      renderToStaticMarkup(
        createElement(Game, {
          events, labels, idx, mode, selected, onSelect: () => {},
          step: { pos: 3, count: 12, live: false, atLatest: false, newCount: 0, onPrev: () => {}, onNext: () => {}, onLive: () => {} },
          seq: events[idx].seq, onSeek: () => {},
          narration: { available: true, on: true, enabled: everything, onToggle: () => {}, onCategory: () => {} },
        }),
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

  return { events, seats, secrets, revealedAt, leaks, render, renderWithControls };
}

const sample = load(fixture, labelsText);
const { events, seats, secrets, revealedAt, leaks, render, renderWithControls } = sample;

describe("App villager view", () => {
  it("shows nothing private at any event, whoever is selected", () => {
    expect(secrets.length).toBeGreaterThan(30);
    const found = new Set<string>();
    events.forEach((_, idx) => {
      for (const selected of [null, ...seats]) leaks(render(idx, "villager", selected), idx).forEach((l) => found.add(l));
    });
    expect([...found]).toEqual([]);
  }, 30_000);

  it("the stepper and narration controls add nothing private, with every category ticked", () => {
    const found = new Set<string>();
    events.forEach((_, idx) => {
      const html = renderWithControls(idx, "villager", null);
      expect(html).toContain("Narration on");
      expect(html).toContain("Next beat");
      expect(html).not.toMatch(/Wolf chat|Highlights/);
      leaks(html, idx).forEach((l) => found.add(l));
    });
    expect([...found]).toEqual([]);
  }, 30_000);

  it("the narrator view does offer the hidden-info narration categories (so the check above can fail)", () => {
    const html = renderWithControls(0, "narrator", null);
    expect(html).toContain("Wolf chat (narrator view only)");
    expect(html).toContain("Highlights (narrator view only)");
  });

  it("the same checks catch narrator content (so the villager test can fail)", () => {
    const idx = events.findIndex((e) => e.seq === 19); // Finn's false Seer claim, labelled at this seq
    const found = leaks(render(idx, "narrator", "p5"), idx);
    for (const kind of ["secret", "suspicion", "private section", "lie label", "role"])
      expect(found.some((f) => f.startsWith(kind))).toBe(true);
  });

  it("aria-labels and titles name no role before any role is public", () => {
    // Screen readers read these out, so they count as rendered. The secrets scan above covers them too.
    const roleWord = /\b(seer|doctor|wolf|wolves|villager)\b/i;
    const firstReveal = events.findIndex((_, idx) => revealedAt(idx).size > 0);
    expect(firstReveal).toBeGreaterThan(0);
    for (let idx = 0; idx < firstReveal; idx++) {
      for (const selected of [null, ...seats]) expect(ariaText(render(idx, "villager", selected))).not.toMatch(roleWord);
    }
    // The narrator view does put role words there, so this check can fail.
    expect(ariaText(render(events.findIndex((e) => e.seq === 19), "narrator", "p5"))).toMatch(roleWord);
  });

  it("renders every part of the composed view", () => {
    const idx = events.findIndex((e) => e.seq === 54); // the day 1 tie
    const html = render(idx, "villager", "p0");
    expect(html.match(/data-seat=/g)).toHaveLength(seats.length);
    for (const part of ["Accusations and revealed votes", "Chat log", "Vote tally", "Inspector: Ava", "Game timeline"])
      expect(html).toContain(part);
  });
});

describe("mode-only data behind Arrows, Tally and Speech", () => {
  const m = load(modesText, "");
  // Strings only the narrator may see. Each is what a leaking Arrows, Tally or Speech would print.
  const hidden = [
    "Cleo accused Ben", // night 1 wolf proposal drawn as an accusation arrow
    "Finn accused Elif", // night 1 second proposal
    "Finn accused Ava", // night 2 proposal
    "Ben: 2", // night kill tally shown as a vote tally
    "We take Ben tonight", // wolf_chat bubbled by Speech
    "Take her before she talks", // night 2 wolf_chat
    "Elif gave two invalid votes", // narrator-only fallback reason
    "I vote nobody", // raw invalid output
  ];

  it("villager view shows none of it, in markup, aria-labels or titles", () => {
    const found = new Set<string>();
    m.events.forEach((_, idx) => {
      for (const selected of [null, ...m.seats]) {
        const html = m.render(idx, "villager", selected);
        for (const s of hidden) if (html.includes(s) || ariaText(html).includes(s)) found.add(s);
        m.leaks(html, idx).forEach((l) => found.add(l));
      }
    });
    expect([...found]).toEqual([]);
  }, 30_000);

  it("narrator view shows it where the page allows it", () => {
    const wolfTalk = m.events.findIndex((e) => e.seq === 5);
    const html = m.render(wolfTalk, "narrator", "p2");
    for (const s of ["We take Ben tonight", "Agreed on Ben", "Proposed target: Ben"]) expect(html).toContain(s);
  });

  it("the forced abstain shows in both modes once the day result is public", () => {
    const result = m.events.findIndex((e) => e.seq === 24); // Cleo eliminated
    for (const mode of ["narrator", "villager"] as const) {
      const html = m.render(result, mode, null);
      expect(html).toContain("Elif was forced to abstain"); // Arrows
      expect(html).toContain("Forced abstain: Elif"); // Tally
    }
  });
});
