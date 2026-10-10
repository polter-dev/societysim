import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { DEFAULT_INDEX_URL, indexSource, publishedBase } from "./data/staticSource";
import IndexPage from "./pages/Index";
import LeaderboardPage from "./pages/Leaderboard";
import "./a11y.css";

// Routes: ?game=<id> or ?log=<url> or ?page=replay -> replay, ?page=leaderboard, else the game index.
// ?base=<url> overrides the published data root. Legacy ?index=<url> remains supported.
const params = new URLSearchParams(location.search);
const indexParam = params.get("index");
let baseError: string | null = null;
let dataBase: URL;
try { dataBase = publishedBase(location.search, location.href); }
catch (err) { baseError = err instanceof Error ? err.message : String(err); dataBase = new URL('./', location.href); }
const indexUrl = new URL(indexParam ?? DEFAULT_INDEX_URL, dataBase).href;
const source = indexSource(indexUrl);
const href = (extra: Record<string, string>) =>
  "?" + new URLSearchParams({ ...(params.get("base") ? { base: params.get("base")! } : {}), ...(indexParam ? { index: indexParam } : {}), ...extra });
const sibling = (name: string) => new URL(name, new URL(indexUrl, location.href)).href;

const page = params.get("page");
const replay = params.has("game") || params.has("log") || page === "replay";
const style = { fontFamily: "system-ui, sans-serif", padding: 16, maxWidth: 1100 };

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {baseError && <p role="alert">Could not use batch base: {baseError}</p>}
    <nav aria-label="Pages" style={{ ...style, paddingBottom: 0 }}>
      <a href={href({})}>Games</a> | <a href={href({ page: "leaderboard" })}>Leaderboard</a> |{" "}
      <a href={href({ page: "replay" })}>Open a log file</a>
    </nav>
    {replay ? (
      <App source={source} />
    ) : (
      <main style={style}>
        <h1>SocietySim {page === "leaderboard" ? "leaderboard" : "games"}</h1>
        <form method="get" className="batch-source"><label>Published batch base URL <input name="base" type="url" required defaultValue={dataBase.href} /></label><button type="submit">Load batch</button></form>
        {page === "leaderboard" ? (
          <LeaderboardPage leaderboardUrl={sibling("leaderboard.json")} metricsUrl={sibling("metrics.json")} />
        ) : (
          <IndexPage source={source} href={(id) => href({ game: id })} />
        )}
      </main>
    )}
  </StrictMode>,
);
