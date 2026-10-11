// Pure: turn the visitor's form state into the ActionBody for the pending action, or list what's wrong.
import type { ActionBody, PendingAction, Player } from "./types";

export const MAX_TEXT = 280;
export const ABSTAIN = "abstain";

/** The three answers per player, as P(wolf). Same values as agents/human.py LEVELS. */
export const LEVELS = { suspicious: 0.8, unsure: 0.5, trust: 0.2 } as const;
export type Level = keyof typeof LEVELS;

export interface Draft {
  /** Statement text or wolf chat message. */
  text?: string;
  /** Vote or night target; wolf chat's proposed target. A seat id, or "abstain" for a vote. */
  target?: string;
  accuse?: string[];
  /** Optional, per other living player. Players left out are logged as not rated, never as a default. */
  suspicion?: Partial<Record<string, Level>>;
}

export type Built = { ok: true; body: ActionBody } | { ok: false; errors: string[] };

/** "Ava" for a seat id ("Seat 4" if the name is unknown); "Abstain" for the abstain choice. Never the raw id. */
export function playerLabel(players: Player[], seat: string): string {
  if (seat === ABSTAIN) return "Abstain";
  return players.find((p) => p.seat === seat)?.name ?? `Seat ${Number(seat.slice(1)) + 1}`;
}

export function buildAction(pending: PendingAction, d: Draft): Built {
  const errors: string[] = [];
  const text = (d.text ?? "").replace(/[\u0000-\u001f\u007f]/g, "").trim();
  const needsText = pending.kind === "statement" || pending.kind === "wolf_chat";
  if (needsText && (text.length < 1 || text.length > MAX_TEXT)) {
    errors.push(`Write between 1 and ${MAX_TEXT} characters (${text.length} now).`);
  }
  if (pending.kind !== "statement") {
    if (!d.target || !pending.targets.includes(d.target)) errors.push("Pick a player.");
  }
  const accuse = d.accuse ?? [];
  if (pending.kind === "statement" && accuse.some((a) => !pending.targets.includes(a))) {
    errors.push("You can only accuse living players.");
  }
  if (errors.length) return { ok: false, errors };

  const suspicion = pending.others.flatMap((player) => {
    const level = d.suspicion?.[player];
    return level ? [{ player, p: LEVELS[level] }] : [];
  });
  // Nothing rated: no scratchpad at all; the server logs suspicion as null.
  const reasoning = suspicion.length ? { private_reasoning: { suspicion } } : {};
  const target = d.target ?? "";
  switch (pending.kind) {
    case "statement":
      return { ok: true, body: { ...reasoning, action: { text, accuse, claim_role: null, seer_report: null } } };
    case "vote":
      return { ok: true, body: { ...reasoning, action: { vote: target } } };
    case "wolf_chat":
      return { ok: true, body: { ...reasoning, action: { message: text, proposed_target: target } } };
    default:
      return { ok: true, body: { ...reasoning, action: { target } } };
  }
}

/** Whole seconds left until the deadline (epoch seconds); never negative. */
export const secondsLeft = (deadline: number, nowMs: number = Date.now()) =>
  Math.max(0, Math.ceil(deadline - nowMs / 1000));
