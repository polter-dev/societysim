import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import fixture from "../../../../fixtures/sample_game.jsonl?raw";
import { parseLog } from "../../data/parse";
import { deriveScene } from "../../scene/deriveScene";
import { ModeToggle } from "./ModeToggle";
import { modeFromSearch, urlWithMode } from "./modeUrl";

const events = parseLog(fixture).events;

describe("ModeToggle", () => {
  it("shows the active mode and both choices", () => {
    const html = renderToStaticMarkup(ModeToggle({ mode: "villager", onChange: () => {} }));
    expect(html).toContain("View");
    expect(html).toContain('<option value="villager" selected="">Villager</option>');
    expect(html).toContain('<option value="narrator">Narrator</option>');
  });

  it("reads the mode from the URL and preserves the log URL when changing modes", () => {
    expect(modeFromSearch("")).toBe("villager");
    expect(modeFromSearch("?mode=unknown")).toBe("villager");
    expect(modeFromSearch("?mode=narrator")).toBe("narrator");
    expect(modeFromSearch("?mode=villager&log=%2Fruns%2Fgame.jsonl")).toBe("villager");

    const url = urlWithMode(
      "https://example.test/viewer/?log=%2Fruns%2Fgame.jsonl#event-9",
      "villager",
    );
    expect(url).toBe("/viewer/?log=%2Fruns%2Fgame.jsonl&mode=villager#event-9");
    expect(modeFromSearch(new URL(url, "https://example.test").search)).toBe("villager");
    expect(urlWithMode(`https://example.test${url}`, "narrator")).toContain("mode=narrator");
  });

  it("the default villager scene hides roles and private events at every fixture seq", () => {
    expect(events).toHaveLength(118);
    for (let seq = 0; seq <= 117; seq++) {
      const scene = deriveScene(events, seq, modeFromSearch(""));
      if (events[seq].type !== "game_end") {
        expect(scene.players.filter((player) => player.alive).every((player) => player.role === null)).toBe(true);
      }
      expect(scene.log.some((event) => event.type === "wolf_chat")).toBe(false);
      expect(scene.log.every((event) => event.private_reasoning === null && event.suspicion === null)).toBe(true);
      if (events[seq].type === "wolf_chat") expect(scene.current).toBeNull();
    }
    expect(deriveScene(events, 9, "narrator").log.some((event) => event.type === "wolf_chat")).toBe(true);
  });
});
