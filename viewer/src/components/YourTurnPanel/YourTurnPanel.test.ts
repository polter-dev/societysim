// @vitest-environment jsdom
// The panel's controls fill a Draft; buildAction turns it into the body the server gets.
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { buildAction, type Draft, LEVELS } from "../../play/action";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { parseLog } from "../../data/parse";
import type { Me, PendingAction } from "../../play/types";
import type { Event } from "../../types/events";
import { privateNotes, YourTurnPanel } from "./YourTurnPanel";

const leaveGame = vi.hoisted(() => vi.fn(async (_id: string) => ({ accepted: true as const })));
vi.mock("../../play/api", () => ({ leaveGame }));

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const players = [
  { seat: "p0", name: "You", alive: true },
  { seat: "p1", name: "Ava", alive: true },
  { seat: "p2", name: "Bo", alive: true },
];
const me = (pending: PendingAction): Me => ({
  game_id: "g1", status: "running", winner: null, seat: "p0", name: "You", role: "villager", alive: true,
  day: 1, phase: "vote", observation: "Now: Day 1, vote.", pending_action: pending, players,
});

let root: Root;
let box: HTMLElement;
let submit: ReturnType<typeof vi.fn<(d: Draft) => Promise<boolean>>>;

beforeEach(() => {
  box = document.body.appendChild(document.createElement("div"));
  root = createRoot(box);
  submit = vi.fn(async (_d: Draft) => true);
});
afterEach(() => {
  act(() => root.unmount());
  box.remove();
});

function render(pending: PendingAction, refresh = vi.fn(async () => {})) {
  const seat = { me: me(pending), gone: false, pending, error: null, errors: [], busy: false, secondsLeft: 30, submit, refresh };
  act(() => root.render(createElement(YourTurnPanel, { seat })));
}
const button = (text: string, scope: ParentNode = box) => {
  const b = [...scope.querySelectorAll("button")].find((x) => x.textContent?.includes(text));
  if (!b) throw new Error(`no button "${text}"`);
  return b;
};
const click = (b: HTMLElement) => act(() => b.click());
const send = () => act(() => box.querySelector("form")!.requestSubmit());
const sent = (pending: PendingAction) => buildAction(pending, submit.mock.calls[0][0]);

describe("YourTurnPanel", () => {
  it("votes by name, with no JSON and no seat ids to type", () => {
    const pending: PendingAction = { kind: "vote", targets: ["p1", "p2", "abstain"], others: ["p1", "p2"], deadline: 0 };
    render(pending);
    expect(box.textContent).toContain("Ava");
    expect(box.querySelector("input[type=number]")).toBeNull();
    click(button("Bo"));
    expect(button("Bo").getAttribute("aria-pressed")).toBe("true");
    send();
    expect(sent(pending)).toEqual({ ok: true, body: { action: { vote: "p2" } } });
  });

  it("abstain is its own button", () => {
    const pending: PendingAction = { kind: "vote", targets: ["p1", "abstain"], others: ["p1"], deadline: 0 };
    render(pending);
    click(button("Abstain"));
    send();
    expect(sent(pending)).toEqual({ ok: true, body: { action: { vote: "abstain" } } });
  });

  it("statement: text box, accuse buttons and optional ratings build the payload", () => {
    const pending: PendingAction = { kind: "statement", targets: ["p1", "p2"], others: ["p1", "p2"], deadline: 0 };
    render(pending);
    const text = box.querySelector("textarea")!;
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(text, "Ava is lying.");
      text.dispatchEvent(new Event("input", { bubbles: true }));
    });
    click(button("Ava"));
    const ava = box.querySelector('.your-turn__suspicion fieldset[data-seat="p1"]')!;
    const bo = box.querySelector('.your-turn__suspicion fieldset[data-seat="p2"]')!;
    click(button("trust", bo));
    click(button("unsure", bo)); // changed their mind
    click(button("suspicious", ava));
    click(button("suspicious", ava)); // a second click clears it
    send();
    expect(sent(pending)).toEqual({
      ok: true,
      body: {
        private_reasoning: { suspicion: [{ player: "p2", p: LEVELS.unsure }] },
        action: { text: "Ava is lying.", accuse: ["p1"], claim_role: null, seer_report: null },
      },
    });
  });

  it("Leave game asks first, then ends the seat's game on the server", async () => {
    const pending: PendingAction = { kind: "vote", targets: ["p1"], others: ["p1"], deadline: 0 };
    const refresh = vi.fn(async () => {});
    render(pending, refresh);
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValueOnce(true);
    click(button("Leave game"));
    expect(leaveGame).not.toHaveBeenCalled();
    await act(async () => button("Leave game").click());
    expect(leaveGame).toHaveBeenCalledWith("g1");
    expect(refresh).toHaveBeenCalled();
    confirm.mockRestore();
  });
});

