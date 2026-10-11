// Unstyled start screen for visitor play. Basic (you play) or Watch, or Advanced via SetupForm.
import { useEffect, useRef, useState } from "react";
import { allowedModels } from "../../play/allowed";
import { PlayError, getLimits, startGame } from "../../play/api";
import { getTurnstileToken } from "../../play/turnstile";
import type { Limits, StartedGame } from "../../play/types";
import type { GameSetupJson, SetupAssets } from "../../settings/setup";
import { SetupForm } from "../SetupForm/SetupForm";

export function PlayStart({
  onStarted,
  assets,
  initialMode = "basic",
  modeLocked = false,
}: {
  onStarted: (game: StartedGame, watchOnly: boolean) => void;
  /** Model catalog and setup schema; without them there is no Advanced option. */
  assets?: SetupAssets;
  initialMode?: "basic" | "watch" | "advanced";
  modeLocked?: boolean;
}) {
  const [adv, setAdv] = useState<{ setup: GameSetupJson; valid: boolean } | null>(null);
  const [watch, setWatch] = useState(initialMode === "watch");
  const [useAdvanced, setUseAdvanced] = useState(initialMode === "advanced");
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
    const t = getTurnstileToken(widget.current, undefined, setToken);
    t.token.then(setToken, (e: Error) => e.message !== "cancelled" && setError(e.message));
    return t.cancel;
  }, []);

  const allowed = allowedModels(limits);
  const paused = limits !== null && !limits.play_enabled;
  const none = limits !== null && limits.remaining_today <= 0;
  const setup: GameSetupJson = useAdvanced && adv ? adv.setup : { mode: "basic", seed: 0 };
  const canStart = !busy && limits !== null && limits.play_enabled === true && Number.isInteger(limits.remaining_today) && limits.remaining_today > 0 && token !== null && (!useAdvanced || (adv !== null && adv.valid));

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
      {!modeLocked && <fieldset>
        <legend>How do you want to join?</legend>
        {[
          [false, "Play one seat"],
          [true, "Watch"],
        ].map(([w, label]) => (
          <label key={String(w)}>
            <input type="radio" name="play-mode" checked={watch === w} onChange={() => setWatch(w as boolean)} /> {label}
          </label>
        ))}
      </fieldset>}
      {(!modeLocked || initialMode === "advanced") && (assets || initialMode === "advanced") && (
        <label>
          <input type="checkbox" checked={useAdvanced} onChange={(e) => setUseAdvanced(e.target.checked)} /> Advanced
          settings
        </label>
      )}
      {useAdvanced && !assets && <p role="status">Waiting for the model catalog. Turn off Advanced settings to use the standard lineup.</p>}
      {useAdvanced && assets && (
        <SetupForm
          key={(allowed ?? []).join()}
          assets={assets}
          play={!watch}
          allowed={allowed}
          onChange={(setup, valid) => setAdv({ setup, valid })} />
      )}
      <div ref={widget} className="play-start__turnstile" />
      {limits && (
        <p className="play-start__limits">
          {paused ? "Play is paused right now." : `${limits.remaining_today} games left today.`}
        </p>
      )}
      {busy && <p className="play-start__status" role="status">Waking the server… A cold start can take 30–60 seconds.</p>}
      {!limits && !error && <p className="play-start__status" role="status">Waking the server… Checking availability can take 30–60 seconds.</p>}
      {limits && <p className="play-start__status">{paused ? "Play is paused." : none ? "The daily allowance or budget is used up." : "Play is open."}</p>}
      {error && <p role="alert">{error}</p>}
      <button type="button" disabled={!canStart} onClick={start}>
        {busy ? "Waking the server…" : "Start game"}
      </button>
    </section>
  );
}
