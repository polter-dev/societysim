// Unstyled "your turn" panel: role card, private info, the action form and a countdown.
// Plain controls only: a text box for speech, player buttons for every choice, optional
// suspicious/unsure/trust buttons. The client builds the JSON (play/action.ts).
// Everything comes from useHumanSeat; the live table itself uses the existing Neon live mode.
import { useEffect, useState } from "react";
import { leaveGame } from "../../play/api";
import { ABSTAIN, type Draft, LEVELS, type Level, MAX_TEXT, playerLabel } from "../../play/action";
import { ModelAvatar, useModelLabel } from "../ModelAvatar/ModelAvatar";
import type { Player } from "../../play/types";
import type { useHumanSeat } from "../../play/useHumanSeat";

import { art } from "../../illustrated/assets";

type Seat = ReturnType<typeof useHumanSeat>;

function roleArt(role: string): string | undefined {
  if (role === "wolf" || role === "werewolf") return art("new/event_werewolf 1.png");
  if (role === "doctor") return art("new/event_protection 1.png");
  if (role === "seer") return art("new/event_investigation 1.png");
  if (role === "villager") return art("inspector/05_group.png");
  return undefined;
}

const PICK_LABEL: Record<string, string> = {
  vote: "Vote to eliminate",
  wolf_chat: "Who should the pack kill tonight?",
  lone_wolf: "Who do you kill tonight?",
  seer: "Whose role do you check?",
  doctor: "Who do you protect tonight?",
};
const MOVE: Record<string, string> = {
  statement: "speak to the village",
  vote: "vote",
  wolf_chat: "talk with your pack",
  lone_wolf: "choose a kill",
  seer: "check a player",
  doctor: "protect a player",
};

/** A player as a button: initial avatar and name. `pressed` marks the current choice. */
function PlayerButton({ players, seat, pressed, onClick }: {
  players: Player[]; seat: string; pressed: boolean; onClick: () => void;
}) {
  const label = playerLabel(players, seat);
  const modelLabel = useModelLabel();
  const model = players.find((p) => p.seat === seat)?.model;
  return (
    <button type="button" className="your-turn__player" data-seat={seat} aria-pressed={pressed} onClick={onClick}>
      {model ? <ModelAvatar model={model} label={modelLabel(model)} size={24} alive={players.find((p) => p.seat === seat)?.alive} /> : <span className="your-turn__avatar" aria-hidden="true">{label.slice(0, 1)}</span>} {label}
    </button>
  );
}

/** Same-site link to a page, keeping the ?base= / ?index= data source. */
function pageHref(page: string) {
  const q = new URLSearchParams(window.location.search);
  const next = new URLSearchParams({ page });
  for (const k of ["base", "index"]) if (q.has(k)) next.set(k, q.get(k)!);
  return `?${next}`;
}

/** The game is over, or the server no longer has it (pruned, restarted): offer a new one. */
function Ended() {
  return (
    <p role="status" className="your-turn__ended">
      This game has ended. <a className="your-turn__new" href={pageHref("modes")}>Start a new game</a>{" "}
      <a href={pageHref("games")}>Watch replays</a>
    </p>
  );
}

