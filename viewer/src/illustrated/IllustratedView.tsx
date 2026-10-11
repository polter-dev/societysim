import { useEffect, useRef, useState } from 'react';
import type { Scene, Mode } from '../scene/deriveScene';
import type { Event } from '../types/events';
import { inspect } from '../components/Inspector/Inspector';
import { art } from './assets';
import { AvatarMedallion } from '../components/AvatarMedallion/AvatarMedallion';
import { useModelLabel } from '../components/ModelAvatar/ModelAvatar';
import { eventPresentation, playerStatistics } from './presentation';
import ForestTable from './ForestTable';
import './scenery.css';
import './inspector.css';
import './viewer.css';

export function IllustratedShell({ scene, onSelect }: { scene: Scene; onSelect: (id: string) => void }) {
  const label = useModelLabel();
  const [collapsed, setCollapsed] = useState(false);
  const [paused, setPaused] = useState(false);
  const [eventSeq, setEventSeq] = useState<number | null>(null);
  const event = scene.log.find(e => e.seq === eventSeq);
  const night = scene.phase === 'night' || scene.phase === 'setup';
  return <div className={`illustrated-shell ${night ? 'night' : 'day'} ${collapsed ? 'agents-collapsed' : ''} ${paused ? 'still' : ''}`}>
    <ForestTable paused={paused}/>
    <div className="day-counter" key={`${scene.day}-${scene.phase}`}><img src={art(`new/${night ? 'night_counter 1.png' : 'day_counter-v2.png'}`)} alt=""/><span>{scene.phase === 'setup' ? 'Gathering the village' : `${night ? 'Night' : 'Day'} ${scene.day} · ${scene.phase === 'vote' ? 'Voting' : night ? 'Nightfall' : 'Discussion'}`}{scene.winner && ` · ${scene.winner} win`}</span></div>
    <section id="agent-roster" className="roster" aria-label="Agents" inert={collapsed} aria-hidden={collapsed}>
      <img className="panel-art" src={art('new/agent menu-1.png')} alt=""/>
      <div className="panel-content"><h2>Agents</h2><ul className="agent-list">{scene.players.map(p => <li key={p.id}><button className="agent-button" data-alive={p.alive} onClick={() => onSelect(p.id)}><AvatarMedallion model={p.model} label={label(p.model)} alive={p.alive}/><span><strong>{p.name}</strong><small>{label(p.model)}</small><small>{p.alive ? '● Alive' : '† Eliminated'}</small></span></button></li>)}</ul></div>
    </section>
    <button className="roster-toggle" aria-expanded={!collapsed} aria-controls="agent-roster" aria-label={collapsed ? 'Expand agents' : 'Collapse agents'} onClick={() => setCollapsed(!collapsed)}><img src={art(`new/Arrow_${collapsed ? 'Right' : 'Left'} 1.png`)} alt=""/></button>
    <section className="event-roster" aria-label="Game events"><img className="panel-art" src={art('new/event menu.png')} alt=""/><div className="panel-content"><h2>Game events</h2><ol className="event-list">{[...scene.log].reverse().map(e => {const item = eventPresentation(e, scene);return <li key={e.seq}><button className={`decision-card ${item.image === '10_notes.png' ? 'decision-notes' : ''}`} style={{backgroundImage:`url("${art(`inspector/${item.image}`)}")`}} onClick={() => setEventSeq(e.seq)}><span><small>{e.phase === 'night' ? 'Night' : 'Day'} {e.day} · {item.actor}</small><strong>{item.title}</strong><small>{item.text}</small></span></button></li>})}</ol></div></section>
    <button className="motion-toggle" onClick={() => setPaused(!paused)}>{paused ? 'Resume ambience' : 'Pause ambience'}</button>
    {event && <EventDetails key={`${event.seq}-${scene.log.length}`} event={event} scene={scene} close={() => setEventSeq(null)}/>}
  </div>;
}
function Modal({ children, close, label, className = '' }: { children: React.ReactNode; close: () => void; label: string; className?: string }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => { const node = ref.current!; const previous = document.activeElement as HTMLElement | null; node.showModal(); return () => {node.close(); previous?.focus();}; }, []);
  return <dialog ref={ref} className={`illustrated-dialog ${className}`} aria-label={label} onCancel={close} onClick={e => { if(e.target === e.currentTarget) close(); }}>{children}</dialog>;
}
function EventDetails({ event, scene, close }: { event: Event; scene: Scene; close: () => void }) {
  const item = eventPresentation(event, scene);
  const people = [...new Set([event.actor, event.target].filter((id): id is string => !!id))].map(id => scene.players.find(p => p.id === id)).filter(p => !!p);
  return <Modal close={close} label={`Event: ${item.title}`} className="event-dialog"><div className="event-detail-board"><img className="detail-art" src={art('event-details-v2.png')} alt=""/><button className="detail-close" autoFocus onClick={close} aria-label="Close event details">×</button><h2 className="detail-heading">Event Details</h2><h3 className="detail-title">{item.title}</h3><p className="detail-time">{event.phase} {event.day} · Event {event.seq}</p><p className="detail-description" tabIndex={0}>{item.text}</p><section className="detail-participants" tabIndex={0}><h3>Participants</h3>{people.length ? people.map(p => <p key={p.id}><strong>{p.name}</strong><br/>{p.model} · {p.role ?? 'role unknown'}</p>) : <p>Village event</p>}</section><section className="detail-reasoning" tabIndex={0}><h3>Logged reasoning</h3><p>{event.private_reasoning ?? 'No reasoning available in this view.'}</p></section><section className="detail-outcome" tabIndex={0}><h3>Logged outcome</h3><p>{item.result ?? 'No separate outcome logged for this action.'}</p></section></div></Modal>;
}
export function IllustratedInspector({scene, mode, selected, close}: {scene: Scene; mode: Mode; selected: string | null; close: () => void}) {
 const player = scene.players.find(p => p.id === selected);
 if (!player) return null;
 const stats = playerStatistics(scene, player.id);
 const info = inspect(scene, player.id);
 return <Modal close={close} label={`Inspector: ${player.name}`} className="inspector-dialog"><div className="inspector-board">
 <img className="inspector-art" src={art('inspector/Inspector Illustration.png')} alt=""/>
 <h2 className="inspector-mobile-heading">Agent Inspector</h2><button autoFocus onClick={close} className="inspector-close" aria-label="Close agent inspector"><span>×</span></button>
 <section className="inspector-profile" aria-label="Agent profile"><h3>{player.name}</h3><p>{player.model}</p><div className="profile-badges"><span className="status-badge">● {player.alive ? 'Alive' : 'Eliminated'}</span><span className="role-badge">◇ {player.role ?? 'role unknown'}</span></div></section>
 <dl className="inspector-stats">{[['Statements made',stats.statements],['Votes cast',stats.votes],['Times accused',stats.accused],['Days survived',stats.days]].map(([label,value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
 <section className="inspector-goals"><h3>{mode === 'narrator' ? 'Private reasoning' : 'Public statement'}</h3><div tabIndex={0} className="inspector-scroll" aria-label={mode === 'narrator' ? 'Private reasoning' : 'Public statement'}><p>{mode === 'narrator' ? info.reasoning ?? 'No reasoning logged yet.' : info.publicText ?? 'Nothing said yet.'}</p>{mode === 'narrator' && info.wolfChat && <p>Wolf chat: {info.wolfChat}</p>}{mode === 'narrator' && info.wolfProposal && <p>Proposed target: {scene.players.find(p => p.id === info.wolfProposal)?.name ?? info.wolfProposal}</p>}</div></section>
 <section className="inspector-history"><h3>Decision History</h3><ul tabIndex={0} className="inspector-scroll" aria-label={`${player.name} decision history`}>{[...stats.history].reverse().map(e => {const item=eventPresentation(e,scene);return <li key={e.seq} className={`decision-card ${item.image === '10_notes.png' ? 'decision-notes' : ''}`} style={{backgroundImage:`url("${art(`inspector/${item.image}`)}")`}}><div><small>{e.phase} {e.day} · {item.actor}</small><h4>{item.title}</h4><p>{item.text}</p></div></li>})}{stats.history.length === 0 && <li>No decisions yet.</li>}</ul></section>
 <section className="inspector-analysis"><h3>{mode === 'narrator' ? 'Suspicion' : 'Public activity'}</h3><div tabIndex={0} className="inspector-scroll" aria-label="Agent activity">{mode === 'narrator' ? info.suspicion.length ? info.suspicion.map(s => <p key={s.player}>{scene.players.find(p => p.id === s.player)?.name ?? s.player}: <meter min={0} max={1} value={s.p}>{s.p}</meter> {Math.round(s.p*100)}%</p>) : <p>No suspicion scores logged yet.</p> : <p>{stats.statements} statements and {stats.votes} votes so far.</p>}</div></section>
 </div></Modal>;
}

