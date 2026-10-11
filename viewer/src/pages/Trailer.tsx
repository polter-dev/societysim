/*
 * ?page=trailer: a 59 s in-browser trailer (#626). One real moment from mixed_v2_10p-g0043, played on
 * the site's own table with its own narrator clips, then the batch's three numbers and a call to action.
 *
 * THESIS: the trailer is a scripted replay, not a slideshow. Every seat, bubble, arrow and vote comes from
 * the recorded log through deriveScene, so what the viewer sees is what the models did.
 * OWN-WORLD: the night forest table, gold medallions and emerald plates of the replay; one parchment card
 * for the numbers; Georgia throughout, as on the landing page.
 * STORY: ten models sit down; one wolf publicly agrees to hang his partner while his notes say why; the
 * village follows him; the numbers say lying mostly doesn't pay; come watch, or sit down yourself.
 * FIRST VIEWPORT: the table at night, fireflies, one line of title, the narrator's first words.
 * MOTION: one authored moment per beat (seats lighting up, the vote count climbing, the reveal), on
 * exponential ease-out; prefers-reduced-motion ends every animation at once (a11y.css).
 *
 * Only public events are embedded (G0043). The single private line is Hugo's seq-48 reasoning, already
 * public in the replay's narrator mode; the issue asks for it as the reveal.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { clipsForSeq, clipUrl, type Enabled, loadIndex, type NarrationIndex } from "../audio/narration";
import { type AudioLike, createPlayer } from "../audio/player";
import { Arrows } from "../components/Arrows/Arrows";
import { AvatarMedallion } from "../components/AvatarMedallion/AvatarMedallion";
import { useModelLabel } from "../components/ModelAvatar/ModelAvatar";
import { Speech } from "../components/Speech/Speech";
import { seatPosition, tintFor } from "../components/Table/Table";
import { modelName } from "../data/modelNames";
import { art } from "../illustrated/assets";
import ForestTable from "../illustrated/ForestTable";
import { deriveScene } from "../scene/deriveScene";
import type { Event, EventMeta, EventType, Phase, Visibility } from "../types/events";
import "../illustrated/scenery.css";
import "./Trailer.css";

export const GAME_ID = "mixed_v2_10p-g0043";
export const AUDIO_INDEX_URL = `${GAME_ID}.audio/index.json`;

const ev = (
  seq: number,
  t: number,
  day: number,
  phase: Phase,
  type: EventType,
  visibility: Visibility,
  actor: string | null,
  target: string | null,
  public_text: string | null,
  meta: EventMeta = {},
): Event => ({ schema_version: 3, game_id: GAME_ID, seq, t, day, phase, type, visibility, actor, target, public_text, private_reasoning: null, suspicion: null, meta });

/** The public events the trailer plays, copied from the published mixed_v2_10p-g0043.jsonl (reasoning, suspicion and usage stripped). */
export const G0043: Event[] = [
  ev(0, 0, 0, "setup", "game_start", "narrator", null, null, null, {
    seed: 2355397164,
    seats: {
      p0: { name: "Gia", model: "qwen3-235b", color: "#4f8ef7", role: "villager" },
      p1: { name: "Finn", model: "llama4-maverick", color: "#9b6bf2", role: "villager" },
      p2: { name: "Iris", model: "gpt-oss-120b", color: "#e8833a", role: "wolf" },
      p3: { name: "Ava", model: "deepseek-v3.1", color: "#2bb3b1", role: "villager" },
      p4: { name: "Elif", model: "gemini-flash-lite", color: "#fbbc04", role: "villager" },
      p5: { name: "Hugo", model: "gemini-flash", color: "#f29900", role: "wolf" },
      p6: { name: "Cleo", model: "gemma4-26b", color: "#34a853", role: "villager" },
      p7: { name: "Jude", model: "qwen3-235b", color: "#4f8ef7", role: "doctor" },
      p8: { name: "Ben", model: "llama4-maverick", color: "#9b6bf2", role: "seer" },
      p9: { name: "Dev", model: "gpt-oss-120b", color: "#e8833a", role: "villager" },
    },
  }),
  ev(1, 1, 1, "night", "phase_start", "public", null, null, "Night 1"),
  ev(14, 2, 1, "day", "phase_start", "public", null, null, "Day 1"),
  ev(48, 2, 1, "day", "statement", "public", "p5", null,
    "I understand the suspicion on p2; pushing immediately against an uncontested Seer claim without offering an alternative explanation or counter-claim definitely looks defensive. That said, let's keep our eyes open and make sure we don't tunnel vision completely before hearing everyone's thoughts.",
    { round: 2 }),
  ev(49, 2, 1, "day", "accusation", "public", "p5", "p2", null, { statement_seq: 48 }),
  ev(68, 3, 1, "vote", "phase_start", "public", null, null, "Day 1 vote"),
  ...["p0", "p1", "p2", "p3", "p4", "p5", "p6", "p7", "p8", "p9"].map((actor, i) =>
    ev(69 + i, 3, 1, "vote", "vote", "public", actor, actor === "p2" ? "p9" : "p2", null)),
  ev(79, 3, 1, "vote", "elimination", "public", null, "p2", null, { role: "wolf", tally: { p2: 9, p9: 1 } }),
];

