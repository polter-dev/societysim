import { describe, expect, it, vi } from "vitest";
import { clipsForSeq, clipsForSeqRange, clipUrl, DEFAULT_ENABLED, loadIndex, parseIndex } from "./narration";
import { type AudioLike, createPlayer } from "./player";

const clip = (seq: number, category: string, file: string) => ({
  seq,
  category,
  speaker: "x",
  voice: "v",
  text: file,
  file,
});
const raw = {
  game_id: "g1",
  clips: [
    clip(3, "narrator", "a.mp3"),
    clip(3, "speech", "b.mp3"),
    clip(3, "wolf_chat", "c.mp3"),
    clip(3, "highlight", "d.mp3"),
    clip(4, "narrator", "e.mp3"),
    { seq: "x", category: "narrator", file: "bad.mp3" },
    clip(5, "bogus", "bad2.mp3"),
  ],
};
const all = { narrator: true, speech: true, wolf_chat: true, highlight: true };

describe("parseIndex", () => {
  it("keeps valid clips and drops bad ones", () => {
    const idx = parseIndex(raw);
    expect(idx?.gameId).toBe("g1");
    expect(idx?.clips.map((c) => c.file)).toEqual(["a.mp3", "b.mp3", "c.mp3", "d.mp3", "e.mp3"]);
  });
  it("returns null for junk", () => {
    expect(parseIndex(null)).toBeNull();
    expect(parseIndex({ clips: "no" })).toBeNull();
  });
  it("treats a missing index as no narration", async () => {
    expect(
      await loadIndex("x/index.json", async () => new Response("", { status: 404 })),
    ).toBeNull();
    expect(
      await loadIndex("x/index.json", async () => {
        throw new Error("net");
      }),
    ).toBeNull();
    expect(clipsForSeq(null, 3, "narrator")).toEqual([]);
  });
});

describe("clipsForSeq", () => {
  const idx = parseIndex(raw);
  it("defaults to narrator events only", () => {
    expect(clipsForSeq(idx, 3, "narrator").map((c) => c.file)).toEqual(["a.mp3"]);
  });
  it("narrator mode plays everything enabled, in index order", () => {
    expect(clipsForSeq(idx, 3, "narrator", all).map((c) => c.file)).toEqual([
      "a.mp3",
      "b.mp3",
      "c.mp3",
      "d.mp3",
    ]);
  });
  it("villager mode never plays wolf_chat or highlight", () => {
    expect(clipsForSeq(idx, 3, "villager", all).map((c) => c.file)).toEqual(["a.mp3", "b.mp3"]);
    expect(clipsForSeq(idx, 3, "villager", DEFAULT_ENABLED).map((c) => c.file)).toEqual(["a.mp3"]);
  });
  it("builds clip urls beside the index", () => {
    expect(clipUrl("/runs/g1.audio/index.json", idx!.clips[0])).toBe("/runs/g1.audio/a.mp3");
  });
});

function fakeAudio() {
  const made: AudioLike[] = [];
  const make = () => {
    const a: AudioLike = {
      src: "",
      onended: null,
      onerror: null,
      play: vi.fn(async () => {}),
      pause: vi.fn(),
    };
    made.push(a);
    return a;
  };
  return { made, make };
}

describe("createPlayer", () => {
  const idx = parseIndex(raw)!;
  const urlOf = (c: { file: string }) => `/u/${c.file}`;

  it("plays clips in order on one element", () => {
    const { made, make } = fakeAudio();
    const p = createPlayer(make, urlOf as never);
    p.play(clipsForSeq(idx, 3, "narrator", all));
    expect(made).toHaveLength(1);
    expect(made[0].src).toBe("/u/a.mp3");
    made[0].onended?.();
    expect(made[0].src).toBe("/u/b.mp3");
    made[0].onerror?.();
    expect(made[0].src).toBe("/u/c.mp3");
  });

  it("stepping stops the previous clip and its queue", () => {
    const { made, make } = fakeAudio();
    const p = createPlayer(make, urlOf as never);
    p.play(clipsForSeq(idx, 3, "narrator", all));
    const first = made[0];
    const staleEnded = first.onended;
    p.play(clipsForSeq(idx, 4, "narrator", all));
    expect(first.pause).toHaveBeenCalled();
    expect(made).toHaveLength(1);
    expect(first.src).toBe("/u/e.mp3");
    staleEnded?.(); // a late event from the old queue does nothing
    expect(first.src).toBe("/u/e.mp3");
  });

  it("extend queues new clips behind the playing one, resumes a finished run, and restarts on a different list", () => {
    const { made, make } = fakeAudio();
    const p = createPlayer(make, urlOf as never);
    const [a, b, e] = [clipsForSeq(idx, 3, "narrator", all)[0], clipsForSeq(idx, 3, "narrator", all)[1], clipsForSeq(idx, 4, "narrator", all)[0]];
    p.play([a]);
    p.extend([a, b]);
    expect(made[0].play).toHaveBeenCalledTimes(1); // a keeps playing
    made[0].onended?.();
    expect(made[0].src).toBe("/u/b.mp3");
    made[0].onended?.(); // run over
    const idle = vi.fn();
    p.whenIdle(idle);
    expect(idle).toHaveBeenCalled();
    p.extend([a, b, e]);
    expect(made[0].src).toBe("/u/e.mp3"); // only the new clip
    p.extend([e]);
    expect(made[0].play).toHaveBeenCalledTimes(4); // not a prefix: restart
  });

  it("stop pauses and plays nothing further", () => {
    const { made, make } = fakeAudio();
    const p = createPlayer(make, urlOf as never);
    p.play(clipsForSeq(idx, 3, "narrator", all));
    p.stop();
    expect(made[0].pause).toHaveBeenCalled();
    expect(made[0].onended).toBeNull();
  });
});

describe("clipsForSeqRange", () => {
  const idx = parseIndex(raw);
  it("returns clips from every seq in the range, in index order", () => {
    expect(clipsForSeqRange(idx, 3, 4, "narrator").map((c) => c.file)).toEqual(["a.mp3", "e.mp3"]);
    expect(clipsForSeqRange(idx, 4, 9, "narrator").map((c) => c.file)).toEqual(["e.mp3"]);
    expect(clipsForSeqRange(idx, 0, 2, "narrator")).toEqual([]);
  });
  it("still hides wolf_chat and highlight in villager mode", () => {
    const files = clipsForSeqRange(idx, 3, 4, "villager", all).map((c) => c.file);
    expect(files).toEqual(["a.mp3", "b.mp3", "e.mp3"]);
  });
  it("handles a null index", () => {
    expect(clipsForSeqRange(null, 0, 9, "narrator")).toEqual([]);
  });
});
