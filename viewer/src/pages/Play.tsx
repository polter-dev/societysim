import { useEffect, useState } from "react";
import { PlayStart } from "../components/PlayStart/PlayStart";
import { YourTurnPanel } from "../components/YourTurnPanel/YourTurnPanel";
import { playEnabled } from "../play/config";
import { leaveWatch, leaveWatchOnUnload, loadToken, loadWatchToken } from "../play/api";
import { usePlayStatus } from "../play/usePlayStatus";
import { useHumanSeat } from "../play/useHumanSeat";
import { useLiveEvents } from "../play/liveEvents";
import { loadSetupAssets, type SetupAssets } from "../settings/setup";
import { art } from "../illustrated/assets";
import back from "../landing/assets/modes/back.png";
import "../landing/landing.css";
import "../landing/mode-selection.css";
import "../play/gate.css";

const CAST = [
  "inspector/01_discussion.png",
  "inspector/02_werewolf.png",
  "inspector/06_night.png",
  "inspector/04_investigation.png",
  "inspector/03_protection.png",
];

/** Nav entry: a link when play is on, a disabled "coming soon" label when it is not. */
export function PlayNavLink({ href }: { href: string }) {
  const status = usePlayStatus();
  if (playEnabled() && status.kind === "offline")
    return <span aria-disabled="true" title={status.message}>Play (offline)</span>;
  return playEnabled() ? (
    <a className="play-nav" href={href}>Play</a>
  ) : (
    <span className="play-nav" aria-disabled="true" title="Playing from the website is coming soon">Play (coming soon)</span>
  );
}

export function PlayComingSoon({ gamesHref }: { gamesHref: string }) {
  return (
    <section className="play-coming-soon" role="status">
      <p>Playing from the website is coming soon. For now you can watch recorded games and live matches.</p>
      <p><a href={gamesHref}>Browse games</a></p>
    </section>
  );
}

/**
 * True only when this page load is a fresh navigation from the mode picker (same-origin referrer is
 * ?page=modes). A pasted link, link-preview bot, prefetch or reload has no such referrer or is a reload.
 */
export function cameFromModePicker(): boolean {
  try {
    const nav = performance.getEntriesByType("navigation")[0] as PerformanceNavigationTiming | undefined;
    if (nav && nav.type !== "navigate") return false;
    if (!document.referrer) return false;
    const ref = new URL(document.referrer);
    return ref.origin === window.location.origin && ref.searchParams.get("page") === "modes";
  } catch {
    return false;
  }
}

