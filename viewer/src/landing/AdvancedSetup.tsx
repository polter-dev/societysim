import { useEffect, useRef, useState } from 'react';
import { defaultState, HUMAN, humanSeat, loadSetupAssets, MAX_PLAYERS, MIN_PLAYERS, maxWolves, modelLabel, restrictCatalog, schemaErrors, seatId, setHumanSeat, setNPlayers, toSetupJson, validate, type SetupAssets, type SetupState } from '../settings/setup';
import { getLimits, PlayError, startGame } from '../play/api';
import { allowedModels } from '../play/allowed';
import { playEnabled } from '../play/config';
import { getTurnstileToken } from '../play/turnstile';
import type { Limits } from '../play/types';
import back from './assets/modes/back.png';
import rules from './assets/advanced/rules.png';
import ai from './assets/advanced/ai.png';
import start from './assets/advanced/start.png';
import './landing.css';
import './mode-selection.css';
import './advanced-setup.css';

const emptyCatalog = { human: HUMAN, default_lineup: [], models: [] };

export default function AdvancedSetup({ catalogUrl, schemaUrl, href, go = (url: string) => window.location.assign(url) }: {
  catalogUrl: string; schemaUrl: string; go?: (url: string) => void; href: (extra: Record<string, string>) => string;
}) {
  const [assets, setAssets] = useState<SetupAssets | null>(null);
  const [assetError, setAssetError] = useState<string | null>(null);
  const [state, setState] = useState<SetupState>(() => setHumanSeat({ ...defaultState(emptyCatalog), mode: 'advanced' }, 0, emptyCatalog));
  const [limits, setLimits] = useState<Limits | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [paused, setPaused] = useState(false);
  const widget = useRef<HTMLDivElement>(null);
  const starting = useRef(false);
  const on = playEnabled();

  useEffect(() => {
    let active = true;
    setAssetError(null);
    loadSetupAssets(catalogUrl, schemaUrl).then(next => {
      if (!active) return;
      setAssets(next);
      const defaults = defaultState(next.catalog).seats;
      setState(s => ({ ...s, seats: s.seats.map((model,i) => model === HUMAN ? HUMAN : defaults[i]) }));
    }, () => { if (active) setAssetError('Could not load the model catalog. Check the published batch URL and reload.'); });
    return () => { active = false; };
  }, [catalogUrl, schemaUrl]);

  useEffect(() => {
    if (!on) return;
    let active = true;
    getLimits().then(next => { if (active) setLimits(next); }, () => { if (active) setError('Could not check game availability. Reload to try again.'); });
    const check = getTurnstileToken(widget.current!, undefined, value => {if(active) setToken(value);});
    check.token.then(value => { if (active) setToken(value); }, (e: Error) => { if (active && e.message !== 'cancelled') setError('Could not complete the human verification. Reload to try again.'); });
    return () => { active = false; check.cancel(); };
  }, [on]);

  const allowed = allowedModels(limits);
  const rawCatalog = assets?.catalog ?? emptyCatalog;
  const restricted = restrictCatalog(rawCatalog, allowed);
  const catalog = { ...restricted, models: rawCatalog.models.map(m => allowed && !allowed.includes(m.name) ? { ...m, available:false, unavailable_reason:'Not available for visitor games' } : m) };
  const allowedKey = allowed?.join('\n') ?? '';
  useEffect(() => {
    if (!assets || !allowed) return;
    const defaults = defaultState(restrictCatalog(assets.catalog, allowed)).seats;
    setState(s => ({ ...s, seats:s.seats.map((m,i) => m === HUMAN || allowed.includes(m) ? m : defaults[i]) }));
  }, [assets, allowedKey]);
  // No seed field: the play server draws its own seed so the role deal stays secret.
  const setup = toSetupJson(state);
  const errors = assets ? [...validate(state, catalog), ...schemaErrors(setup, assets.schema)] : [];
  const canStart = on && assets !== null && errors.length === 0 && token !== null && limits !== null && limits.play_enabled === true && Number.isInteger(limits.remaining_today) && limits.remaining_today > 0 && !busy;
  const human = humanSeat(state);
  const defaults = defaultState(catalog).seats;
  const models = state.seats.slice(0,state.nPlayers).filter(m => m !== HUMAN);
  const defaultLineup = state.seats.slice(0,state.nPlayers).every((model,i) => model === HUMAN || model === defaults[i]);
  const selectedModel = defaultLineup ? '__default' : models.every(m => m === models[0]) ? models[0] : '__custom';
  const update = (patch: Partial<SetupState>) => setState(s => ({ ...s, ...patch }));

  const submit = async () => {
    if (!canStart || starting.current) return;
    starting.current = true;
    setBusy(true); setError(null);
    try {
      const game = await startGame({ setup, turnstile_token: token!, watch_only: human === null });
      go(href({game:game.game_id}));
    } catch (e) { setError(e instanceof PlayError ? e.message : 'Could not start the simulation. Try again.'); }
    finally { starting.current = false; setBusy(false); }
  };

  return <main className={`landing-page advanced-screen ${paused ? 'landing-paused' : ''}`}>
    <div className="landing-background" aria-hidden="true" />
    <div className="landing-fireflies" aria-hidden="true">{Array.from({length:28},(_,i) => <i key={i} style={{left:`${(i*37+11)%100}%`,top:`${(i*23+9)%100}%`,animationDelay:`-${i*.8}s`}} />)}</div>
    <a className="mode-back" href={href({page:'modes'})} aria-label="Back to game modes"><img src={back} alt="" /></a>
    <section className="advanced-panel" aria-labelledby="advanced-title"><div className="advanced-panel-content">
      <header className="advanced-title"><h1 id="advanced-title">Advanced Setup</h1><p>Customize your simulation</p></header>
      <form onSubmit={e => { e.preventDefault(); void submit(); }}>
        <div className="advanced-columns">
          <fieldset><legend><img src={rules} alt="Game rules" /></legend>
            <label className="advanced-row"><span>Seats</span><select aria-label="Seats" value={state.nPlayers} onChange={e => setState(s => setNPlayers(s, Number(e.target.value), catalog))}>{Array.from({length:MAX_PLAYERS-MIN_PLAYERS+1},(_,i) => <option key={i} value={MIN_PLAYERS+i}>{MIN_PLAYERS+i}</option>)}</select></label>
            <label className="advanced-row"><span>Werewolves</span><select aria-label="Werewolves" value={state.nWolves} onChange={e => update({nWolves:Number(e.target.value)})}>{Array.from({length:maxWolves(state.nPlayers)},(_,i) => <option key={i} value={i+1}>{i+1}</option>)}</select></label>
            <label className="advanced-row"><span>Seer</span><input className="advanced-toggle" type="checkbox" role="switch" aria-label="Seer" checked={state.seer} onChange={e => update({seer:e.target.checked})} /></label>
            <label className="advanced-row"><span>Doctor</span><input className="advanced-toggle" type="checkbox" role="switch" aria-label="Doctor" checked={state.doctor} onChange={e => update({doctor:e.target.checked})} /></label>
          </fieldset>
          <fieldset><legend><img src={ai} alt="AI configuration" /></legend>
            <label className="advanced-row"><span>Model selection</span><select aria-label="Model selection" disabled={!assets} value={selectedModel} onChange={e => {
              const model = e.target.value;
              setState(s => ({ ...s, seats:s.seats.map((m,i) => m === HUMAN ? HUMAN : model === '__default' ? defaults[i] : model) }));
            }}><option value="__default">{assets ? 'Default lineup' : 'Loading models…'}</option><option value="__custom" disabled>Custom lineup</option>{catalog.models.map(m => <option key={m.name} value={m.name} disabled={!m.available} title={m.unavailable_reason ?? undefined}>{modelLabel(m)}{m.available ? '' : ' (unavailable)'}</option>)}</select></label>
            <label className="advanced-row"><span>Your seat</span><select aria-label="Your seat" value={human ?? ''} onChange={e => setState(s => setHumanSeat(s,e.target.value ? Number(e.target.value.slice(1)) : null,catalog))}><option value="">None — watch</option>{Array.from({length:state.nPlayers},(_,i) => <option key={i} value={seatId(i)}>Seat {i+1}</option>)}</select></label>
            {assets && <details className="advanced-lineup"><summary>Customize each AI seat</summary><div>{state.seats.slice(0,state.nPlayers).map((model,i) => model === HUMAN ? <p key={i}>Seat {i+1} · You</p> : <label key={i}>Seat {i+1}<select aria-label={`Model for seat ${i+1}`} value={model} onChange={e => setState(s => ({ ...s, seats:s.seats.map((m,k) => k === i ? e.target.value : m) }))}>{catalog.models.map(m => <option key={m.name} value={m.name} disabled={!m.available}>{modelLabel(m)}{m.available ? '' : ' (unavailable)'}</option>)}</select></label>)}</div></details>}
          </fieldset>
        </div>
        <div className="advanced-feedback" aria-live="polite">
          {assetError && <p role="alert">{assetError}</p>}
          {!assets && !assetError && <p>Loading the model catalog…</p>}
          {errors.length > 0 && <ul role="alert">{errors.map((message,i) => <li key={i}>{message}</li>)}</ul>}
          {!on && <p>Live play is coming soon. <a href={href({page:'games'})}>Watch recorded games</a> in the meantime.</p>}
          {on && limits && <p>{limits.play_enabled ? `${limits.remaining_today} games left today` : 'Play is paused right now.'}</p>}
          {busy && <p role="status">Waking the server… This can take 30–60 seconds.</p>}
          {error && <p role="alert">{error}</p>}
        </div>
        <div className="advanced-actions"><div ref={widget} className="advanced-verification" /><button className="advanced-start" type="submit" disabled={!canStart} aria-label="Start simulation" aria-busy={busy}><img src={start} alt="" /></button></div>
      </form>
    </div></section>
    <button className="landing-motion" aria-pressed={paused} onClick={() => setPaused(!paused)}>{paused ? 'Resume ambience' : 'Pause ambience'}</button>
  </main>;
}
