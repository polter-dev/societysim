import { useState } from 'react';
import normal from './assets/modes/normal.webp';
import watch from './assets/modes/watch.webp';
import advanced from './assets/modes/advanced.webp';
import back from './assets/modes/back.png';
import './landing.css';
import './mode-selection.css';

export default function ModeSelection({ href }: { href: (extra: Record<string, string>) => string }) {
  const [paused, setPaused] = useState(false);
  return <main className={`landing-page mode-selection ${paused ? 'landing-paused' : ''}`}>
    <div className="landing-background" aria-hidden="true" />
    <div className="landing-fireflies" aria-hidden="true">{Array.from({length:38}, (_,i) => <i key={i} style={{left:`${(i*37+11)%100}%`,top:`${(i*23+9)%100}%`,animationDelay:`-${i*.8}s`,animationDuration:`${7+i%6}s`}} />)}</div>
    <a className="mode-back" href={href({})} aria-label="Back to home"><img src={back} alt="" /></a>
    <h1>Choose your fate</h1>
    <nav className="mode-cards" aria-label="Game modes">
      <a className="mode-card" href={href({page:'play',mode:'basic'})} aria-label="Normal: Play against nine AI agents"><img src={normal} alt="Normal. Play against nine AI agents in a living simulation." /></a>
      <a className="mode-card" href={href({page:'play',mode:'watch'})} aria-label="Watch: Spectate an all-AI simulation"><img src={watch} alt="Watch. Spectate an all-AI simulation and see how their society evolves." /></a>
      <a className="mode-card" href={href({page:'play',mode:'advanced'})} aria-label="Advanced: Customize your simulation"><img src={advanced} alt="Advanced. Customize settings and create your own simulation." /></a>
    </nav>
    <button className="landing-motion" aria-pressed={paused} onClick={() => setPaused(!paused)}>{paused ? 'Resume ambience' : 'Pause ambience'}</button>
  </main>;
}
