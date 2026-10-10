// Unstyled game setup form. Mount it anywhere with the loaded assets; see the PR's port notes.
import { useEffect } from "react";
import { type GameSetupJson, type SetupAssets, HUMAN, MAX_PLAYERS, MIN_PLAYERS, PLAY_COMMAND, maxWolves, seatId } from "../../settings/setup";
import { useSetupForm } from "../../settings/useSetupForm";

export function SetupForm({
  assets,
  onChange,
}: {
  assets: SetupAssets;
  /** Called on mount and after every edit with the current setup and whether it is valid. */
  onChange?: (setup: GameSetupJson, valid: boolean) => void;
}) {
  const f = useSetupForm(assets);
  // Fire on content change only, not when the parent passes a new callback.
  useEffect(() => onChange?.(f.setup, f.valid), [f.json, f.valid]);
  const { state: s } = f;
  const advanced = s.mode === "advanced";
  const humanIndex = s.seats.slice(0, s.nPlayers).indexOf(HUMAN);
  const download = () => {
    const url = URL.createObjectURL(new Blob([f.json + "\n"], { type: "application/json" }));
    const a = Object.assign(document.createElement("a"), { href: url, download: "setup.json" });
    a.click();
    URL.revokeObjectURL(url);
  };
  const copy = () => navigator.clipboard?.writeText(PLAY_COMMAND);

  return (
    <form className="setup-form" onSubmit={(e) => e.preventDefault()}>
      <fieldset>
        <legend>Mode</legend>
        {(["basic", "advanced"] as const).map((m) => (
          <label key={m}>
            <input type="radio" name="mode" checked={s.mode === m} onChange={() => f.setMode(m)} /> {m}
          </label>
        ))}
      </fieldset>

      {!advanced && (
        <label>
          <input type="checkbox" checked={s.basicHuman} onChange={(e) => f.update({ basicHuman: e.target.checked })} />{" "}
          I play (seat p0, 1 human and 9 models)
        </label>
      )}

      {advanced && (
        <>
          <label>
            Seats{" "}
            <input
              type="number"
              min={MIN_PLAYERS}
              max={MAX_PLAYERS}
              value={s.nPlayers}
              onChange={(e) => f.update({ nPlayers: Number(e.target.value) })}
            />
          </label>
          <label>
            Wolves{" "}
            <input
              type="number"
              min={1}
              max={maxWolves(s.nPlayers)}
              value={s.nWolves}
              onChange={(e) => f.update({ nWolves: Number(e.target.value) })}
            />
          </label>
          <label>
            <input type="checkbox" checked={s.seer} onChange={(e) => f.update({ seer: e.target.checked })} /> Seer
          </label>
          <label>
            <input type="checkbox" checked={s.doctor} onChange={(e) => f.update({ doctor: e.target.checked })} /> Doctor
          </label>
          <label>
            Human seat{" "}
            <select
              value={humanIndex < 0 ? "" : String(humanIndex)}
              onChange={(e) => f.setHuman(e.target.value === "" ? null : Number(e.target.value))}
            >
              <option value="">none</option>
              {Array.from({ length: s.nPlayers }, (_, i) => (
                <option key={i} value={i}>
                  {seatId(i)}
                </option>
              ))}
            </select>
          </label>
          <ol className="setup-seats">
            {s.seats.slice(0, s.nPlayers).map((m, i) => (
              <li key={i}>
                <label>
                  {seatId(i)}{" "}
                  {m === HUMAN ? (
                    <span>human</span>
                  ) : (
                    <select value={m} onChange={(e) => f.setSeatModel(i, e.target.value)}>
                      {assets.catalog.models.map((c) => (
                        <option key={c.name} value={c.name} disabled={!c.available} title={c.unavailable_reason ?? undefined}>
                          {c.name}
                          {c.available ? "" : " (unavailable)"}
                        </option>
                      ))}
                    </select>
                  )}
                </label>
              </li>
            ))}
          </ol>
        </>
      )}

      <label>
        Seed{" "}
        <input type="number" value={s.seed} onChange={(e) => f.update({ seed: Math.trunc(Number(e.target.value)) })} />
      </label>

      {f.errors.length > 0 && (
        <ul role="alert" className="setup-errors">
          {f.errors.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      )}

      <pre className="setup-json">{f.json}</pre>
      <button type="button" disabled={!f.valid} onClick={download}>
        Download setup.json
      </button>
      <code className="setup-command">{PLAY_COMMAND}</code>{" "}
      <button type="button" onClick={copy}>
        Copy command
      </button>
    </form>
  );
}
