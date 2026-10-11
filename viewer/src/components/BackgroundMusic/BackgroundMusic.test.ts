// @vitest-environment jsdom
import { act, createElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MUTED_KEY, POSITION_KEY, musicCredit } from "../../audio/music";
import BackgroundMusic from "./BackgroundMusic";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

class FakeAudio {
  static all: FakeAudio[] = [];
  /** Like a browser before the visitor has clicked: play() is rejected. */
  static blocked = true;
  src = "";
  loop = false;
  volume = 1;
  preload = "";
  currentTime = 0;
  duration = 100;
  plays = 0;
  pauses = 0;
  private handlers: Record<string, (() => void)[]> = {};
  constructor() {
    FakeAudio.all.push(this);
  }
  addEventListener(type: string, fn: () => void) {
    (this.handlers[type] ??= []).push(fn);
  }
  removeAttribute() {}
  play() {
    this.plays++;
    return FakeAudio.blocked ? Promise.reject(new Error("NotAllowedError")) : Promise.resolve();
  }
  pause() {
    this.pauses++;
  }
  fire(type: string) {
    (this.handlers[type] ?? []).forEach((fn) => fn());
  }
}

let root: Root;
let host: HTMLElement;
const audio = () => FakeAudio.all[0];
const button = () => host.querySelector<HTMLButtonElement>("button.music-toggle");
const mount = async () => {
  await act(async () => root.render(createElement(BackgroundMusic)));
};
const loaded = () => act(async () => audio().fire("canplay"));

beforeEach(() => {
  FakeAudio.all = [];
  FakeAudio.blocked = true;
  localStorage.clear();
  sessionStorage.clear();
  vi.stubGlobal("Audio", FakeAudio);
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  vi.unstubAllGlobals();
});

describe("BackgroundMusic", () => {
  it("loops the shipped track quietly from ./music/background.mp3", async () => {
    await mount();
    expect(audio().loop).toBe(true);
    expect(audio().volume).toBe(0.25);
    expect(audio().src).toMatch(/\/music\/background\.mp3$/);
  });

  it("renders nothing when the file is missing", async () => {
    await mount();
    await act(async () => audio().fire("error"));
    expect(button()).toBeNull();
    expect(host.innerHTML).toBe("");
    expect(document.documentElement.hasAttribute("data-music")).toBe(false);
  });

  it("shows an accessible mute button once the file loads", async () => {
    await mount();
    expect(button()).toBeNull(); // not before it has loaded
    await loaded();
    expect(button()!.getAttribute("aria-label")).toBe("Mute music");
    expect(button()!.getAttribute("aria-pressed")).toBe("false");
    expect(document.documentElement.hasAttribute("data-music")).toBe(true);
  });

  it("starts on the first click or keypress, and not before", async () => {
    await mount();
    await loaded();
    const tries = audio().plays; // the attempt at load: blocked, so nothing is heard yet
    expect(tries).toBeGreaterThan(0);
    FakeAudio.blocked = false; // the visitor's gesture makes playback allowed
    await act(async () => void window.dispatchEvent(new Event("keydown")));
    expect(audio().plays).toBe(tries + 1);
    await act(async () => void window.dispatchEvent(new Event("pointerdown")));
    expect(audio().plays).toBe(tries + 1); // playing now: the one-time listener is gone
  });

  it("keeps waiting for a gesture while autoplay is still blocked", async () => {
    await mount();
    await loaded();
    const tries = audio().plays;
    await act(async () => void window.dispatchEvent(new Event("pointerdown"))); // rejected
    FakeAudio.blocked = false;
    await act(async () => void window.dispatchEvent(new Event("pointerdown")));
    expect(audio().plays).toBe(tries + 2);
  });

  it("mute pauses, persists, and unmute plays again", async () => {
    await mount();
    await loaded();
    await act(async () => button()!.click());
    expect(button()!.getAttribute("aria-pressed")).toBe("true");
    expect(button()!.getAttribute("aria-label")).toBe("Unmute music");
    expect(localStorage.getItem(MUTED_KEY)).toBe("1");
    expect(audio().pauses).toBeGreaterThan(0);
    const plays = audio().plays;
    await act(async () => button()!.click());
    expect(localStorage.getItem(MUTED_KEY)).toBe("0");
    expect(audio().plays).toBeGreaterThan(plays);
  });

  it("stays silent and muted on the next page when the visitor muted", async () => {
    localStorage.setItem(MUTED_KEY, "1");
    await mount();
    await loaded();
    expect(button()!.getAttribute("aria-pressed")).toBe("true");
    await act(async () => void window.dispatchEvent(new Event("pointerdown")));
    expect(audio().plays).toBe(0);
  });

  it("resumes from the position saved on the previous page", async () => {
    sessionStorage.setItem(POSITION_KEY, "42.5");
    await mount();
    await act(async () => audio().fire("loadedmetadata"));
    expect(audio().currentTime).toBe(42.5);
  });

  it("saves its position when the page goes away", async () => {
    await mount();
    audio().currentTime = 17;
    await act(async () => void window.dispatchEvent(new Event("pagehide")));
    expect(sessionStorage.getItem(POSITION_KEY)).toBe("17");
  });

  it("does not overwrite the saved position before the track has started", async () => {
    sessionStorage.setItem(POSITION_KEY, "42.5");
    await mount();
    await act(async () => root.unmount()); // a page that unloads before playback began
    expect(sessionStorage.getItem(POSITION_KEY)).toBe("42.5");
    root = createRoot(host);
  });

  it("still works when storage throws", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    await mount();
    await loaded();
    await act(async () => button()!.click());
    expect(button()!.getAttribute("aria-pressed")).toBe("true");
    vi.restoreAllMocks();
  });
});

describe("music credit", () => {
  it("reads 'Music: <title> by <artist>'", () => {
    expect(musicCredit()).toMatch(/^Music: .+ by .+$/);
  });
});
