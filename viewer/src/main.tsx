import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import LandingPage from "./landing/LandingPage";
import ModeSelection from "./landing/ModeSelection";
import AdvancedSetup from "./landing/AdvancedSetup";
import { neonSource, withFallback } from "./data/neonSource";
import { DEFAULT_INDEX_URL, indexSource, publishedBase } from "./data/staticSource";
import { retryLoad } from "./data/source";
import ModelProfilePage from "./components/ModelProfile/ModelProfile";
import { modelName } from "./data/modelNames";
import IndexPage from "./pages/Index";
import LeaderboardPage from "./pages/Leaderboard";
import NewGamePage from "./pages/NewGame";
import PlayPage, { PlayDock, PlayNavLink } from "./pages/Play";
import ResearchPage from "./pages/Research";
import BackgroundMusic from "./components/BackgroundMusic/BackgroundMusic";
import TrailerPage, { AUDIO_INDEX_URL } from "./pages/Trailer";
import "./pages/pages.css";
import "./a11y.css";
import "./play/play.css";
import "./controls.css";
// Last, so the game-screen layout wins over the older page rules it replaces.
import "./illustrated/layout.css";

// Routes: ?game=<id> or ?log=<url> or ?page=replay -> replay, ?page=leaderboard,
// ?page=model&model=<name> -> model profile, else the game index.
// ?base=<url> overrides the published data root. Legacy ?index=<url> remains supported.
const params = new URLSearchParams(location.search);
const page = params.get("page");
const home = !params.has("game") && !params.has("log") && (!page || page === "home");
const modes = !params.has("game") && !params.has("log") && page === "modes";
const advanced = !params.has("game") && !params.has("log") && page === "play" && params.get("mode") === "advanced";
const trailer = !params.has("game") && !params.has("log") && page === "trailer";
const indexParam = params.get("index");
let baseError: string | null = null;
let dataBase: URL;
try { dataBase = publishedBase(location.search, location.href); }
catch (err) { baseError = err instanceof Error ? err.message : String(err); dataBase = new URL('./', location.href); }
const indexUrl = new URL(indexParam ?? DEFAULT_INDEX_URL, dataBase).href;
const staticGames = home ? undefined : indexSource(indexUrl, undefined, !indexParam && !params.get("base"));
// Neon (index, leaderboard, live games) when the build has both Neon URLs and the URL doesn't
// pick a static batch with ?base= or ?index=. Neon errors fall back to the static files.
// The global rankings board reads Neon directly, so it uses the same gate.
// A visitor game lives only in Neon: no static fallback, and its row may land a moment after the start.
const { VITE_NEON_DATA_API_URL, VITE_NEON_AUTH_URL } = import.meta.env;
const neonOn = !home && Boolean(VITE_NEON_DATA_API_URL && VITE_NEON_AUTH_URL);
const visitorGame = params.get("game")?.startsWith("visitor-") ?? false;
const neon =
  neonOn && visitorGame ? retryLoad(neonSource())
    : neonOn && !indexParam && !params.get("base") ? withFallback(neonSource(), staticGames!)
    : null;
const source = neon ?? staticGames;
const href = (extra: Record<string, string>) =>
  "?" + new URLSearchParams({ ...(params.get("base") ? { base: params.get("base")! } : {}), ...(indexParam ? { index: indexParam } : {}), ...extra });
const sibling = (name: string) => new URL(name, new URL(indexUrl, location.href)).href;

const replay = params.has("game") || params.has("log") || page === "replay";
const ledger = page === "leaderboard";
const joining = !params.has("game") && !params.has("log") && page === "play" && params.get("mode") !== "advanced";
const profileModel = page === "model" ? params.get("model") : null;
const profileHref = (model: string) => href({ page: "model", model });
const title =
  profileModel ?? { leaderboard: "leaderboard", research: "research", new: "new game" }[page ?? ""] ?? "games";
const catalogUrl = sibling("models_catalog.json");
const schemaUrl = sibling("devui/schema/game_setup.schema.json");

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BackgroundMusic />
    {baseError && <p role="alert">Could not use batch base: {baseError}</p>}
    {!home && !modes && !advanced && !joining && !trailer && <nav aria-label="Pages" className={ledger ? "site-nav site-nav--float" : "site-nav"}>
      <a href={href({})}>Home</a><a href={href({page:"games"})}>Games</a><a href={href({ page: "leaderboard" })}>Leaderboard</a><a href={href({ page: "research" })}>Research</a>
      <PlayNavLink href={href({ page: "modes" })} />
      <a href={href({ page: "replay" })}>Open a log file</a>
    </nav>}
    {home ? <LandingPage href={href}/> : trailer ? <TrailerPage audioIndexUrl={new URL(AUDIO_INDEX_URL, new URL(import.meta.env.BASE_URL, location.href)).href}/> : modes ? <ModeSelection href={href}/> : advanced ? <AdvancedSetup href={href} catalogUrl={catalogUrl} schemaUrl={schemaUrl}/> : ledger ? (
      <LeaderboardPage
        leaderboardUrl={sibling("leaderboard.json")}
        metricsUrl={sibling("metrics.json")}
        profileHref={profileHref}
        globalBoard={Boolean(neon)}
        batchBase={dataBase.href}
      />
    ) : joining ? (
      <PlayPage initialMode={params.get("mode") === "watch" ? "watch" : "basic"} catalogUrl={catalogUrl} schemaUrl={schemaUrl} gamesHref={href({page:"games"})} gameHref={(id) => href({ game: id })} backHref={href({ page: "modes" })} />
    ) : replay ? (
      <div className="play-replay">
        <PlayDock gameId={params.get("game")} />
        <App source={source} />
      </div>
    ) : (
      <main className={profileModel || page === "new" ? "info-page info-page--text" : "info-page"}>
        <h1 title={profileModel ?? undefined}>SocietySim {profileModel ? modelName(profileModel) : title}</h1>
        <form method="get" className="batch-source"><label>Published batch base URL <input name="base" type="url" required defaultValue={dataBase.href} /></label><button type="submit">Load batch</button></form>
        {profileModel ? (
          <ModelProfilePage metricsUrl={sibling("metrics.json")} model={profileModel} />
        ) : page === "research" ? (
          <ResearchPage metricsUrl={sibling("metrics.json")} />
        ) : page === "new" ? (
          <NewGamePage catalogUrl={catalogUrl} schemaUrl={schemaUrl} />
        ) : (
          <IndexPage source={source!} href={(id) => href({ game: id })} />
        )}
      </main>
    )}
  </StrictMode>,
);
