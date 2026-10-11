import { visibleIn, type Mode } from "../../scene/deriveScene";
import type { Event } from "../../types/events";

/** Autoplay dwell for events with no readable text. */
export const BASE_MS = 1000;
const TEXT_BASE_MS = 1500;
const MS_PER_WORD = 250; // ~240 words per minute
const MIN_TEXT_MS = 2000;
const MAX_TEXT_MS = 12000;

/**
 * How long autoplay stays on `event` before advancing, at 1x speed.
 * Only `public_text` the viewer can see counts; private_reasoning never does, so timing leaks nothing.
 * Text events: clamp(1500 + 250 * words, 2000, 12000) ms. Everything else: 1000 ms.
 */
export function dwellMs(event: Event, mode: Mode): number {
  const text = visibleIn(event, mode) ? event.public_text?.trim() : "";
  if (!text) return BASE_MS;
  const words = text.split(/\s+/).length;
  return Math.min(MAX_TEXT_MS, Math.max(MIN_TEXT_MS, TEXT_BASE_MS + MS_PER_WORD * words));
}

/** Dwell for the real timer: `dwellMs` divided by the speed multiplier. */
export function stepDelayMs(event: Event, mode: Mode, speed: number): number {
  return dwellMs(event, mode) / speed;
}
