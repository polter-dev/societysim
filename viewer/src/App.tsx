import { useCallback, useEffect, useRef, useState } from 'react';
import { Arrows } from './components/Arrows/Arrows';
import { ChatLog } from './components/ChatLog/ChatLog';
import { LabelPopups } from './components/Labels/LabelPopups';
import { ModeToggle } from './components/ModeToggle/ModeToggle';
import { modeFromSearch, urlWithMode } from './components/ModeToggle/modeUrl';
import { Speech } from './components/Speech/Speech';
import { Table } from './components/Table/Table';
import { Tally } from './components/Tally/Tally';
import Timeline from './components/Timeline/Timeline';
import { fetchLabels, siblingLabelsUrl } from './data/labels';
import { parseLog, type ParsedLog } from './data/parse';
import { fetchLog, type GameSource } from './data/source';
import { deriveScene, type Mode } from './scene/deriveScene';
import type { Event, Label } from './types/events';
import { IllustratedShell, IllustratedInspector } from './illustrated/IllustratedView';

export interface GameProps {
  events: Event[]; labels: Label[]; idx: number; mode: Mode; selected: string | null;
  onSelect: (seat: string | null) => void; onSeqChange: (seq: number, index: number) => void;
}
export function Game({events, labels, idx, mode, selected, onSelect, onSeqChange}: GameProps) {
  const scene = deriveScene(events, idx, mode);
  return <>
    <IllustratedShell scene={scene} onSelect={onSelect}/>
    <div className="replay-table"><LabelPopups scene={scene} labels={labels} mode={mode}><Table scene={scene} selected={selected} onSelect={onSelect}/><Arrows scene={scene}/><Speech scene={scene}/></LabelPopups></div>
    <details className="replay-chat"><summary>Discussion and vote tally</summary><Tally scene={scene}/><ChatLog scene={scene}/></details>
    <IllustratedInspector scene={scene} mode={mode} selected={selected} close={() => onSelect(null)}/>
    <div className="replay-controls"><Timeline events={events} labels={labels} mode={mode} onSeqChange={onSeqChange}/></div>
  </>;
}
export default function App({source}: {source?: GameSource} = {}) {
 const [log, setLog] = useState<ParsedLog | null>(null);
 const [labels, setLabels] = useState<Label[]>([]);
 const [loadError, setLoadError] = useState<string | null>(null);
 const [idx, setIdx] = useState(0);
 const [mode, setMode] = useState<Mode>(() => modeFromSearch(window.location.search));
 const [selected, setSelected] = useState<string | null>(null);
 const loadVersion = useRef(0);
 const show = (parsed: ParsedLog) => {setLog(parsed);setIdx(0);setSelected(null);setLoadError(null);};
 useEffect(() => {
   let active = true;
   const version = ++loadVersion.current;
   const current = () => active && loadVersion.current === version;
   const params = new URLSearchParams(location.search);
   const game = params.get('game'); const url = params.get('log');
   const request = game && source ? source.loadGame(game) : url ? fetchLog(url) : null;
   request?.then(parsed => {if(current()) show(parsed);}, err => {if(current()) setLoadError(String(err.message ?? err));});
   const labelsUrl = params.get('labels') ?? (url && siblingLabelsUrl(url));
   const labelRequest = game && source ? source.loadLabels?.(game) : labelsUrl ? fetchLabels(labelsUrl) : null;
   labelRequest?.then(value => {if(current()) setLabels(value as Label[]);}, () => {if(current()) setLabels([]);});
   return () => {active = false;};
 }, [source]);
 useEffect(() => {const sync = () => setMode(modeFromSearch(window.location.search));window.addEventListener('popstate',sync);return () => window.removeEventListener('popstate',sync);},[]);
 const onSeqChange = useCallback((_seq: number, index: number) => setIdx(index), []);
 const changeMode = (next: Mode) => {window.history.replaceState(window.history.state,'',urlWithMode(window.location.href,next));setMode(next);};
 const onFile = async (file?: File) => {if(!file) return;const version = ++loadVersion.current;try {setLabels([]);const parsed = parseLog(await file.text());if(loadVersion.current === version) show(parsed);} catch(err) {setLoadError(err instanceof Error ? err.message : String(err));}};
 const events = log?.events ?? [];
 const gameLabels = labels.filter(l => l.game_id === events[0]?.game_id);
 return <main className="replay-app"><div className="replay-tools"><h1>SocietySim viewer</h1><p><input type="file" accept=".jsonl" aria-label="Open a game log (.jsonl)" onChange={e => onFile(e.target.files?.[0])}/>{log && <ModeToggle mode={mode} onChange={changeMode}/>}</p>
 {loadError && <p role="alert">Could not load log: {loadError}</p>}
 {log && log.errors.length > 0 && <details open><summary role="alert">{log.errors.length} bad line(s) skipped</summary><ul>{log.errors.map(b => <li key={b.line}>line {b.line}: {b.error}</li>)}</ul></details>}
 {!log && <p>Open a recorded game log or choose a game from Games.</p>}{log && !events.length && <p role="status">This file has no valid game events.</p>}</div>
 {events.length > 0 && <Game events={events} labels={gameLabels} idx={idx} mode={mode} selected={selected} onSelect={setSelected} onSeqChange={onSeqChange}/>}</main>;
}
