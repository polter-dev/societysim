// Unstyled start screen for visitor play. Basic (you play) or Watch, or Advanced via SetupForm.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { allowedModels } from "../../play/allowed";
import { PlayError, getLimits, startGame } from "../../play/api";
import { useTurnstile } from "../../play/useTurnstile";
import type { Limits, StartedGame } from "../../play/types";
import type { GameSetupJson, SetupAssets } from "../../settings/setup";
import { SetupForm } from "../SetupForm/SetupForm";
import { art } from "../../illustrated/assets";

const seal = art("inspector/10_notes.png");
const night = art("inspector/06_night.png");

export function PlayStart({
  onStarted,
  assets,
  initialMode = "basic",
  modeLocked = false,
  autoStart = false,
}: {
  onStarted: (game: StartedGame, watchOnly: boolean) => void;
  /** Model catalog and setup schema; without them there is no Advanced option. */
  assets?: SetupAssets;
  initialMode?: "basic" | "watch" | "advanced";
  modeLocked?: boolean;
  /** The mode-picker click is the start action; wait for verification, then join. */
  autoStart?: boolean;
}) {
  const [adv, setAdv] = useState<{ setup: GameSetupJson; valid: boolean } | null>(null);
  const [watch, setWatch] = useState(initialMode === "watch");
  const [useAdvanced, setUseAdvanced] = useState(initialMode === "advanced");
  const [limits, setLimits] = useState<Limits | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const widget = useRef<HTMLDivElement>(null);
  const retryButton = useRef<HTMLButtonElement>(null);
  const attempted = useRef(false);
  const starting = useRef(false);
  const started = useRef(false);
  const { token, reset } = useTurnstile(widget, setError);

  const loadLimits = useCallback(() => {
    getLimits().then(setLimits, (e) => setError(e instanceof PlayError ? e.message : "Couldn't load limits."));
  }, []);
  useEffect(loadLimits, [loadLimits]);

  const allowed = allowedModels(limits);
  const paused = limits !== null && !limits.play_enabled;
  const none = limits !== null && limits.remaining_today <= 0;
  const setup = useMemo<GameSetupJson>(() => useAdvanced && adv ? adv.setup : { mode: "basic", seed: 0 }, [useAdvanced, adv]);
  const canStart = !busy && limits !== null && limits.play_enabled === true && Number.isInteger(limits.remaining_today) && limits.remaining_today > 0 && token !== null && (!useAdvanced || (adv !== null && adv.valid));

  const start = useCallback(async () => {
    if (!canStart || token === null || starting.current || started.current) return;
    starting.current = true;
    setBusy(true);
    setError(null);
    try {
      const game = await startGame({ setup, turnstile_token: token, watch_only: watch });
      started.current = true;
      onStarted(game, watch);
    } catch (e) {
      setError(e instanceof PlayError ? e.message : "Couldn't start the game.");
      reset(); // the token was spent on this attempt
    } finally {
      starting.current = false;
      if (!started.current) setBusy(false);
    }
  }, [canStart, token, setup, watch, onStarted, reset]);

  useEffect(() => {
    if (!autoStart || !canStart || error || attempted.current) return;
    attempted.current = true;
    void start();
  }, [autoStart, canStart, error, start]);

  // Keyboard users land on Try again as soon as an automatic start fails.
  useEffect(() => {
    if (autoStart && error) retryButton.current?.focus();
  }, [autoStart, error]);

  /** Re-arm the automatic start. A failed check or limits fetch is redone, so Try again never dead-ends. */
  const retry = () => {
    setError(null);
    attempted.current = false;
    if (limits === null) loadLimits();
    if (token === null) reset();
  };

  const isGate = autoStart || (modeLocked && initialMode !== "advanced");

  return (
    <section className={isGate ? "play-start play-start--gate" : "play-start"}>
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
      {isGate && <div className="play-arrival">
        {night && <img className="play-arrival__art" src={night} alt="" />}
        <h2>{error ? "A moment before we begin" : busy ? "The village is waking" : !limits ? "Opening the village gates" : !token ? "Your invitation awaits" : "Ready to enter"}</h2>
        <ol className="play-arrival__steps" aria-label="Joining progress">
          <li data-state={limits ? "done" : "current"}>Availability</li>
          <li data-state={token || busy ? "done" : limits ? "current" : "waiting"}>Bot check</li>
          <li data-state={busy ? "current" : "waiting"}>Enter the village</li>
        </ol>
      </div>}
      <div className={isGate ? "play-gate-seal" : undefined}>
        {isGate && seal && <img src={seal} alt="" />}
        <div ref={widget} className="play-start__turnstile" />
      </div>
      {isGate && limits && !busy && !error && !paused && !none && !token && <p className="play-start__status" role="status">Checking you in… Complete the bot check to join the village.</p>}
      {limits && (
        <p className="play-start__limits">
          {paused ? "Play is paused right now." : `${limits.remaining_today} games left today.`}
        </p>
      )}
      {busy && <p className="play-start__status" role="status">Waking the server… A cold start can take 30–60 seconds.</p>}
      {!limits && !error && <p className="play-start__status" role="status">Waking the server… Checking availability can take 30–60 seconds.</p>}
      {limits && (!autoStart || paused || none) && <p className="play-start__status">{paused ? "Play is paused." : none ? "The daily allowance or budget is used up." : "Play is open."}</p>}
      {error && <p role="alert">{error}</p>}
      {autoStart ? (
        error && <button ref={retryButton} type="button" disabled={busy} onClick={retry}>Try again</button>
      ) : (
        <button type="button" disabled={!canStart} onClick={start}>
          {busy ? "Waking the server…" : "Start game"}
        </button>
      )}
    </section>
  );
}
