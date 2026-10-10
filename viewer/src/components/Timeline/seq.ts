import type { Event } from "../../types/events";

export function indexForSeq(events: readonly Event[], seq: number): number {
  return events.findIndex((event) => event.seq === seq);
}

export function nearestSeq(events: readonly Event[], value: number): number | null {
  if (!events.length) return null;
  if (!Number.isFinite(value)) return events[0].seq;
  let nearest = events[0].seq;
  for (const event of events) {
    if (Math.abs(event.seq - value) < Math.abs(nearest - value)) nearest = event.seq;
  }
  return nearest;
}

export function stepSeq(events: readonly Event[], current: number, delta: number): number | null {
  if (!events.length) return null;
  const index = indexForSeq(events, current);
  return events[Math.max(0, Math.min((index < 0 ? 0 : index) + delta, events.length - 1))].seq;
}

export function readSeqParam(events: readonly Event[], search: string): number | null {
  if (!events.length) return null;
  const raw = new URLSearchParams(search).get("seq");
  if (raw === null || !/^-?\d+$/.test(raw)) return events[0].seq;
  const seq = Number(raw);
  return indexForSeq(events, seq) < 0 ? events[0].seq : seq;
}

export function withSeqParam(search: string, seq: number): string {
  const params = new URLSearchParams(search);
  params.set("seq", String(seq));
  return `?${params.toString()}`;
}
