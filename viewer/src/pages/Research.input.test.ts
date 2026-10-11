// @vitest-environment jsdom
// #699: the Minimum voters box can be cleared while typing; the matrix keeps the last valid value.
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it } from "vitest";
import { parseDeceptionMatrix } from "../data/deception";
import { fixture } from "../components/DeceptionMatrix/fixture";
import { ResearchView } from "./Research";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let host: HTMLDivElement;
let root: Root;
beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  act(() => root.render(createElement(ResearchView, { summary: null, matrix: parseDeceptionMatrix(fixture) })));
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

const input = () => host.querySelector<HTMLInputElement>("input[type=number]")!;
const caption = () => host.querySelector("caption")!.textContent!;
// React tracks the input's value; set it through the native setter so onChange fires.
const type = (value: string) =>
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input(), value);
    input().dispatchEvent(new Event("input", { bubbles: true }));
  });

it("lets the box be emptied and retyped", () => {
  type("");
  expect(input().value).toBe("");
  expect(caption()).toContain("cells under 5");
  type("8");
  expect(input().value).toBe("8");
  expect(caption()).toContain("cells under 8");
});

it("ignores 0 and negatives, and restores the last valid value on blur", () => {
  type("12");
  type("0");
  expect(caption()).toContain("cells under 12");
  type("-3");
  expect(caption()).toContain("cells under 12");
  act(() => input().dispatchEvent(new FocusEvent("focusout", { bubbles: true })));
  expect(input().value).toBe("12");
});