describe("discussion log in the panel", () => {
  const events = parseLog(readFileSync(resolve(process.cwd(), "../fixtures/sample_game.jsonl"), "utf8")).events;
  const long = (xs: (string | null)[]) => xs.filter((s): s is string => !!s && s.length > 20);
  const wolfTexts = long(events.filter((e) => e.type === "wolf_chat").map((e) => e.public_text));
  const privateTexts = long(events.map((e) => e.private_reasoning));
  const statement = events.find((e) => e.type === "statement")!.public_text!;
  const PUBLIC_OBS = 'Now: Day 1, discussion.\n\nToday so far:\n- Omar (p8): claimed to be villager; last said: "I agree with everyone..."';
  const DOCTOR_OBS = `Now: Day 1, discussion.\nYour protects: night 1: Ben (p1).\n\nToday so far:\n- Omar (p8): last said: "I agree with everyone..."`;

  function renderLog(role: Me["role"], observation: string, evs: Event[] = events) {
    const mine: Me = { ...me({ kind: "vote", targets: ["p1"], others: ["p1"], deadline: 0 }), role, observation, pending_action: null };
    const seat = { me: mine, gone: false, pending: null, error: null, errors: [], busy: false, secondsLeft: null, submit, refresh: vi.fn(async () => {}) };
    act(() => root.render(createElement(YourTurnPanel, { seat, events: evs })));
  }

  it("shows the pop-up's Tally and formatted Chat log instead of raw observation text", () => {
    renderLog("villager", PUBLIC_OBS);
    expect(box.querySelector('[aria-label="Vote tally"]')).not.toBeNull();
    expect(box.querySelector('[aria-label="Chat log"] li[aria-current="true"]')).not.toBeNull();
    expect(box.textContent).toContain(statement);
    expect(box.textContent).not.toContain("last said");
    expect(box.querySelector(".your-turn__notes")).toBeNull();
    expect(box.textContent).toContain("Leave game");
  });

  it.each([
    ["villager", PUBLIC_OBS],
    ["doctor", DOCTOR_OBS],
  ] as const)("a %s never sees the wolf channel, seer checks or anyone's private reasoning", (role, obs) => {
    // The fixture is the full narrator log: wolf chat, seer checks and reasoning are all in the events.
    renderLog(role, obs);
    const text = box.textContent ?? "";
    expect(wolfTexts.length).toBeGreaterThan(5);
    expect(privateTexts.length).toBeGreaterThan(20);
    for (const t of [...wolfTexts, ...privateTexts]) expect(text).not.toContain(t);
    expect(text).not.toMatch(/Wolf channel|Your Seer checks/);
    expect(box.querySelector('[data-channel="wolves"]')).toBeNull();
  });

  it("keeps only the role's own private lines as Your notes", () => {
    renderLog("doctor", DOCTOR_OBS);
    const notes = box.querySelector(".your-turn__notes")?.textContent ?? "";
    expect(notes).toContain("Your protects: night 1: Ben (p1).");
    expect(notes).not.toContain("last said");
  });

  it("without live events it falls back to the observation text", () => {
    renderLog("villager", PUBLIC_OBS, []);
    expect(box.querySelector(".your-turn__private")?.textContent).toContain("last said");
    expect(box.querySelector('[aria-label="Chat log"]')).toBeNull();
  });
});

describe("privateNotes", () => {
  it("keeps knowledge lines and the wolf channel, drops public history", () => {
    const obs = [
      "Now: Night 2.",
      "Your fellow wolves: Dev (p4).",
      "Dead: Ava (p1), villager.",
      "Day 1 summary:",
      "- (wolf channel) The pack attacked Ava (p1).",
      '- Omar (p8): claimed to be villager; last said: "hi"',
      "Today so far:",
      '(wolf channel) Dev (p4): "take Cleo"',
    ].join("\n");
    expect(privateNotes(obs)).toEqual([
      "Your fellow wolves: Dev (p4).",
      "(wolf channel) The pack attacked Ava (p1).",
      '(wolf channel) Dev (p4): "take Cleo"',
    ]);
    expect(privateNotes("Your Seer checks: night 1: Cleo (p2) is a wolf.")).toHaveLength(1);
    expect(privateNotes(null)).toEqual([]);
  });
});
