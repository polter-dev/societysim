import type { Scene } from "../../scene/deriveScene";
import { seatName } from "../../scene/seatName";

/** The vote is secret until its result event; the event also carries the final tally. */
export function Tally({ scene }: { scene: Scene }) {
  const result = [...scene.log].reverse().find(
    (e) => e.day === scene.day && (e.type === "elimination" || e.type === "no_elimination"),
  );
  const name = (id: string) => seatName(scene.players, id);

  // Both states share one live section, so the reveal is announced when it replaces "pending".
  if (!result) return <section aria-label="Vote tally" aria-live="polite"><h2>Vote tally</h2><p>Votes pending reveal.</p></section>;

  const abstains = scene.votes.filter((vote) => vote.target === null);
  const allAbstain = result.type === "no_elimination" && Object.values(scene.tally).every((n) => n === 0);
  const tie = result.type === "no_elimination" && !allAbstain;

  return (
    <section aria-label="Vote tally" aria-live="polite">
      <h2>Vote tally</h2>
      <ul>
        {scene.players.filter((p) => (scene.tally[p.id] ?? 0) > 0).map((p) => (
          <li key={p.id}>{name(p.id)}: {scene.tally[p.id]}</li>
        ))}
        <li>Abstain: {abstains.length}</li>
      </ul>
      {abstains.some((vote) => vote.fallback) && (
        <p>Forced abstain: {abstains.filter((vote) => vote.fallback).map((vote) => name(vote.actor)).join(", ")}</p>
      )}
      {tie && <p>Tie. No elimination.</p>}
      {allAbstain && <p>All abstained. No elimination.</p>}
      {result.type === "elimination" && <p>{name(result.target ?? "")} eliminated.</p>}
    </section>
  );
}
