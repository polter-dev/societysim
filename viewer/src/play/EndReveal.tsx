import { useState } from 'react';
import type { Event, Label } from '../types/events';
import { deriveScene, type Mode } from '../scene/deriveScene';

/** Only facts from a finished game's recorded human ratings, never an inferred causal claim. */
export function trustedWolves(events: Event[], idx: number) {
  const shown = events.slice(0,idx+1);
  const end = shown.find(e => e.type === 'game_end');
  if (!end) return [];
  const seats = shown.find(e => e.type === 'game_start')?.meta.seats ?? {};
  const human = Object.keys(seats).find(id => seats[id].model === 'human');
  if (!human) return [];
  const latest = new Map<string, number>();
  for (const event of shown) if (event.actor === human) for (const score of event.suspicion ?? []) latest.set(score.player,score.p);
  return Object.entries(end.meta.roles ?? {}).filter(([id,role]) => role === 'wolf' && latest.has(id) && latest.get(id)! < .5)
    .map(([id]) => ({id,name:seats[id]?.name ?? id,p:latest.get(id)!}));
}

export function EndReveal({events,labels,idx,mode}: {events:Event[];labels:Label[];idx:number;mode:Mode}) {
  const [copied,setCopied] = useState(false);
  const shown = events.slice(0,idx+1);
  const end = shown.find(e => e.type === 'game_end');
  if (!end) return null;
  const scene = deriveScene(events,idx,mode);
  const fooled = trustedWolves(events,idx);
  const human = scene.players.find(p => p.model === 'human');
  const visibleLabels = labels.filter(l => l.game_id === end.game_id && shown.some(e => e.seq === l.source_seq));
  const params = new URLSearchParams(typeof window === "undefined" ? "" : window.location.search);
  const again = new URLSearchParams();
  for(const key of ['base','index']) if(params.has(key)) again.set(key,params.get(key)!);
  again.set('page','modes');
  const share = new URL(typeof window === "undefined" ? "/" : window.location.href || "/", "https://society-sim.us/");
  share.searchParams.delete('seq'); share.searchParams.set('step',String(end.seq));
  const reveal = new URL(share); reveal.searchParams.set("mode","narrator");
  const copy = async () => {try {await navigator.clipboard.writeText(share.href);setCopied(true);} catch {setCopied(false);} };
  return <section className="game-reveal" aria-labelledby="game-reveal-title">
    <h2 id="game-reveal-title">The village revealed</h2>
    <p>{scene.winner === 'wolves' ? 'The Werewolves won.' : scene.winner === 'villagers' ? 'The village won.' : 'The game ended in a draw.'}</p>
    <table><thead><tr><th>Player</th><th>Model</th><th>Role</th></tr></thead><tbody>{scene.players.map(p => <tr key={p.id}><td>{p.name} ({p.id})</td><td>{p.model}</td><td>{p.role ?? 'Not recorded'}</td></tr>)}</tbody></table>
    {mode === "narrator" && <>
    {human && <><h3>Who fooled you</h3><p>Your last recorded wolf ratings, compared with the revealed roles.</p>{fooled.length ? <ul>{fooled.map(p => <li key={p.id}>{p.name} was a Werewolf. You rated them {Math.round(p.p*100)}% likely to be a wolf.</li>)}</ul> : <p>No Werewolf had a recorded rating below 50% from you.</p>}</>}
    <details><summary>Hidden reasoning and wolf chat</summary>{shown.filter(e => e.private_reasoning || e.type === 'wolf_chat').map(e => <div key={e.seq}><h4>Day {e.day} · {scene.players.find(p => p.id === e.actor)?.name ?? e.actor}</h4>{e.private_reasoning && <pre>{e.private_reasoning}</pre>}{e.type === 'wolf_chat' && <p>{e.public_text}</p>}</div>)}{!shown.some(e => e.private_reasoning) && <p>Hidden reasoning has not arrived in this log yet.</p>}</details>
    <details><summary>Lies and deception labels ({visibleLabels.length})</summary>{visibleLabels.length ? <ul>{visibleLabels.map((l,i) => <li key={i}>{scene.players.find(p => p.id === l.actor)?.name ?? l.actor}: {l.label.replaceAll('_',' ')} · {l.detail} ({l.source}, {Math.round(l.confidence*100)}% confidence)</li>)}</ul> : <p>No lie labels are available for this game yet.</p>}</details>
    </>}
    {mode !== "narrator" && <p><a href={reveal.href}>Reveal hidden reasoning, lies, and who fooled you →</a></p>}
    <div className="game-reveal__actions"><a href={'?'+again}>Play again</a><button onClick={() => void copy()}>{copied ? 'Link copied' : 'Copy share link'}</button><a href={share.href}>Shareable game link</a></div>
  </section>;
}
