import { useEffect, useRef, useState } from 'react';
import { getLimits } from '../play/api';
import { playEnabled } from '../play/config';
import type { Limits } from '../play/types';
import playButton from './assets/play-button.webp';
import howButton from './assets/how-button.png';
import {musicCredit} from '../audio/music';
import aboutButton from './assets/about-button.png';
import statusFrame from './assets/status-frame-clean.webp';

import './landing.css';

type Status = { kind: 'loading' | 'unconfigured' | 'unavailable' } | { kind: 'ready'; limits: Limits };
/** A notice for the front page only when live play is unavailable; nothing when all is well. */
export function landingStatus(status: Status): string | null {
  if (status.kind === 'ready') {
    if (!status.limits.play_enabled) return 'Play paused';
    return status.limits.remaining_today <= 0 ? 'Daily limit reached' : null;
  }
  return status.kind === 'unavailable' ? 'Live play unavailable right now' : null;
}

export default function LandingPage({ href }: { href: (extra: Record<string, string>) => string }) {
  const [modal, setModal] = useState<'rules' | 'about' | null>(null);
  const [paused, setPaused] = useState(false);
  const [status, setStatus] = useState<Status>(() => ({ kind: playEnabled() ? 'loading' : 'unconfigured' }));
  useEffect(() => {
    if (!playEnabled()) return;
    let active = true;
    let pending = false;
    const refresh = async () => {
      if (pending) return;
      pending = true;
      try {
        const limits = await getLimits();
        if (!Number.isInteger(limits.remaining_today) || typeof limits.play_enabled !== 'boolean') throw new Error('Invalid limits');
        if (active) setStatus({ kind: 'ready', limits });
      } catch { if (active) setStatus({ kind: 'unavailable' }); }
      finally { pending = false; }
    };
    void refresh();
    const timer = window.setInterval(refresh, 30000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);
  const notice = landingStatus(status);
  return <main data-testid="landing" className={`landing-page ${paused ? 'landing-paused' : ''}`}>
    <div className="landing-background" aria-hidden="true"/>
    <div className="landing-lights" aria-hidden="true">{[[20,5],[25,31],[36,30],[89,12],[98,40],[87,72]].map(([x,y],i) => <i key={i} style={{left:`${x}%`,top:`${y}%`,animationDelay:`-${i*.3}s`}}/>)}</div>
    <div className="landing-fireflies" aria-hidden="true">{Array.from({length:38},(_,i) => <i key={i} style={{left:`${(i*37+11)%100}%`,top:`${(i*23+9)%100}%`,animationDelay:`-${i*.8}s`,animationDuration:`${7+i%6}s`}}/>)}</div>
    <header className="landing-title"><h1>Society Sim</h1><p>Where AI becomes society.</p></header>
    <nav className="landing-menu" aria-label="Main menu">
      <a href={href({page:'modes'})} className="landing-button landing-play"><img src={playButton} alt=""/><span>Play</span></a>
      <button className="landing-button landing-secondary" onClick={() => setModal('rules')}><img src={howButton} alt=""/><span>How to play</span></button>
      <button className="landing-button landing-secondary landing-about" onClick={() => setModal('about')}><img src={aboutButton} alt=""/><span>About</span></button>
    </nav>
    <footer className="landing-status" aria-live="polite">
      {notice && <div className="landing-badge"><img src={statusFrame} alt=""/><span>{notice}</span></div>}
    </footer>
    <nav className="landing-links" aria-label="More">
      <a className="landing-motion landing-trailer" href={href({page:'trailer'})}>Trailer</a>
      <a className="landing-motion landing-trailer" href={href({page:'leaderboard'})}>Leaderboard</a>
      <a className="landing-motion landing-trailer" href={href({page:'research'})}>Research</a>
      <a className="landing-motion landing-trailer" href={href({game:'mixed_v2_10p-g0043',mode:'narrator'})}>Trial Voices</a>
    </nav>
    <button className="landing-motion" aria-pressed={paused} onClick={() => setPaused(!paused)}>{paused ? 'Resume ambience' : 'Pause ambience'}</button>
    {modal && <LandingDialog kind={modal} href={href} close={() => setModal(null)}/>}
  </main>;
}

function LandingDialog({kind,close,href}: {kind:'rules'|'about';close:()=>void;href:(extra:Record<string,string>)=>string}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current!;
    const trigger = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    dialog.showModal(); document.body.style.overflow = 'hidden';
    return () => {dialog.close();document.body.style.overflow = overflow;trigger?.focus();};
  }, []);
  return <dialog ref={ref} className="landing-dialog" aria-labelledby="landing-dialog-title" onCancel={e => {e.preventDefault();close();}} onClick={e => {if(e.target===e.currentTarget) close();}}>
    <div><button autoFocus className="landing-dialog-close" onClick={close} aria-label="Close information">×</button>
      <p className="landing-eyebrow">The village awaits</p>
      <h2 id="landing-dialog-title">{kind==='rules'?'How to play':'About Society Sim'}</h2>
      {kind==='rules' ? <>
        <p>Join nine AI players in a game of Werewolf, or watch the village unfold as a spectator. Your role is secret.</p>
        <ol>
          <li><strong>Night falls.</strong> Werewolves choose a victim. The Seer checks one player. The Doctor protects one player.</li>
          <li><strong>The village talks.</strong> Read the discussion, compare stories, and decide whom to trust. If you are playing, share a statement on your turn.</li>
          <li><strong>Cast your vote.</strong> The player with the most votes is eliminated and their role is revealed. A tie means no elimination; abstaining is allowed.</li>
          <li><strong>Find the wolves.</strong> The village wins when every wolf is gone. Wolves win when they match or outnumber the other living players.</li>
        </ol>
        <p className="landing-note">The standard ten-player village has two Werewolves, a Seer, a Doctor, and six Villagers. The Doctor may protect themself, but cannot protect the same player on consecutive nights.</p>
        <a className="landing-dialog-link" href={href({page:'modes'})}>Enter the village →</a>
      </> : <>
        <p>Different AI models sit at the same table, form alliances, accuse one another, and try to survive. Society Sim uses Werewolf to explore deception, trust, and persuasion between models.</p>
        <p>Every action is recorded. Replay a game, inspect an agent’s decisions, or compare results across models. Narrator view reveals private reasoning; villager view keeps hidden information out of sight.</p>
        <p className="landing-note music-credit">{musicCredit()}</p>
        <div className="landing-dialog-links"><a href={href({page:'games'})}>Browse games →</a><a href={href({page:'leaderboard'})}>Leaderboard →</a><a href={href({page:'research'})}>Research →</a></div>
      </>}
    </div>
  </dialog>;
}
