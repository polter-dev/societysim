// @vitest-environment jsdom
// The Research page's tabs: keyboard use, the source toggle, and hiding a tab with no data.
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { parseDeceptionMatrix } from "../data/deception";
import { fixture } from "../components/DeceptionMatrix/fixture";
import { parseLieTable, parseRoleSkill } from "../data/researchTabs";
import { ResearchView, type ResearchViewProps } from "./Research";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ls = (lies: number, moved: number, comparisons: number) => ({
  lies,
  by_type: {},
  lie_rate: lies ? 0.1 : null,
  lie_success: { exploratory: true, comparisons, moved, voters: 9, followed: 3, base_voters: 12, base_followed: 2, base_abstained: 0, share_moved: comparisons ? moved / comparisons : null, share_followed: 1 / 3, base_rate: 1 / 6 },
});
const modelsJson = (jevLies: number) => ({
  "gemini-flash": { all: { lies: { self_tag: ls(4, 6, 10), jev: ls(jevLies, 2, 4) } } },
});
const doctor = { nights: 32, saves: 12, self_protects: 13, fallbacks: 0, save_rate: 0.375, save_ci: [0.23, 0.55], chance_rate: 0.12 };
const seer = { checks: 27, wolves_found: 10, fallbacks: 0, hit_rate: 0.37, hit_ci: [0.22, 0.56], chance_rate: 0.22 };
const skillJson = { doctor: { models: { "gemini-flash": doctor } }, seer: { models: { "gemini-flash": seer } } };

let host: HTMLDivElement;
let root: Root;
const show = (props: Partial<ResearchViewProps> = {}) =>
  act(() => root.render(createElement(ResearchView, { summary: null, matrix: parseDeceptionMatrix(structuredClone(fixture)), ...props })));
beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

const tabs = () => [...host.querySelectorAll<HTMLElement>("[role=tab]")];
const labels = () => tabs().map((t) => t.textContent);
const panel = () => host.querySelector("[role=tabpanel]")!.textContent!;
const press = (el: Element, key: string) => act(() => void el.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true })));

describe("Research tabs", () => {
  it("has a tablist with all three tabs and the matrix selected first", () => {
    show({ lies: parseLieTable(modelsJson(0)), roleSkill: parseRoleSkill(skillJson) });
    expect(host.querySelector("[role=tablist]")).not.toBeNull();
    expect(labels()).toEqual(["Who fools whom", "Lie success", "Doctor & Seer"]);
    expect(tabs().map((t) => t.getAttribute("aria-selected"))).toEqual(["true", "false", "false"]);
    expect(host.querySelector(".deception-matrix")).not.toBeNull();
    expect(tabs().map((t) => t.tabIndex)).toEqual([0, -1, -1]);
    expect(host.querySelector("[role=tabpanel]")!.getAttribute("aria-labelledby")).toBe(tabs()[0].id);
  });

  it("moves with the arrow keys, Home and End, wrapping around", () => {
    show({ lies: parseLieTable(modelsJson(0)), roleSkill: parseRoleSkill(skillJson) });
    press(tabs()[0], "ArrowRight");
    expect(tabs()[1].getAttribute("aria-selected")).toBe("true");
    expect(document.activeElement).toBe(tabs()[1]);
    expect(panel()).toContain("Suspicion moved");
    press(tabs()[1], "End");
    expect(panel()).toContain("Self-protects");
    press(tabs()[2], "ArrowRight");
    expect(tabs()[0].getAttribute("aria-selected")).toBe("true");
    press(tabs()[0], "ArrowLeft");
    expect(tabs()[2].getAttribute("aria-selected")).toBe("true");
    press(tabs()[2], "Home");
    expect(host.querySelector(".deception-matrix")).not.toBeNull();
  });

  it("selects a tab on click", () => {
    show({ roleSkill: parseRoleSkill(skillJson) });
    act(() => tabs()[1].click());
    expect(panel()).toContain("Doctor");
  });

  it("Doctor & Seer shows each rate with its n, CI and chance baseline", () => {
    show({ roleSkill: parseRoleSkill(skillJson), initialTab: "skill" });
    const text = panel();
    expect(text).toContain("38% (n=32)");
    expect(text).toContain("23% to 55%");
    expect(text).toContain("12%");
    expect(text).toContain("37% (n=27)");
    expect(text).toContain("22%");
    expect(text).toContain("Gemini Flash");
  });

  it("Lie success shows n beside each rate and toggles between self-tag and Jev", () => {
    show({ lies: parseLieTable(modelsJson(3)), initialTab: "lies" });
    expect(panel()).toContain("60% (n=10)"); // self-tag: 6 moved of 10
    expect(panel()).toContain("33% (n=9)");
    const jev = [...host.querySelectorAll<HTMLButtonElement>(".deception-sources button")].find((b) => b.textContent === "Jev")!;
    act(() => jev.click());
    expect(jev.getAttribute("aria-pressed")).toBe("true");
    expect(panel()).toContain("50% (n=4)"); // jev: 2 moved of 4
    expect(panel()).not.toContain("60% (n=10)");
  });

  it("Lie success hides the toggle when the batch has no Jev labels", () => {
    show({ lies: parseLieTable(modelsJson(0)), initialTab: "lies" });
    expect(host.querySelector(".deception-sources")).toBeNull();
    expect(panel()).toContain("Jev labels haven't been run");
    expect(panel()).toContain("60% (n=10)");
  });

  it("hides the tabs whose data is missing, as an old metrics.json would leave them", () => {
    show({ lies: null, roleSkill: null });
    expect(labels()).toEqual(["Who fools whom"]);
    expect(host.querySelector(".deception-matrix")).not.toBeNull();
    show({ lies: parseLieTable(modelsJson(0)), roleSkill: null });
    expect(labels()).toEqual(["Who fools whom", "Lie success"]);
    show({ lies: null, roleSkill: parseRoleSkill(skillJson) });
    expect(labels()).toEqual(["Who fools whom", "Doctor & Seer"]);
  });

  it("falls back to the matrix when the selected tab's data goes away", () => {
    show({ roleSkill: parseRoleSkill(skillJson), initialTab: "skill" });
    expect(panel()).toContain("Self-protects");
    show({ roleSkill: null, initialTab: "skill" });
    expect(labels()).toEqual(["Who fools whom"]);
    expect(host.querySelector(".deception-matrix")).not.toBeNull();
  });

  it("says so when a role has no decisions", () => {
    show({ roleSkill: parseRoleSkill({ doctor: { models: { a: doctor } }, seer: { models: {} } }), initialTab: "skill" });
    expect(panel()).toContain("No Seer decisions in this batch.");
  });
});