/** Start screen. Advanced settings need the model list; if it won't load, Basic and Watch still work. */
export default function PlayPage({
  catalogUrl, schemaUrl, gamesHref, gameHref, initialMode = "basic", backHref = "?page=modes", go = (url) => window.location.replace(url), picked = cameFromModePicker,
}: { picked?: () => boolean; initialMode?: "basic" | "watch" | "advanced"; catalogUrl: string; schemaUrl: string; gamesHref: string; gameHref: (id: string) => string; backHref?: string; go?: (url: string) => void }) {
  const on = playEnabled();
  const [auto] = useState(() => initialMode !== "advanced" && picked());
  const status = usePlayStatus();
  const [assets, setAssets] = useState<SetupAssets | undefined>();
  const [paused, setPaused] = useState(false);
  useEffect(() => {
    if (on) loadSetupAssets(catalogUrl, schemaUrl).then(setAssets, () => setAssets(undefined));
  }, [on, catalogUrl, schemaUrl]);
  if (!on) return <PlayComingSoon gamesHref={gamesHref} />;
  const watch = initialMode === "watch";
  const counter = art("new/night_counter 1.png");
  if (status.kind === "offline" || status.kind === "closed")
    return (
      <main className={`play-gate landing-page ${paused ? "landing-paused" : ""}`}>
        <div className="landing-background" aria-hidden="true" />
        <a className="mode-back" href={backHref} aria-label="Back to game modes"><img src={back} alt="" /></a>
        <header className="play-gate__banner">
          {counter && <img src={counter} alt="" />}
          <h1>The village is quiet</h1>
        </header>
        <section className="play-offline" role="status">
          <p className="play-gate__lead">{status.message}</p>
          <p><a className="play-gate__link" href={gamesHref}>Watch replays</a></p>
        </section>
      </main>
    );
  // startGame saves the seat token in sessionStorage; the live table opens on the game page.
  // `go` replaces this history entry, so Back from the game can't land here and auto-start another.
  return (
    <main className={`play-gate landing-page ${paused ? "landing-paused" : ""}`}>
      <div className="landing-background" aria-hidden="true" />
      <div className="landing-lights" aria-hidden="true">{[[20, 8], [78, 14], [12, 62], [88, 70]].map(([x, y], i) => <i key={i} style={{ left: `${x}%`, top: `${y}%`, animationDelay: `-${i * 0.4}s` }} />)}</div>
      <div className="landing-fireflies" aria-hidden="true">{Array.from({ length: 28 }, (_, i) => <i key={i} style={{ left: `${(i * 37 + 11) % 100}%`, top: `${(i * 23 + 9) % 100}%`, animationDelay: `-${i * 0.8}s`, animationDuration: `${7 + (i % 6)}s` }} />)}</div>
      <a className="mode-back" href={backHref} aria-label="Back to game modes"><img src={back} alt="" /></a>
      <section className="play-gate__panel"><div className="play-gate__panel-content">
      <header className="play-gate__banner">
        {counter && <img src={counter} alt="" />}
        <h1>{watch ? "Watch the village" : "Join the village"}</h1>
      </header>
      <p className="play-gate__lead">{watch ? "Start an all-AI game and spectate live." : "Play one seat against nine AI models. You see your own role and private information; the full reveal comes at the end."}</p>
      <div className="play-gate__cast" aria-hidden="true">
        {CAST.map((name) => {
          const url = art(name);
          return url ? <img key={name} src={url} alt="" /> : null;
        })}
      </div>
      <PlayStart autoStart={auto} modeLocked initialMode={initialMode} assets={assets} onStarted={(game) => go(gameHref(game.game_id))} />
      </div></section>
      <button className="landing-motion" type="button" aria-pressed={paused} onClick={() => setPaused(!paused)}>{paused ? "Resume ambience" : "Pause ambience"}</button>
    </main>
  );
}

function Dock({ gameId }: { gameId: string }) {
  return <YourTurnPanel seat={useHumanSeat(gameId)} events={useLiveEvents(gameId)} />;
}

/**
 * Your-turn panel above the live table. Shown only in the browser tab that started the game as a
 * player (its seat token is in sessionStorage); spectators and watch-only visitors never get it.
 */
export function PlayDock({ gameId, modesHref = "?page=modes", go = (url) => window.location.assign(url) }: { gameId: string | null; modesHref?: string; go?: (url: string) => void }) {
  if (!gameId || !playEnabled()) return null;
  if (loadToken(gameId)) return <Dock gameId={gameId} />;
  if (loadWatchToken(gameId)) return <WatchBar gameId={gameId} modesHref={modesHref} go={go} />;
  return null;
}

/** Stop-watching control for a watch game. Also sends a best-effort leave when the page is closed. */
function WatchBar({ gameId, modesHref, go }: { gameId: string; modesHref: string; go: (url: string) => void }) {
  useEffect(() => {
    const onHide = () => leaveWatchOnUnload(gameId);
    window.addEventListener("pagehide", onHide);
    return () => window.removeEventListener("pagehide", onHide);
  }, [gameId]);
  const stop = async () => {
    await leaveWatch(gameId).catch(() => undefined);
    go(modesHref);
  };
  return (
    <section className="your-turn your-turn--watch" aria-label="Watching">
      <button type="button" className="your-turn__abstain your-turn__leave" onClick={() => void stop()}>
        Stop watching
      </button>
    </section>
  );
}
