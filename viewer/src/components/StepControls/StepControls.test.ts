import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { StepControls } from "./StepControls";

const base = { pos: 2, count: 10, live: true, atLatest: false, newCount: 3, onPrev() {}, onNext() {}, onLive() {} };
const html = (o = {}) => renderToStaticMarkup(React.createElement(StepControls, { ...base, ...o }));

describe("StepControls", () => {
  it("shows label and live button when live and behind", () => {
    expect(html()).toContain("beat 3 / 10");
    expect(html()).toContain("Back to live (3 new)");
  });
  it("hides live button at the tip or when not live", () => {
    expect(html({ atLatest: true })).not.toContain("Back to live");
    expect(html({ live: false })).not.toContain("Back to live");
  });
  it("disables prev at the start", () => {
    expect(html({ pos: 0 })).toMatch(/aria-label="Previous beat" disabled/);
  });
});
