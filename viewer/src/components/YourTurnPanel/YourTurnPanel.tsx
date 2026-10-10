// Unstyled "your turn" panel: role card, private info, the action form and a countdown.
// Everything comes from useHumanSeat; the live table itself uses the existing Neon live mode.
import { useEffect, useState } from "react";
import { DEFAULT_SUSPICION, type Draft, MAX_TEXT } from "../../play/action";
import type { useHumanSeat } from "../../play/useHumanSeat";

type Seat = ReturnType<typeof useHumanSeat>;

export function YourTurnPanel({ seat }: { seat: Seat }) {
  const { me, pending } = seat;
  const [draft, setDraft] = useState<Draft>({});
  // A new prompt (kind or deadline) starts with a clean form.
  const key = pending ? `${pending.kind}:${pending.deadline}` : "";
  useEffect(() => setDraft({}), [key]);

  if (!me) return <section className="your-turn">{seat.error ?? "Loading your seat..."}</section>;
  const set = (patch: Partial<Draft>) => setDraft((d) => ({ ...d, ...patch }));
  const needsText = pending?.kind === "statement" || pending?.kind === "wolf_chat";

  return (
    <section className="your-turn">
      <div className="your-turn__role">
        <h3>
          {me.name} ({me.seat}): {me.role}
        </h3>
        <p>
          Day {me.day}, {me.phase}. {me.alive ? "You are alive." : "You are out."}
        </p>
        {me.status !== "running" && (
          <p>
            Game {me.status}
            {me.winner ? `: ${me.winner} win` : ""}.
          </p>
        )}
      </div>
      {me.observation && <pre className="your-turn__private">{me.observation}</pre>}
      {seat.error && <p role="alert">{seat.error}</p>}

      {pending && (
        <form
          className="your-turn__form"
          onSubmit={(e) => {
            e.preventDefault();
            void seat.submit(draft);
          }}
        >
          <p>
            Your move: {pending.kind.replace("_", " ")}
            {seat.secondsLeft !== null && <span aria-live="off"> ({seat.secondsLeft}s left)</span>}
          </p>
          {needsText && (
            <label>
              {pending.kind === "wolf_chat" ? "Message to your pack" : "What do you say?"}
              <textarea
                maxLength={MAX_TEXT}
                value={draft.text ?? ""}
                onChange={(e) => set({ text: e.target.value })}
              />
              <small>
                {(draft.text ?? "").length}/{MAX_TEXT}
              </small>
            </label>
          )}
          {pending.kind !== "statement" && (
            <label>
              {pending.kind === "wolf_chat" ? "Proposed target" : "Target"}
              <select value={draft.target ?? ""} onChange={(e) => set({ target: e.target.value })}>
                <option value="">Choose...</option>
                {pending.targets.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </label>
          )}
          {pending.kind === "statement" && (
            <fieldset>
              <legend>Accuse (optional)</legend>
              {pending.others.map((o) => (
                <label key={o}>
                  <input
                    type="checkbox"
                    checked={draft.accuse?.includes(o) ?? false}
                    onChange={(e) =>
                      set({ accuse: e.target.checked ? [...(draft.accuse ?? []), o] : (draft.accuse ?? []).filter((a) => a !== o) })
                    }
                  />{" "}
                  {o}
                </label>
              ))}
            </fieldset>
          )}
          <fieldset>
            <legend>How likely is each player a wolf? (0 to 1)</legend>
            {pending.others.map((o) => (
              <label key={o}>
                {o}{" "}
                <input
                  type="number"
                  min={0}
                  max={1}
                  step={0.05}
                  value={draft.suspicion?.[o] ?? DEFAULT_SUSPICION}
                  onChange={(e) => set({ suspicion: { ...draft.suspicion, [o]: Number(e.target.value) } })}
                />
              </label>
            ))}
          </fieldset>
          <label>
            Private notes (not logged)
            <textarea value={draft.thoughts ?? ""} onChange={(e) => set({ thoughts: e.target.value })} />
          </label>
          {seat.errors.length > 0 && (
            <ul role="alert">
              {seat.errors.map((m) => (
                <li key={m}>{m}</li>
              ))}
            </ul>
          )}
          <button type="submit" disabled={seat.busy}>
            {seat.busy ? "Sending..." : "Submit"}
          </button>
        </form>
      )}
    </section>
  );
}
