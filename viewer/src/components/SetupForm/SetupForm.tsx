// Game setup form (styled in controls.css). Mount it anywhere with the loaded assets; see the PR's port notes.
import { useEffect } from "react";
import { avatarFor } from "../../data/models";
import { type GameSetupJson, type SetupAssets, HUMAN, modelLabel, MAX_PLAYERS, MIN_PLAYERS, PLAY_COMMAND, maxWolves, seatId } from "../../settings/setup";
import { useSetupForm } from "../../settings/useSetupForm";
import { ModelAvatar } from "../ModelAvatar/ModelAvatar";
import { ThemedSelect } from "../ThemedSelect/ThemedSelect";

export function SetupForm({
  assets,
  onChange,
  play,
  allowed,
}: {
  assets: SetupAssets;
  /** Called on mount and after every edit with the current setup and whether it is valid. */
  onChange?: (setup: GameSetupJson, valid: boolean) => void;
  /** Visitor play: true = you play, false = you watch. Drives the human seat, hides seed and developer output. */
  play?: boolean;
  /** Visitor play: model ids the server allows (/api/limits). Remount (key) when this changes. */
  allowed?: string[] | null;
}) {
  const f = useSetupForm(assets, { play, allowed });
  const visitor = play !== undefined;
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
  const modelOptions = f.catalog.models.map((c) => ({
    value: c.name,
    label: modelLabel(c) + (c.available ? "" : " (unavailable)"),
    disabled: !c.available,
    title: c.unavailable_reason ?? undefined,
    icon: avatarFor(c.name),
  }));
  const seatOptions = [
    ...(visitor ? [] : [{ value: "", label: "none" }]),
    ...Array.from({ length: s.nPlayers }, (_, i) => ({ value: String(i), label: seatId(i) })),
  ];

  const dev = (
    <div className="setup-dev">
      <pre className="setup-json">{f.json}</pre>
      <button type="button" className="gold-button" disabled={!f.valid} onClick={download}>
        Download setup.json
      </button>
      <code className="setup-command">{PLAY_COMMAND}</code>
      <button type="button" className="gold-button" onClick={copy}>
        Copy command
      </button>
    </div>
  );
  const slider = (label: string, value: number, min: number, max: number, set: (n: number) => void) => (
    <label className="setup-slider">
      <span className="setup-label">{label}</span>
      <input type="range" min={min} max={max} step={1} value={value} onChange={(e) => set(Number(e.target.value))} />
      <output>{value}</output>
    </label>
  );
  const check = (label: string, checked: boolean, set: (v: boolean) => void) => (
    <label className="setup-check">
      <input type="checkbox" checked={checked} onChange={(e) => set(e.target.checked)} /> {label}
    </label>
  );

  return (
    <form className="setup-form" onSubmit={(e) => e.preventDefault()}>
      <fieldset className="setup-mode">
        <legend>Mode</legend>
        {(["basic", "advanced"] as const).map((m) => (
          <label key={m}>
            <input type="radio" name="mode" checked={s.mode === m} onChange={() => f.setMode(m)} /> {m}
          </label>
        ))}
      </fieldset>

      {!advanced && !visitor && check("I play (seat p0, 1 human and 9 models)", s.basicHuman, (v) => f.update({ basicHuman: v }))}

      {advanced && (
        <>
          <div className="setup-row">
            {slider("Seats", s.nPlayers, MIN_PLAYERS, MAX_PLAYERS, (n) => f.update({ nPlayers: n }))}
            {slider("Wolves", s.nWolves, 1, maxWolves(s.nPlayers), (n) => f.update({ nWolves: n }))}
          </div>
          <div className="setup-row">
            {check("Seer", s.seer, (v) => f.update({ seer: v }))}
            {check("Doctor", s.doctor, (v) => f.update({ doctor: v }))}
            {(!visitor || play) && (
              <label className="setup-field">
                <span className="setup-label">Human seat</span>
                <ThemedSelect
                  className="themed-select--field"
                  label="Human seat"
                  value={humanIndex < 0 ? "" : String(humanIndex)}
                  options={seatOptions}
                  onChange={(v) => f.setHuman(v === "" ? null : Number(v))}
                />
              </label>
            )}
          </div>
          <ol className="setup-seats" aria-label="Seats">
            {s.seats.slice(0, s.nPlayers).map((m, i) => (
              <li key={i}>
                <span className="setup-seat-id">{seatId(i)}</span>
                <span aria-hidden="true">
                  <ModelAvatar model={m} size={36} />
                </span>
                {m === HUMAN ? (
                  <span className="setup-human">Human</span>
                ) : (
                  <ThemedSelect
                    className="themed-select--field"
                    label={`Model for ${seatId(i)}`}
                    value={m}
                    options={modelOptions}
                    onChange={(v) => f.setSeatModel(i, v)}
                  />
                )}
              </li>
            ))}
          </ol>
        </>
      )}

      {!visitor && (
        <label className="setup-field">
          <span className="setup-label">Seed</span>
          <input type="number" value={s.seed} onChange={(e) => f.update({ seed: Math.trunc(Number(e.target.value)) })} />
        </label>
      )}

      {f.errors.length > 0 && (
        <ul role="alert" className="setup-errors">
          {f.errors.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      )}

      {visitor ? (
        <details>
          <summary>Developer</summary>
          {dev}
        </details>
      ) : (
        dev
      )}
    </form>
  );
}