export type Step =
  | { t: number; kind: "seq"; data: { seq: number } }
  | { t: number; kind: "narrate"; data: { seq: number } }
  /** The player's own voice (the `speech` clip) for a public statement. */
  | { t: number; kind: "speak"; data: { seq: number } }
  /** Hold the clock until the running clip ends (#627 style), so no voice is cut off. Instant when muted. */
  | { t: number; kind: "wait"; data: Record<string, never> }
  | { t: number; kind: "title"; data: { text: string | null } }
  | { t: number; kind: "seats"; data: { lit: number } }
  | { t: number; kind: "caption"; data: { text: string | null } }
  | { t: number; kind: "notes"; data: { seq: number; text: string | null } }
  | { t: number; kind: "reveal"; data: { wolves: string[] } }
  | { t: number; kind: "card"; data: { card: "stats" | "cta" | "end" | null } }
  | { t: number; kind: "stat"; data: { value: string; label: string } }
  | { t: number; kind: "end"; data: Record<string, never> };

const s = <K extends Step["kind"]>(t: number, kind: K, data: Extract<Step, { kind: K }>["data"]): Step => ({ t, kind, data }) as Step;

/**
 * The whole trailer, in seconds of muted time. Pure data: the page only plays it. With sound, the `wait`
 * after Hugo's line holds the clock until his 16 s clip ends, so the whole thing runs about 67 s.
 * The vote run-up statements (seq 50 to 65) are 14 to 28 s each; none fits, so only Hugo is voiced.
 */
export const TIMELINE: Step[] = [
  s(0, "seq", { seq: 1 }),
  s(0, "narrate", { seq: 1 }),
  s(0.8, "title", { text: "Ten AI models.\nTwo are wolves." }),
  s(4.5, "title", { text: null }),
  s(4.7, "seq", { seq: 14 }),
  s(4.7, "narrate", { seq: 14 }),
  ...Array.from({ length: 10 }, (_, i) => s(5.1 + i * 0.6, "seats", { lit: i + 1 })),
  s(11.5, "caption", { text: "Hugo, Gemini Flash, is a wolf. Nobody knows yet." }),
  s(13.5, "caption", { text: null }),
  s(13.5, "seq", { seq: 48 }),
  s(13.5, "speak", { seq: 48 }),
  s(15.5, "seq", { seq: 49 }),
  s(16, "wait", {}),
  // Hugo's private reasoning on seq 48, as the replay's narrator mode shows it. Never voiced.
  s(17.5, "notes", { seq: 48, text: "If I defend Iris blatantly, I'll be tied to her." }),
  s(22.5, "notes", { seq: 48, text: null }),
  s(22.5, "seq", { seq: 68 }),
  s(22.5, "narrate", { seq: 68 }),
  ...Array.from({ length: 10 }, (_, i) => s(25 + i * 0.38, "seq", { seq: 69 + i })),
  s(29.2, "seq", { seq: 79 }),
  s(29.2, "narrate", { seq: 79 }),
  s(32.8, "reveal", { wolves: ["p2", "p5"] }),
  s(32.8, "caption", { text: "Iris was his partner. Hugo voted with the village." }),
  s(37, "caption", { text: null }),
  s(37, "card", { card: "stats" }),
  s(37.6, "stat", { value: "356", label: "lies said out loud" }),
  s(40.4, "stat", { value: "40% vs 71%", label: "lies followed, against no lie" }),
  s(43.2, "stat", { value: "65%", label: "Gemini Flash win rate" }),
  s(47, "card", { card: "cta" }),
  s(51, "card", { card: "end" }),
  s(54, "end", {}),
];

