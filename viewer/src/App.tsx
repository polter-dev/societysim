import { useEffect, useRef, useState } from 'react';
import { EndReveal } from './play/EndReveal';
import { useNarration } from './audio/useNarration';
import { Arrows } from './components/Arrows/Arrows';
import { ChatLog } from './components/ChatLog/ChatLog';
import { LabelPopups } from './components/Labels/LabelPopups';
import { NarrationControls } from './components/NarrationControls/NarrationControls';
import { StepControls, type StepControlsProps } from './components/StepControls/StepControls';
import { ModeToggle } from './components/ModeToggle/ModeToggle';
import { modeFromSearch, urlWithMode } from './components/ModeToggle/modeUrl';
import { Speech } from './components/Speech/Speech';
import { Table } from './components/Table/Table';
import { Tally } from './components/Tally/Tally';
import Timeline, { type TimelineProps } from './components/Timeline/Timeline';
import { fetchLabels, siblingLabelsUrl } from './data/labels';
import { parseLog } from './data/parse';
import { fetchLog, type GameSource, type LoadedGame } from './data/source';
import { LiveBadge } from './components/LiveBadge/LiveBadge';
import { useLiveGame } from './live/useLiveGame';
import { deriveScene, type Mode } from './scene/deriveScene';
import { clearStepUrl, normalizeStepUrl } from './state/replayUrl';
import { eventIdx } from './state/stepper';
import { siblingAudioIndexUrl } from './audio/narration';
import { useStepper } from './state/useStepper';
import { SpotWolfPanel } from './spotwolf/SpotWolf';
import type { Event, Label } from './types/events';
import { IllustratedShell, IllustratedInspector } from './illustrated/IllustratedView';

