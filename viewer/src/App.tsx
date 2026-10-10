import { useCallback, useEffect, useState } from "react";
import { Arrows } from "./components/Arrows/Arrows";
import { ChatLog } from "./components/ChatLog/ChatLog";
import { Inspector } from "./components/Inspector/Inspector";
import { LabelPopups } from "./components/Labels/LabelPopups";
import { ModeToggle } from "./components/ModeToggle/ModeToggle";
import { modeFromSearch, urlWithMode } from "./components/ModeToggle/modeUrl";
import { Speech } from "./components/Speech/Speech";
import { Table } from "./components/Table/Table";
import { Tally } from "./components/Tally/Tally";
import Timeline from "./components/Timeline/Timeline";
import { fetchLabels, siblingLabelsUrl } from "./data/labels";
import { parseLog, type ParsedLog } from "./data/parse";
import { fetchLog, type GameSource } from "./data/source";
import { deriveScene, type Mode } from "./scene/deriveScene";
import type { Event, Label } from "./types/events";

export interface GameProps {
  events: Event[];
  labels: Label[];
  idx: number;
  mode: Mode;
  selected: string | null;
  onSelect: (seat: string | null) => void;
  onSeqChange: (seq: number, index: number) => void;
}

/** The composed game view. All state comes from the one scene; components only render. */
export function Game({ events, labels, idx, mode, selected, onSelect, onSeqChange }: GameProps) {
  const scene = deriveScene(events, idx, mode);
  // Closing the Inspector unmounts the focused element; send focus back to the seat it was opened for.
  const select = (seat: string | null) => {
    if (seat === null && selected) document.querySelector<HTMLElement>(`[data-seat="${selected}"] button`)?.focus();
    onSelect(seat);
  };

  return (
    <>
      <p>
        Day {scene.day}, {scene.phase}
        {scene.winner && ` | winner: ${scene.winner}`}
      </p>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 16, alignItems: "flex-start" }}>
        {/* One square box: Table sets the size, the overlays fill it and share seatPosition coordinates. */}
        <div style={{ flex: "1 1 400px", maxWidth: 640 }}>
          <LabelPopups scene={scene} labels={labels} mode={mode}>
            <Table scene={scene} selected={selected} onSelect={select} />
            <Arrows scene={scene} />
            <Speech scene={scene} />
          </LabelPopups>
        </div>
        <div style={{ flex: "1 1 260px", minWidth: 0 }}>
          <Tally scene={scene} />
          <ChatLog scene={scene} />
        </div>
      </div>
      <Inspector scene={scene} mode={mode} selected={selected} onSelect={select} />
      <Timeline events={events} labels={labels} mode={mode} onSeqChange={onSeqChange} />
    </>
  );
}

/** `source` serves `?game=<id>` links from the index page; `?log=<url>` and file upload need none. */
export default function App({ source }: { source?: GameSource } = {}) {
  const [log, setLog] = useState<ParsedLog | null>(null);
  const [labels, setLabels] = useState<Label[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [idx, setIdx] = useState(0);
  const [mode, setMode] = useState<Mode>(() => modeFromSearch(window.location.search));
  const [selected, setSelected] = useState<string | null>(null);

  const show = (parsed: ParsedLog) => {
    setLog(parsed);
    setLoadError(null);
    setSelected(null);
  };

  useEffect(() => {
    const params = new URLSearchParams(location.search);
    const game = params.get("game");
    if (game && source) {
      source.loadGame(game).then(show, (err: Error) => setLoadError(err.message));
      source.loadLabels?.(game).then((l) => setLabels(l as Label[]));
      return;
    }
    const url = params.get("log");
    if (url) fetchLog(url).then(show, (err: Error) => setLoadError(err.message));
    const labelsUrl = params.get("labels") ?? (url && siblingLabelsUrl(url));
    // data/labels.ts types schema_version as number; the generated Label narrows it to 1 | 2.
    if (labelsUrl) fetchLabels(labelsUrl).then((l) => setLabels(l as Label[]));
  }, [source]);

  useEffect(() => {
    const syncMode = () => setMode(modeFromSearch(window.location.search));
    window.addEventListener("popstate", syncMode);
    return () => window.removeEventListener("popstate", syncMode);
  }, []);

  // Stable, so Timeline's effect only fires when its seq changes.
  const onSeqChange = useCallback((_seq: number, index: number) => setIdx(index), []);

  const changeMode = (nextMode: Mode) => {
    window.history.replaceState(window.history.state, "", urlWithMode(window.location.href, nextMode));
    setMode(nextMode);
  };

  const onFile = async (file: File | undefined) => {
    if (file) show(parseLog(await file.text()));
  };

  const events = log?.events ?? [];
  // A labels file for another game (stale ?labels=, uploaded log) must not mark this one.
  const gameLabels = labels.filter((l) => l.game_id === events[0]?.game_id);

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: 16, maxWidth: 1100 }}>
      <h1>SocietySim viewer</h1>
      <p>
        <input type="file" accept=".jsonl" aria-label="Open a game log (.jsonl)" onChange={(e) => onFile(e.target.files?.[0])} /> or
        open with <code>?log=&lt;url&gt;</code>
        {log && (
          <>
            {" "}
            <ModeToggle mode={mode} onChange={changeMode} />
          </>
        )}
      </p>
      {loadError && <p role="alert">Could not load log: {loadError}</p>}
      {log && log.errors.length > 0 && (
        <details open>
          <summary role="alert">{log.errors.length} bad line(s) skipped</summary>
          <ul>
            {log.errors.map((b) => (
              <li key={b.line}>
                line {b.line}: {b.error}
              </li>
            ))}
          </ul>
        </details>
      )}
      {events.length > 0 && (
        <Game
          events={events}
          labels={gameLabels}
          idx={idx}
          mode={mode}
          selected={selected}
          onSelect={setSelected}
          onSeqChange={onSeqChange}
        />
      )}
    </main>
  );
}