export interface TrailerState {
  /** The log event the table shows (deriveScene on G0043). */
  seq: number;
  lit: number;
  title: string | null;
  caption: string | null;
  notes: { seq: number; text: string } | null;
  wolves: string[];
  card: "stats" | "cta" | "end" | null;
  stats: { value: string; label: string }[];
  done: boolean;
}

const INITIAL: TrailerState = { seq: 0, lit: 0, title: null, caption: null, notes: null, wolves: [], card: null, stats: [], done: false };

/** Pure: the state after steps[0..idx]. idx < 0 is the idle state before the trailer starts. */
export function trailerState(steps: Step[], idx: number): TrailerState {
  let st = INITIAL;
  for (const step of steps.slice(0, idx + 1)) {
    switch (step.kind) {
      case "seq": st = { ...st, seq: step.data.seq }; break;
      case "narrate": case "speak": case "wait": break;
      case "title": st = { ...st, title: step.data.text }; break;
      case "seats": st = { ...st, lit: step.data.lit }; break;
      case "caption": st = { ...st, caption: step.data.text }; break;
      case "notes": st = { ...st, notes: step.data.text === null ? null : { seq: step.data.seq, text: step.data.text } }; break;
      case "reveal": st = { ...st, wolves: step.data.wolves }; break;
      case "card": st = { ...st, card: step.data.card, stats: step.data.card === "stats" ? st.stats : [] }; break;
      case "stat": st = { ...st, stats: [...st.stats, step.data] }; break;
      case "end": st = { ...st, done: true }; break;
    }
  }
  return st;
}

/** Index of the last step at or before `t` seconds; -1 before the first. */
export function stepAt(steps: Step[], t: number): number {
  let i = -1;
  while (i + 1 < steps.length && steps[i + 1].t <= t) i++;
  return i;
}

/** Clips started by steps (from, to]: narrator lines and players' own voices, in step order. */
export const clipsBetween = (steps: Step[], from: number, to: number): { seq: number; category: "narrator" | "speech" }[] =>
  steps.slice(from + 1, to + 1).flatMap((step): { seq: number; category: "narrator" | "speech" }[] =>
    step.kind === "narrate" ? [{ seq: step.data.seq, category: "narrator" }] : step.kind === "speak" ? [{ seq: step.data.seq, category: "speech" }] : []);

/** Narrator lines and public speech only; wolf chat and highlights would leak hidden roles. */
const PUBLIC_VOICES: Enabled = { narrator: true, speech: true, wolf_chat: false, highlight: false };

const seqIndex = (seq: number) => G0043.findIndex((e) => e.seq === seq);

/**
 * Runs the timeline against the wall clock. `run` increments to (re)start; 0 is idle.
 * `apply` gets each batch of same-instant steps as it lands (to start its clips); a batch with a `wait`
 * step then holds the clock until `whenIdle` fires, and later steps shift by the time held.
 */
