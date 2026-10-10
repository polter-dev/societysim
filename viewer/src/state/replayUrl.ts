import { parseStep, withStep } from "./stepper";

/**
 * Normalise the share-link params before the stepper reads them. Highlight links carry `?seq=<n>`
 * (DATA_CONTRACT highlights); the stepper uses `?step=<seq>`. `step` wins, then `seq`, then the
 * game's first event, so a finished game opens at its start rather than at the winner.
 */
export function normalizeStepSearch(search: string, firstSeq: number): string {
  const p = new URLSearchParams(search);
  const legacy = p.get("seq");
  const start = parseStep(search) ?? (legacy !== null && /^\d+$/.test(legacy) ? Number(legacy) : firstSeq);
  p.delete("seq");
  const rest = p.toString();
  return withStep(rest ? `?${rest}` : "", start);
}

/** Apply `normalizeStepSearch` to the address bar (replace, not push). */
export function normalizeStepUrl(firstSeq: number): void {
  const search = normalizeStepSearch(window.location.search, firstSeq);
  if (search !== window.location.search)
    window.history.replaceState(window.history.state, "", window.location.pathname + search + window.location.hash);
}

/** Drop `step` and `seq` from the address bar, for a freshly opened file that belongs to no shared link. */
export function clearStepUrl(): void {
  const p = new URLSearchParams(window.location.search);
  p.delete("step");
  p.delete("seq");
  const rest = p.toString();
  window.history.replaceState(window.history.state, "", window.location.pathname + (rest ? `?${rest}` : "") + window.location.hash);
}