type NarrationUi = Pick<ReturnType<typeof useNarration>, 'available' | 'on' | 'enabled'> & {
  onToggle: (on: boolean) => void; onCategory: Parameters<typeof NarrationControls>[0]['onCategory'];
};
export interface GameProps {
  events: Event[]; labels: Label[]; idx: number; mode: Mode; selected: string | null;
  onSelect: (seat: string | null) => void; onSeqChange?: (seq: number, index: number) => void;
  /** Stepper wiring. With `onSeek` the timeline is controlled by the stepper (`seq` is the shown event). */
  step?: StepControlsProps; seq?: number | null; onSeek?: (seq: number) => void; onStep?: TimelineProps['onStep']; narration?: NarrationUi;
}
export function Game({events, labels, idx, mode, selected, onSelect, onSeqChange, step, seq, onSeek, onStep, narration}: GameProps) {
  const scene = deriveScene(events, idx, mode);
  const [playing, setPlaying] = useState(false); // autoplay: chat stays out of the live regions
  return <>
    <div className="replay-table"><LabelPopups scene={scene} labels={labels} mode={mode} playing={playing}><Table scene={scene} selected={selected} onSelect={onSelect}/><Arrows scene={scene}/><Speech scene={scene} playing={playing}/></LabelPopups></div>
    <IllustratedInspector scene={scene} mode={mode} selected={selected} close={() => onSelect(null)}/>
    <EndReveal events={events} labels={labels} idx={idx} mode={mode}/>
    {/* The bottom bar: stepping and the timeline, then pop-ups that open upward over the table. */}
    <div className="replay-bar">
      <div className="replay-controls">{step?.live && <LiveBadge newCount={step.newCount}/>}{step && <StepControls {...step}/>}<Timeline events={events} labels={labels} mode={mode} onSeqChange={onSeqChange} seq={seq} onSeek={onSeek} onStep={onStep} onPlayingChange={setPlaying}/></div>
      <div className="replay-pops">
        <details className="replay-pop replay-chat" name="replay-pop"><summary>Discussion and votes</summary><div className="replay-pop__body"><Tally scene={scene}/><ChatLog scene={scene}/></div></details>
        <details className="replay-pop replay-spotwolf" name="replay-pop"><summary>Spot the wolf</summary><div className="replay-pop__body"><SpotWolfPanel key={events[0]?.game_id} events={events} idx={idx}/></div></details>
        {narration && <details className="replay-pop replay-narration" name="replay-pop"><summary>Narration</summary><div className="replay-pop__body"><NarrationControls available={narration.available} mode={mode} on={narration.on} enabled={narration.enabled} onToggle={narration.onToggle} onCategory={narration.onCategory}/></div></details>}
      </div>
    </div>
    {/* Fixed overlay, so its place here only sets focus order: table and controls first, then the long Agents and Game events panels. */}
    <IllustratedShell scene={scene} onSelect={onSelect}/>
  </>;
}
/** Owns step state (?step=, arrow keys) and narration for one loaded game; deriveScene stays the source of the scene. */
export function Replay({events, labels, mode, selected, onSelect, audioUrl = null, live = false, atEnd = false}: Omit<GameProps, 'idx'> & {audioUrl?: string | null; live?: boolean; atEnd?: boolean}) {
  // before useStepper reads the URL: ?seq= highlight links become ?step=. A watched-live game opens at its latest beat.
  useState(() => {if(!(live || atEnd) || new URLSearchParams(window.location.search).has('seq')) normalizeStepUrl(events[0].seq);});
  const st = useStepper(events, live); // live: polled events append; a stepped-back reader stays put
  const beat = st.state.beats[st.state.pos];
  const n = useNarration(audioUrl, beat ? {first: beat.firstSeq, last: beat.lastSeq} : null, mode);
  const idx = eventIdx(st.state);
  const step: StepControlsProps = {pos: st.state.pos, count: st.state.beats.length, live: st.state.live, atLatest: st.atLatest, newCount: st.newCount,
    onPrev: () => st.dispatch({type: 'prev'}), onNext: () => st.dispatch({type: 'next'}), onLive: st.goLive};
  return <Game events={events} labels={labels} idx={idx} mode={mode} selected={selected} onSelect={onSelect} step={step} seq={events[idx]?.seq ?? null}
    onSeek={seq => st.dispatch({type: 'goto', seq})} onStep={type => st.dispatch({type})} narration={{available: n.available, on: n.on, enabled: n.enabled, onToggle: n.setOn, onCategory: n.setCategory}}/>;
}
export default function App({source}: {source?: GameSource} = {}) {
 const [log, setLog] = useState<LoadedGame | null>(null);
 const [labels, setLabels] = useState<Label[]>([]);
 const [loadError, setLoadError] = useState<string | null>(null);
 const [audioUrl, setAudioUrl] = useState<string | null>(null);
 const [loadId, setLoadId] = useState(0);
 const [mode, setMode] = useState<Mode>(() => modeFromSearch(window.location.search));
 const [selected, setSelected] = useState<string | null>(null);
 const loadVersion = useRef(0);
 const show = (parsed: LoadedGame, audio: string | null = null) => {setLog(parsed);setAudioUrl(audio);setLoadId(n => n + 1);setSelected(null);setLoadError(null);};
 useEffect(() => {
   let active = true;
   const version = ++loadVersion.current;
   const current = () => active && loadVersion.current === version;
   const params = new URLSearchParams(location.search);
   const game = params.get('game'); const url = params.get('log');
   const request = game && source ? source.loadGame(game) : url ? fetchLog(url) : null;
   const audioRequest = game && source?.audioIndexUrl ? source.audioIndexUrl(game).catch(() => null) : Promise.resolve(url ? siblingAudioIndexUrl(url) : null);
   request?.then(async parsed => {const audio = await audioRequest;if(current()) show(parsed, audio);}, err => {if(current()) setLoadError(String(err.message ?? err));});
   const labelsUrl = params.get('labels') ?? (url && siblingLabelsUrl(url));
   const labelRequest = game && source ? source.loadLabels?.(game) : labelsUrl ? fetchLabels(labelsUrl) : null;
   labelRequest?.then(value => {if(current()) setLabels(value as Label[]);}, () => {if(current()) setLabels([]);});
   return () => {active = false;};
 }, [source]);
 useEffect(() => {const sync = () => setMode(modeFromSearch(window.location.search));window.addEventListener('popstate',sync);return () => window.removeEventListener('popstate',sync);},[]);
 useLiveGame(source, log, setLog);
 useEffect(() => {
   if(!log?.watchedLive || log.live || !source?.loadLabels || !log.events[0]?.game_id) return;
   let active = true;
   source.loadLabels(log.events[0]?.game_id!).then(value => {if(active) setLabels(value as Label[]);}, () => {});
   return () => {active = false;};
 }, [log?.watchedLive, log?.live, source]);
 const changeMode = (next: Mode) => {window.history.replaceState(window.history.state,'',urlWithMode(window.location.href,next));setMode(next);};
 const onFile = async (file?: File) => {if(!file) return;const version = ++loadVersion.current;try {setLabels([]);const parsed = parseLog(await file.text());if(loadVersion.current === version) {clearStepUrl();show(parsed);}} catch(err) {setLoadError(err instanceof Error ? err.message : String(err));}};
 const events = log?.events ?? [];
 const gameLabels = labels.filter(l => l.game_id === events[0]?.game_id);
 return <main className="replay-app"><div className="replay-tools"><h1>SocietySim viewer</h1><p><label className="file-button">Open log<input type="file" className="visually-hidden" accept=".jsonl" aria-label="Open a game log (.jsonl)" onChange={e => onFile(e.target.files?.[0])}/></label>{log && <ModeToggle mode={mode} onChange={changeMode}/>}</p>
 {loadError && <p role="alert">Could not load log: {loadError}</p>}
 {log && log.errors.length > 0 && <details open><summary role="alert">{log.errors.length} bad line(s) skipped</summary><ul>{log.errors.map(b => <li key={b.line}>line {b.line}: {b.error}</li>)}</ul></details>}
 {!log && (new URLSearchParams(window.location.search).has('game') && !loadError ? <p role="status">Loading the game…</p> : <p>Open a recorded game log or choose a game from Games.</p>)}{log && !events.length && <p role="status">This file has no valid game events.</p>}</div>
 {log?.finishing && <p className="live-finishing" role="status">Finishing up…</p>}{events.length > 0 && <Replay key={`${loadId}${log?.partial ? 'p' : ''}`} live={!!log?.live} atEnd={!!log?.watchedLive} events={events} labels={gameLabels} mode={mode} selected={selected} onSelect={setSelected} audioUrl={audioUrl}/>}</main>;
}
