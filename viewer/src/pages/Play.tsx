import { useEffect, useState } from "react";
import { PlayStart } from "../components/PlayStart/PlayStart";
import { YourTurnPanel } from "../components/YourTurnPanel/YourTurnPanel";
import { playEnabled } from "../play/config";
import { loadToken } from "../play/api";
import { usePlayStatus } from "../play/usePlayStatus";
import { useHumanSeat } from "../play/useHumanSeat";
import { loadSetupAssets, type SetupAssets } from "../settings/setup";

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

/** Start screen. Advanced settings need the model list; if it won't load, Basic and Watch still work. */
export default function PlayPage({
  catalogUrl, schemaUrl, gamesHref, gameHref, initialMode = "basic", go = (url) => window.location.assign(url),
}: { initialMode?: "basic" | "watch" | "advanced"; catalogUrl: string; schemaUrl: string; gamesHref: string; gameHref: (id: string) => string; go?: (url: string) => void }) {
  const on = playEnabled();
  const status = usePlayStatus();
  const [assets, setAssets] = useState<SetupAssets | undefined>();
  useEffect(() => {
    if (on) loadSetupAssets(catalogUrl, schemaUrl).then(setAssets, () => setAssets(undefined));
  }, [on, catalogUrl, schemaUrl]);
  if (!on) return <PlayComingSoon gamesHref={gamesHref} />;
  if (status.kind === "offline" || status.kind === "closed")
    return (
      <section className="play-offline" role="status">
        <p>{status.message}</p>
        <p><a href={gamesHref}>Watch replays</a></p>
      </section>
    );
  // startGame saves the seat token in sessionStorage; the live table opens on the game page.
  return (
    <>
      <h2>{initialMode === "watch" ? "Watch the village" : "Join the village"}</h2>
<p>{initialMode === "watch" ? "Start an all-AI game and spectate live." : "Play one seat against nine AI models. You see your own role and private information; the full reveal comes at the end."}</p>
      <PlayStart modeLocked initialMode={initialMode} assets={assets} onStarted={(game) => go(gameHref(game.game_id))} />
    </>
  );
}

function Dock({ gameId }: { gameId: string }) {
  return <YourTurnPanel seat={useHumanSeat(gameId)} />;
}

/**
 * Your-turn panel above the live table. Shown only in the browser tab that started the game as a
 * player (its seat token is in sessionStorage); spectators and watch-only visitors never get it.
 */
export function PlayDock({ gameId }: { gameId: string | null }) {
  if (!gameId || !playEnabled() || !loadToken(gameId)) return null;
  return <Dock gameId={gameId} />;
}