function useTimeline(steps: Step[], run: number, apply: (from: number, to: number) => void, whenIdle: (cb: () => void) => () => void) {
  const [idx, setIdx] = useState(-1);
  useEffect(() => {
    if (!run) return;
    setIdx(-1);
    let i = -1;
    let timer = 0;
    let cancelWait = () => {};
    let held = 0;
    const t0 = performance.now();
    const fire = () => {
      // Steps sharing a time land in one batch, so it is never half-applied.
      const from = i;
      const t = steps[i + 1].t;
      while (i + 1 < steps.length && steps[i + 1].t === t) i++;
      setIdx(i);
      apply(from, i);
      if (!steps.slice(from + 1, i + 1).some((step) => step.kind === "wait")) return schedule();
      const heldAt = performance.now();
      cancelWait = whenIdle(() => { held += performance.now() - heldAt; schedule(); });
    };
    const schedule = () => {
      if (i + 1 < steps.length) timer = window.setTimeout(fire, Math.max(0, steps[i + 1].t * 1000 + held - (performance.now() - t0)));
    };
    schedule();
    return () => { clearTimeout(timer); cancelWait(); };
  }, [steps, run, apply, whenIdle]);
  return idx;
}

const roleLabel = (role: string) => role[0].toUpperCase() + role.slice(1);

/** One frame of the trailer: the table and every overlay for `state`. Pure render. */
export function TrailerFrame({ state, paused = false }: { state: TrailerState; paused?: boolean }) {
  const scene = deriveScene(G0043, seqIndex(state.seq), "villager");
  const label = useModelLabel();
  const tint = tintFor(scene.phase);
  const phaseText = scene.phase === "setup" ? null : scene.phase === "night" ? `Night ${scene.day}` : scene.phase === "vote" ? `Day ${scene.day} · Vote` : `Day ${scene.day}`;
  return (
    <div className={`trailer-stage ${tint}`} data-veiled={state.card !== null || undefined}>
      <ForestTable paused={paused} />
      {phaseText && <div className="day-counter" key={phaseText} aria-hidden="true"><img src={art(`new/${tint === "night" ? "night_counter 1.png" : "day_counter-v2.png"}`)} alt="" /><span>{phaseText}</span></div>}
      <div className="trailer-table">
        <section className="round-table" aria-label="The table" data-tint={tint}>
          <ul>
            {scene.players.map((p, i) => {
              const { x, y } = seatPosition(i, scene.players.length);
              const wolf = state.wolves.includes(p.id);
              const role = wolf ? "Wolf" : p.role ? roleLabel(p.role) : null;
              const votes = scene.tally[p.id] ?? 0;
              return (
                <li key={p.id} className="table-seat" data-seat={p.id} data-alive={p.alive} data-revealed={p.revealed || wolf} data-speaking={p.id === scene.speaker || undefined} data-lit={i < state.lit} data-plate={y < 35 ? "above" : "below"} style={{ left: `${x}%`, top: `${y}%` }}>
                  <span className="trailer-seat">
                    <AvatarMedallion model={p.model} label={label(p.model)} alive={p.alive} size={96} />
                    {votes > 0 && <span className="trailer-votes" aria-label={`${votes} votes`}>{votes}</span>}
                    <span className="seat-plate">
                      <span className="seat-name"><strong>{p.name}</strong>{role && <span className="seat-role">{role}</span>}</span>
                      <span className="seat-model" title={label(p.model)}>{modelName(p.model)}</span>
                    </span>
                  </span>
                </li>
              );
            })}
          </ul>
        </section>
        <Arrows scene={scene} />
        <Speech scene={scene} playing />
      </div>
      {state.notes && (
        <aside className="trailer-notes" aria-label="Hugo's private notes">
          <span className="trailer-notes-head"><img src={art("inspector/10_notes.png")} alt="" /><span>Private notes · Hugo</span></span>
          <blockquote>{state.notes.text}</blockquote>
        </aside>
      )}
      {state.title && <h2 className="trailer-title">{state.title.split("\n").map((line, i) => <span key={i}>{line}</span>)}</h2>}
      {state.caption && <p className="trailer-caption" key={state.caption}>{state.caption}</p>}
      {state.card === "stats" && (
        <section className="trailer-card trailer-stats" aria-label="The numbers">
          <h2>100 games. Every lie labelled.</h2>
          <dl>{state.stats.map((st) => <div key={st.label}><dd>{st.value}</dd><dt>{st.label}</dt></div>)}</dl>
        </section>
      )}
      {state.card === "cta" && (
        <section className="trailer-card trailer-cta" aria-label="Call to action">
          <h2>Watch every game.<br />Play a seat against the models.</h2>
          <p>society-sim.us</p>
        </section>
      )}
      {state.card === "end" && (
        <section className="trailer-end" aria-label="Society Sim">
          <h2>Society Sim</h2>
          <p>Where AI becomes society.</p>
          <p className="trailer-end-url">society-sim.us</p>
        </section>
      )}
    </div>
  );
}

