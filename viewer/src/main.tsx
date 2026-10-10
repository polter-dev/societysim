import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { DEFAULT_INDEX_URL, indexSource } from "./data/staticSource";
import IndexPage from "./pages/Index";
import LeaderboardPage from "./pages/Leaderboard";
import "./a11y.css";

// Routes: ?game=<id> or ?log=<url> or ?page=replay -> replay, ?page=leaderboard, else the game index.
// ?index=<url> points every page at another index.json; leaderboard.json and metrics.json sit next to it.
const params = new URLSearchParams(location.search);
const indexParam = params.get("index");
const indexUrl = indexParam ?? DEFAULT_INDEX_URL;
const source = indexSource(indexUrl);
const href = (extra: Record<string, string>) =>
  "?" + new URLSearchParams({ ...(indexParam ? { index: indexParam } : {}), ...extra });
const sibling = (name: string) => new URL(name, new URL(indexUrl, location.href)).href;

const page = params.get("page");
const replay = params.has("game") || params.has("log") || page === "replay";
const style = { fontFamily: "system-ui, sans-serif", padding: 16, maxWidth: 1100 };

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <nav aria-label="Pages" style={{ ...style, paddingBottom: 0 }}>
      <a href={href({})}>Games</a> | <a href={href({ page: "leaderboard" })}>Leaderboard</a> |{" "}
      <a href={href({ page: "replay" })}>Open a log file</a>
    </nav>
    {replay ? (
      <App source={source} />
    ) : (
      <main style={style}>
        <h1>SocietySim {page === "leaderboard" ? "leaderboard" : "games"}</h1>
        {page === "leaderboard" ? (
          <LeaderboardPage leaderboardUrl={sibling("leaderboard.json")} metricsUrl={sibling("metrics.json")} />
        ) : (
          <IndexPage source={source} href={(id) => href({ game: id })} />
        )}
      </main>
    )}
  </StrictMode>,
);
