import { describe, expect, it } from "vitest";
import { metrics } from "../components/ModelProfile/fixture";
import { avatarFor, loadModelProfile, parseProfile, profileFromMetrics } from "./models";

describe("profileFromMetrics", () => {
  it("picks one model's profile", () => {
    const p = profileFromMetrics(structuredClone(metrics), "gemini-flash")!;
    expect(p.model).toBe("gemini-flash");
    expect(p.fools.self_tag.map((r) => r.model)).toEqual(["deepseek-v3.1", "gpt-oss-120b"]);
    expect(p.voice).toEqual({ voice_id: "cgSgspJ2msm6clMCkdW9", fallback: false });
  });

  it("returns null for an unknown model or an older metrics.json without profiles", () => {
    expect(profileFromMetrics(metrics, "nope")).toBeNull();
    expect(profileFromMetrics(metrics, "toString")).toBeNull();
    expect(profileFromMetrics({ models: {} }, "gemini-flash")).toBeNull();
  });

  it("names the bad path when a profile is malformed", () => {
    const bad = structuredClone(metrics);
    (bad.profiles["gemini-flash"].fools as Record<string, unknown>).jev = {};
    expect(() => profileFromMetrics(bad, "gemini-flash")).toThrow("profiles.gemini-flash.fools.jev: expected list");
    expect(() => parseProfile({ model: "m", seats: 1 })).toThrow("profiles.profile.lie_rate");
  });
});

describe("loadModelProfile", () => {
  it("fetches metrics.json once and selects the model", async () => {
    const urls: string[] = [];
    const p = await loadModelProfile("https://h.test/metrics.json", "llama-4-maverick", async (u) => {
      urls.push(u);
      return metrics;
    });
    expect(urls).toEqual(["https://h.test/metrics.json"]);
    expect(p?.voice.fallback).toBe(true);
  });
});

describe("avatarFor", () => {
  it("is a placeholder hook until avatar art lands", () => {
    expect(avatarFor("gemini-flash")).toBeNull();
  });
});