export default function TrailerPage({ audioIndexUrl = AUDIO_INDEX_URL }: { audioIndexUrl?: string }) {
  const [run, setRun] = useState(0);
  const [sound, setSound] = useState(false);
  const [index, setIndex] = useState<NarrationIndex | null>(null);
  const [paused, setPaused] = useState(false);
  const soundRef = useRef(sound);
  soundRef.current = sound;
  const indexRef = useRef(index);
  indexRef.current = index;
  const player = useMemo(() => createPlayer(() => new Audio() as AudioLike, (c) => clipUrl(audioIndexUrl, c)), [audioIndexUrl]);
  // Each batch of steps starts its clips, once, while sound is on. Only public categories: the notes card stays silent.
  const apply = useCallback((from: number, to: number) => {
    const clips = clipsBetween(TIMELINE, from, to);
    if (soundRef.current && clips.length) player.play(clips.flatMap((c) => clipsForSeq(indexRef.current, c.seq, "villager", PUBLIC_VOICES).filter((clip) => clip.category === c.category)));
  }, [player]);
  const idx = useTimeline(TIMELINE, run, apply, player.whenIdle);
  const state = useMemo(() => trailerState(TIMELINE, idx), [idx]);

  useEffect(() => { loadIndex(audioIndexUrl).then(setIndex); }, [audioIndexUrl]);
  useEffect(() => { player.stop(); }, [run, player]);
  useEffect(() => () => player.stop(), [player]);

  const start = (withSound: boolean) => { setSound(withSound); setRun((r) => r + 1); };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.code !== "Space" || (e.target as HTMLElement | null)?.closest?.("button, a, input")) return;
      e.preventDefault();
      start(soundRef.current);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const idle = run === 0;
  return (
    <main className={`trailer ${paused ? "still" : ""}`} data-playing={!idle && !state.done ? "" : undefined}>
      <TrailerFrame state={state} paused={paused} />
      <p className="visually-hidden" role="status">{state.title ?? state.caption ?? ""}</p>
      {idle && (
        <div className="trailer-start">
          <h1>Society Sim trailer</h1>
          <p>Ten AI models play Werewolf. One real game, one minute.</p>
          <div>
            <button type="button" className="trailer-button trailer-button--primary" onClick={() => start(true)}>Play with sound</button>
            <button type="button" className="trailer-button" onClick={() => start(false)}>Play muted</button>
          </div>
          <small>Space starts it too.</small>
        </div>
      )}
      <div className="trailer-controls">
        {!idle && <button type="button" className="trailer-button" onClick={() => start(sound)}>Replay</button>}
        {!idle && <button type="button" className="trailer-button" aria-pressed={sound} onClick={() => { if (sound) player.stop(); setSound(!sound); }}>{sound ? "Sound on" : "Sound off"}</button>}
        <button type="button" className="trailer-button" aria-pressed={paused} onClick={() => setPaused(!paused)}>{paused ? "Resume ambience" : "Pause ambience"}</button>
      </div>
    </main>
  );
}
