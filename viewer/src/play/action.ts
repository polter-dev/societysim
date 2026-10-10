// Pure: turn a visitor's draft into the ActionBody for the pending action, or list what's wrong.
import type { ActionBody, PendingAction, SuspicionEntry } from "./types";

export const MAX_TEXT = 280;
export const DEFAULT_SUSPICION = 0.5;

export interface Draft {
  /** Statement text or wolf chat message. */
  text?: string;
  /** Vote or night target; wolf chat's proposed target. */
  target?: string;
  accuse?: string[];
  /** Per other living player, 0 to 1. Missing means 0.5. */
  suspicion?: Record<string, number>;
  /** Private notes; the server does not log them. */
  thoughts?: string;
}

export type Built = { ok: true; body: ActionBody } | { ok: false; errors: string[] };

export function buildAction(pending: PendingAction, d: Draft): Built {
  const errors: string[] = [];
  const text = (d.text ?? "").replace(/[\u0000-\u001f\u007f]/g, "").trim();
  const needsText = pending.kind === "statement" || pending.kind === "wolf_chat";
  if (needsText && (text.length < 1 || text.length > MAX_TEXT)) {
    errors.push(`Write between 1 and ${MAX_TEXT} characters (${text.length} now).`);
  }
  if (pending.kind !== "statement") {
    if (!d.target || !pending.targets.includes(d.target)) errors.push("Pick one of the allowed targets.");
  }
  const accuse = d.accuse ?? [];
  if (pending.kind === "statement") {
    const bad = accuse.filter((a) => !pending.others.includes(a));
    if (bad.length) errors.push(`Can't accuse ${bad.join(", ")}.`);
  }
  const suspicion: SuspicionEntry[] = pending.others.map((player) => ({
    player,
    p: d.suspicion?.[player] ?? DEFAULT_SUSPICION,
  }));
  for (const s of suspicion) {
    if (!Number.isFinite(s.p) || s.p < 0 || s.p > 1) errors.push(`Suspicion for ${s.player} must be between 0 and 1.`);
  }
  if (errors.length) return { ok: false, errors };

  const private_reasoning = { thoughts: d.thoughts ?? "", suspicion };
  const target = d.target ?? "";
  switch (pending.kind) {
    case "statement":
      return {
        ok: true,
        body: { private_reasoning, action: { text, accuse, claim_role: null, seer_report: null } },
      };
    case "vote":
      return { ok: true, body: { private_reasoning, action: { vote: target } } };
    case "wolf_chat":
      return { ok: true, body: { private_reasoning, action: { message: text, proposed_target: target } } };
    default:
      return { ok: true, body: { private_reasoning, action: { target } } };
  }
}

/** Whole seconds left until the deadline (epoch seconds); never negative. */
export const secondsLeft = (deadline: number, nowMs: number = Date.now()) =>
  Math.max(0, Math.ceil(deadline - nowMs / 1000));
