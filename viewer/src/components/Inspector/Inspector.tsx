import { useEffect, useRef, type KeyboardEvent } from "react";
import type { Event } from "../../types/events";
import type { Mode, Scene } from "../../scene/deriveScene";

export interface InspectorProps {
  scene: Scene;
  mode: Mode;
  /** Selected seat id, or null for closed. */
  selected: string | null;
  onSelect: (seat: string | null) => void;
}

/** The seat's latest visible speech, wolf activity, reasoning, and suspicion row. */
export function inspect(scene: Scene, seat: string) {
  const mine = scene.log.filter((e) => e.actor === seat);
  const last = (pick: (e: Event) => unknown) => [...mine].reverse().find((e) => pick(e));
  const spoke = last((e) => e.visibility === "public" && e.public_text);
  const wolfChat = last((e) => e.type === "wolf_chat" && e.visibility === "wolves" && e.public_text);
  const wolfProposal = last((e) => e.type === "wolf_proposal" && e.visibility === "wolves" && e.target);
  const reasoned = last((e) => e.private_reasoning);
  const susp = last((e) => e.suspicion)?.suspicion ?? [];
  const alive = new Set(scene.players.filter((p) => p.alive).map((p) => p.id));
  return {
    publicText: spoke?.public_text ?? null,
    wolfChat: wolfChat?.public_text ?? null,
    wolfProposal: wolfProposal?.target ?? null,
    reasoning: reasoned?.private_reasoning ?? null,
    suspicion: susp.filter((s) => s.player !== seat && alive.has(s.player)),
  };
}

/** Components only render: all derivation is in inspect() and deriveScene(). */
export function Inspector({ scene, mode, selected, onSelect }: InspectorProps) {
  const ref = useRef<HTMLElement>(null);
  // Focus follows the selection, so arrow keys move focus too, not only the pressed state.
  useEffect(() => {
    ref.current?.querySelector<HTMLElement>('[aria-pressed="true"]')?.focus();
  }, [selected]);
  const living = scene.players.filter((p) => p.alive);
  const player = scene.players.find((p) => p.id === selected);
  if (!player) return null;

  const name = (id: string) => scene.players.find((p) => p.id === id)?.name ?? id;
  const { publicText, wolfChat, wolfProposal, reasoning, suspicion } = inspect(scene, player.id);

  const onKeyDown = (ev: KeyboardEvent) => {
    if (ev.key === "Escape") return onSelect(null);
    const step = ev.key === "ArrowDown" || ev.key === "ArrowRight" ? 1 : ev.key === "ArrowUp" || ev.key === "ArrowLeft" ? -1 : 0;
    if (!step || living.length === 0) return;
    ev.preventDefault();
    const i = living.findIndex((p) => p.id === selected);
    onSelect(living[(i + step + living.length) % living.length].id);
  };

  return (
    // A region, not an aside: a complementary landmark can't sit inside <main>.
    <section ref={ref} className="inspector" aria-label={`Inspector: ${player.name}`} onKeyDown={onKeyDown}>
      <header>
        <h2>{player.name}</h2>
        <button type="button" onClick={() => onSelect(null)} aria-label="Close inspector">
          Close
        </button>
      </header>
      <ul aria-label="Players">
        {scene.players.map((p) => (
          <li key={p.id}>
            <button type="button" aria-pressed={p.id === selected} disabled={!p.alive} onClick={() => onSelect(p.id)}>
              {p.name}
            </button>
          </li>
        ))}
      </ul>
      <section aria-label="Public">
        <h3>Said publicly</h3>
        <p>{publicText ?? "Nothing yet."}</p>
      </section>
      {mode === "narrator" && (
        <>
          {(wolfChat || wolfProposal) && (
            <section aria-label="Wolf chat (private)">
              <h3>Wolf chat (private)</h3>
              {wolfChat && <p>{wolfChat}</p>}
              {wolfProposal && <p>Proposed target: {name(wolfProposal)}</p>}
            </section>
          )}
          <section aria-label="Private reasoning">
            <h3>Private reasoning</h3>
            <p>{reasoning ?? "Nothing yet."}</p>
          </section>
          <section aria-label="Suspicion">
            <h3>Suspicion (P wolf)</h3>
            <ul>
              {suspicion.map((s) => (
                <li key={s.player}>
                  <label>
                    {name(s.player)}
                    <meter min={0} max={1} value={s.p}>
                      {s.p.toFixed(2)}
                    </meter>
                    {Math.round(s.p * 100)}%
                  </label>
                </li>
              ))}
            </ul>
          </section>
        </>
      )}
    </section>
  );
}
