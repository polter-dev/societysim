import { useEffect, useRef } from 'react';
import type { Scene, Mode } from '../scene/deriveScene';
import { inspect } from '../components/Inspector/Inspector';
import { AvatarMedallion } from '../components/AvatarMedallion/AvatarMedallion';
import { modelName } from '../data/modelNames';
import { eventPresentation, playerStatistics } from './presentation';
import './PlayerProfile.css';

const count = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;

/** The player profile dialog a seat or Agents-panel button opens. Sized by clamp(); only the content panes scroll. */
export function IllustratedInspector({ scene, mode, selected, close }: { scene: Scene; mode: Mode; selected: string | null; close: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const player = scene.players.find(p => p.id === selected);
  const open = !!player;
  useEffect(() => {
    const node = ref.current;
    if (!open || !node) return;
    const previous = document.activeElement as HTMLElement | null;
    node.showModal();
    return () => { node.close(); previous?.focus(); };
  }, [open]);
  if (!player) return null;
  const stats = playerStatistics(scene, player.id);
  const info = inspect(scene, player.id);
  const name = (id: string) => scene.players.find(p => p.id === id)?.name ?? id;
  const narrator = mode === 'narrator';
  const speech = narrator ? info.reasoning : info.publicText;
  const tiles: [string, number][] = [['Statements', stats.statements], ['Votes cast', stats.votes], ['Times accused', stats.accused], ['Days survived', stats.days]];
  return <dialog ref={ref} className="illustrated-dialog player-profile" aria-label={`Inspector: ${player.name}`} onCancel={close} onClick={e => { if (e.target === e.currentTarget) close(); }}>
    <header className="profile-head">
      <AvatarMedallion model={player.model} label={modelName(player.model)} alive={player.alive}/>
      <div className="profile-id">
        <h2>{player.name}</h2>
        <p className="profile-model" title={player.model}>{modelName(player.model)}</p>
        <p className="profile-badges"><span className="profile-badge" data-alive={player.alive}>{player.alive ? '● Alive' : '† Eliminated'}</span><span className="profile-badge profile-role">◇ {player.role ?? 'role unknown'}</span></p>
      </div>
      <button autoFocus onClick={close} className="profile-close" aria-label="Close agent inspector">×</button>
    </header>
    <dl className="profile-stats">{tiles.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>
    <div className="profile-body">
      <div className="profile-col">
        <section className="profile-section">
          <h3>{narrator ? 'Private reasoning' : 'Public statement'}</h3>
          {speech || (narrator && (info.wolfChat || info.wolfProposal))
            ? <div tabIndex={0} className="profile-pane" aria-label={narrator ? 'Private reasoning' : 'Public statement'}>
                {speech && <p>{speech}</p>}
                {narrator && info.wolfChat && <p>Wolf chat: {info.wolfChat}</p>}
                {narrator && info.wolfProposal && <p>Proposed target: {name(info.wolfProposal)}</p>}
              </div>
            : <p className="profile-empty">{narrator ? 'No reasoning logged yet.' : 'Nothing said yet.'}</p>}
        </section>
        <section className="profile-section">
          <h3>{narrator ? 'Suspicion' : 'Public activity'}</h3>
          {!narrator ? <p className="profile-empty">{count(stats.statements, 'statement')} and {count(stats.votes, 'vote')} so far.</p>
            : info.suspicion.length
              ? <ul tabIndex={0} className="profile-pane profile-suspicion" aria-label="Suspicion scores">{info.suspicion.map(s => <li key={s.player}><span>{name(s.player)}</span><meter min={0} max={1} value={s.p}>{s.p}</meter><span>{Math.round(s.p * 100)}%</span></li>)}</ul>
              : <p className="profile-empty">No suspicion scores logged yet.</p>}
        </section>
      </div>
      <section className="profile-section profile-history">
        <h3>Decision history</h3>
        {stats.history.length
          ? <ol tabIndex={0} className="profile-pane" aria-label={`${player.name} decision history`}>{[...stats.history].reverse().map(e => {
              const item = eventPresentation(e, scene);
              return <li key={e.seq}><small>{e.phase === 'night' ? 'Night' : 'Day'} {e.day}</small><strong>{item.title}</strong><p>{item.text}</p></li>;
            })}</ol>
          : <p className="profile-empty">No decisions yet.</p>}
      </section>
    </div>
  </dialog>;
}
