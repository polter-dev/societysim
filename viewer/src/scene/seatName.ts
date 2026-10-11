/** A seat's display name: its player name, else "Seat N". Never the raw `p3` id. */
export function seatName(players: readonly { id: string; name: string }[], id: string | null | undefined): string {
  if (!id) return "nobody";
  return players.find((p) => p.id === id)?.name ?? `Seat ${Number(id.slice(1)) + 1}`;
}
