// @vitest-environment jsdom
// The panel's controls fill a Draft; buildAction turns it into the body the server gets.
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { buildAction, type Draft, LEVELS } from "../../play/action";
import type { Me, PendingAction } from "../../play/types";
import { YourTurnPanel } from "./YourTurnPanel";

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

function render(pending: PendingAction) {
  const seat = { me: me(pending), pending, error: null, errors: [], busy: false, secondsLeft: 30, submit, refresh: vi.fn() };
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
    expect(box.textContent).toContain("Ava (p1)");
    expect(box.querySelector("input[type=number]")).toBeNull();
    click(button("Bo (p2)"));
    expect(button("Bo (p2)").getAttribute("aria-pressed")).toBe("true");
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
    click(button("Ava (p1)"));
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
});
