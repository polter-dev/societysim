import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { DEFAULT_ENABLED } from "../../audio/narration";
import { NarrationControls } from "./NarrationControls";

const render = (mode: "narrator" | "villager") =>
  renderToStaticMarkup(
    NarrationControls({
      mode,
      on: true,
      enabled: DEFAULT_ENABLED,
      onToggle: () => {},
      onCategory: () => {},
    }),
  );

describe("NarrationControls", () => {
  it("shows all categories in narrator mode", () => {
    const html = render("narrator");
    expect(html).toContain("Narrator events");
    expect(html).toContain("Wolf chat (narrator view only)");
  });
  it("hides hidden-info categories in villager mode", () => {
    const html = render("villager");
    expect(html).toContain("Speeches");
    expect(html).not.toContain("Wolf chat");
    expect(html).not.toContain("Highlights");
  });
});