export function YourTurnPanel({ seat }: { seat: Seat }) {
  const { me, pending } = seat;
  const [draft, setDraft] = useState<Draft>({});
  const [sent, setSent] = useState(false);
  // A new prompt (kind or deadline) starts with a clean form.
  const key = pending ? `${pending.kind}:${pending.deadline}` : "";
  useEffect(() => { setDraft({}); if (key) setSent(false); }, [key]);

  if (seat.gone) return <section className="your-turn"><Ended /></section>;
  if (!me) return <section className="your-turn">{seat.error ?? "Loading your seat..."}</section>;
  const set = (patch: Partial<Draft>) => setDraft((d) => ({ ...d, ...patch }));
  const needsText = pending?.kind === "statement" || pending?.kind === "wolf_chat";
  const players = me.players;
  const toggleAccuse = (s: string) => {
    const now = draft.accuse ?? [];
    set({ accuse: now.includes(s) ? now.filter((a) => a !== s) : [...now, s] });
  };
  // Ends the game as a forfeit, so a new one can start; the next poll shows it ended.
  const leave = async () => {
    if (!window.confirm("Leave this game? It ends as a forfeit.")) return;
    await leaveGame(me.game_id).catch(() => undefined);
    await seat.refresh();
  };
  const rate = (s: string, level: Level) =>
    set({ suspicion: { ...draft.suspicion, [s]: draft.suspicion?.[s] === level ? undefined : level } });
  const icon = roleArt(me.role);

  return (
    <section className="your-turn">
      <div className="your-turn__role" data-role={me.role}>
        <div className="your-turn__role-header">
          {icon && <img src={icon} alt="" className="your-turn__role-icon" />}
          <div>
            <h3>
              {me.name} <small style={{display:"block"}}>Your role: {me.role}</small>
            </h3>
            <p>
              Day {me.day}, {me.phase}. {me.alive ? "You are alive." : "You are out."}
            </p>
          </div>
        </div>
        {me.status === "running" && (
          <button type="button" className="your-turn__abstain your-turn__leave" onClick={() => void leave()}>
            Leave game
          </button>
        )}
        {me.status !== "running" && (
          <>
            <p>
              Game {me.status}
              {me.winner ? `: ${me.winner} win` : ""}.
            </p>
            <Ended />
          </>
        )}
      </div>
      {me.observation && (
        <div className="your-turn__dispatch">
          <pre className="your-turn__private">{me.observation}</pre>
        </div>
      )}
      {seat.error && <p role="alert">{seat.error}</p>}

      {sent && !pending && <p role="status" className="your-turn__sent">Move sent. Waiting for the village…</p>}
      {pending && (
        <form
          className="your-turn__form"
          onSubmit={(e) => {
            e.preventDefault();
            void seat.submit(draft).then(accepted => { if (accepted) setSent(true); });
          }}
        >
          <div className="your-turn__move-row">
            <p className="your-turn__move-lead">
              Your move: {MOVE[pending.kind]}
              {seat.secondsLeft !== null && (
                <span className={`your-turn__clock ${seat.secondsLeft <= 15 ? "your-turn__clock--urgent" : ""}`} aria-live="off">
                  <span aria-hidden="true">⏱</span> ({seat.secondsLeft}s left)
                </span>
              )}
            </p>
          </div>
          <small>Auto-move if time runs out. Each turn allows 90 seconds.</small>
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
            <fieldset className="your-turn__pick">
              <legend>{PICK_LABEL[pending.kind]}</legend>
              {pending.targets
                .filter((t) => t !== ABSTAIN)
                .map((t) => (
                  <PlayerButton key={t} players={players} seat={t} pressed={draft.target === t} onClick={() => set({ target: t })} />
                ))}
              {pending.targets.includes(ABSTAIN) && (
                <button type="button" className="your-turn__abstain" aria-pressed={draft.target === ABSTAIN} onClick={() => set({ target: ABSTAIN })}>
                  Abstain (vote for nobody)
                </button>
              )}
            </fieldset>
          )}
          {pending.kind === "statement" && (
            <fieldset className="your-turn__pick">
              <legend>Accuse anyone? (optional)</legend>
              {pending.targets.map((t) => (
                <PlayerButton key={t} players={players} seat={t} pressed={draft.accuse?.includes(t) ?? false} onClick={() => toggleAccuse(t)} />
              ))}
            </fieldset>
          )}
          <details className="your-turn__suspicion">
            <summary>Rate the others (optional)</summary>
            <p>Skip any player you have no read on. Skipped players are recorded as not rated.</p>
            {pending.others.map((o) => (
              <fieldset key={o} data-seat={o}>
                <legend>{playerLabel(players, o)}</legend>
                {(Object.keys(LEVELS) as Level[]).map((level) => (
                  <button key={level} type="button" data-level={level} aria-pressed={draft.suspicion?.[o] === level} onClick={() => rate(o, level)}>
                    {level}
                  </button>
                ))}
              </fieldset>
            ))}
          </details>
          {seat.errors.length > 0 && (
            <ul role="alert">
              {seat.errors.map((m) => (
                <li key={m}>{m}</li>
              ))}
            </ul>
          )}
          <button type="submit" disabled={seat.busy || seat.secondsLeft === 0}>
            {seat.busy ? "Sending..." : "Submit"}
          </button>
        </form>
      )}
    </section>
  );
}
