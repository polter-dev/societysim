// Unstyled start screen for visitor play. Basic (you play) or Watch; Advanced is supplied by the
// parent, which owns a useSetupForm and renders <SetupForm> in `advanced.node` (see port notes).
import { type ReactNode, useEffect, useRef, useState } from "react";
import { PlayError, getLimits, startGame } from "../../play/api";
import { getTurnstileToken } from "../../play/turnstile";
import type { Limits, StartedGame } from "../../play/types";
import type { GameSetupJson } from "../../settings/setup";

export interface AdvancedSetup {
  setup: GameSetupJson;
  valid: boolean;
  node: ReactNode;
}

export function PlayStart({
  onStarted,
  advanced,
}: {
  onStarted: (game: StartedGame, watchOnly: boolean) => void;
  advanced?: AdvancedSetup;
}) {
  const [watch, setWatch] = useState(false);
  const [useAdvanced, setUseAdvanced] = useState(false);
  const [limits, setLimits] = useState<Limits | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const widget = useRef<HTMLDivElement>(null);

  useEffect(() => {
    getLimits().then(setLimits, (e) => setError(e instanceof PlayError ? e.message : "Couldn't load limits."));
  }, []);

  useEffect(() => {
    if (!widget.current) return;
    const t = getTurnstileToken(widget.current);
    t.token.then(setToken, (e: Error) => e.message !== "cancelled" && setError(e.message));
    return t.cancel;
  }, []);

  const paused = limits !== null && !limits.play_enabled;
  const none = limits !== null && limits.remaining_today <= 0;
  const setup: GameSetupJson = useAdvanced && advanced ? advanced.setup : { mode: "basic", seed: 0 };
  const canStart = !busy && !paused && !none && token !== null && (!useAdvanced || !advanced || advanced.valid);

  const start = async () => {
    if (token === null) return;
    setBusy(true);
    setError(null);
    try {
      const game = await startGame({ setup, turnstile_token: token, watch_only: watch });
      onStarted(game, watch);
    } catch (e) {
      setError(e instanceof PlayError ? e.message : "Couldn't start the game.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="play-start">
      <fieldset>
        <legend>How do you want to join?</legend>
        {[
          [false, "Play (you are seat p0)"],
          [true, "Watch"],
        ].map(([w, label]) => (
          <label key={String(w)}>
            <input type="radio" name="play-mode" checked={watch === w} onChange={() => setWatch(w as boolean)} /> {label}
          </label>
        ))}
      </fieldset>
      {advanced && (
        <label>
          <input type="checkbox" checked={useAdvanced} onChange={(e) => setUseAdvanced(e.target.checked)} /> Advanced
          settings
        </label>
      )}
      {useAdvanced && advanced?.node}
      <div ref={widget} className="play-start__turnstile" />
      {limits && (
        <p className="play-start__limits">
          {paused ? "Play is paused right now." : `${limits.remaining_today} games left today.`}
        </p>
      )}
      {error && <p role="alert">{error}</p>}
      <button type="button" disabled={!canStart} onClick={start}>
        {busy ? "Starting..." : "Start game"}
      </button>
    </section>
  );
}
