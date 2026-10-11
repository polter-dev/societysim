// @vitest-environment jsdom
// main.tsx gates the Neon sources and the global rankings board the same way:
// on only when the build has both Neon URLs and the URL has no ?base= or ?index=.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const leaderboardProps = vi.hoisted(() => ({ current: null as null | { globalBoard?: boolean; loadBoard?: unknown } }));

vi.mock("./pages/Leaderboard", async () => {
  const { createElement } = await import("react");
  return {
    default: (props: { globalBoard?: boolean; loadBoard?: unknown }) => {
      leaderboardProps.current = props;
      return createElement("div");
    },
  };
});
vi.mock("./data/neonSource", () => ({
  neonSource: () => ({ loadBoard: async () => ({}) }),
  withFallback: (primary: unknown) => primary,
}));
vi.mock("./App", () => ({ default: () => null }));

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

async function loadMain(search: string, neonVars: boolean) {
  vi.resetModules();
  leaderboardProps.current = null;
  vi.stubEnv("VITE_NEON_DATA_API_URL", neonVars ? "https://neon.test/api" : "");
  vi.stubEnv("VITE_NEON_AUTH_URL", neonVars ? "https://neon.test/auth" : "");
  history.replaceState(null, "", `/${search}`);
  await import("./main");
  for (let i = 0; i < 10; i++) await new Promise((r) => setTimeout(r, 0));
  return leaderboardProps.current as { globalBoard?: boolean; loadBoard?: unknown } | null;
}

beforeEach(() => {
  document.body.innerHTML = '<div id="root"></div>';
});
afterEach(() => {
  vi.unstubAllEnvs();
});

describe("main.tsx global rankings gate", () => {
  it("is on with both Neon URLs and no custom source", async () => {
    const props = await loadMain("?page=leaderboard", true);
    expect(props?.globalBoard).toBe(true);
    expect(props?.loadBoard).toBeUndefined(); // static board only (#395)
  });

  it.each(["?page=leaderboard&base=https://example.test/data/", "?page=leaderboard&index=https://example.test/index.json"])(
    "is off with a custom static source (%s)",
    async (search) => {
      const props = await loadMain(search, true);
      expect(props?.globalBoard).toBe(false);
      expect(props?.loadBoard).toBeUndefined();
    },
  );

  it("is off when the build has no Neon URLs", async () => {
    const props = await loadMain("?page=leaderboard", false);
    expect(props?.globalBoard).toBe(false);
  });
});
