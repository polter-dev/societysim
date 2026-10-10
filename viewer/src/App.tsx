import { useEffect, useState } from "react";
import { parseLog, type ParsedLog } from "./data/parse";
import { fetchLog } from "./data/source";
import { deriveScene, type Mode } from "./scene/deriveScene";

// Bare-bones debug UI: the real table, chat and scrubber come in T4.2-T4.8.
export default function App() {
  const [log, setLog] = useState<ParsedLog | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [idx, setIdx] = useState(0);
  const [mode, setMode] = useState<Mode>("narrator");

  const show = (parsed: ParsedLog) => {
    setLog(parsed);
    setLoadError(null);
    setIdx(0);
  };

  useEffect(() => {
    const url = new URLSearchParams(location.search).get("log");
    if (url) fetchLog(url).then(show, (err: Error) => setLoadError(err.message));
  }, []);

  const onFile = async (file: File | undefined) => {
    if (file) show(parseLog(await file.text()));
  };

  const scene = log?.events.length ? deriveScene(log.events, idx, mode) : null;

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: 16, maxWidth: 960 }}>
      <h1>SocietySim viewer</h1>
      <p>
        <input type="file" accept=".jsonl" onChange={(e) => onFile(e.target.files?.[0])} /> or
        open with <code>?log=&lt;url&gt;</code>
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
      {log && scene && (
        <>
          <p>
            <label>
              View{" "}
              <select value={mode} onChange={(e) => setMode(e.target.value as Mode)}>
                <option value="narrator">Narrator</option>
                <option value="villager">Villager</option>
              </select>
            </label>{" "}
            <label>
              Event {scene.seq}{" "}
              <input
                type="range"
                min={0}
                max={log.events.length - 1}
                value={idx}
                onChange={(e) => setIdx(Number(e.target.value))}
              />
            </label>
          </p>
          <p>
            Day {scene.day}, {scene.phase}
            {scene.winner && ` | winner: ${scene.winner}`}
          </p>
          <ul>
            {scene.players.map((p) => (
              <li key={p.id} style={{ color: p.color, opacity: p.alive ? 1 : 0.5 }}>
                {p.name} ({p.id}, {p.model}) {p.role ?? "?"}
                {p.id === scene.speaker && " (speaking)"}
                {!p.alive && " (dead)"}
              </li>
            ))}
          </ul>
          <p>
            Tally:{" "}
            {Object.entries(scene.tally)
              .map(([id, n]) => `${id}: ${n}`)
              .join(", ") || "none"}
          </p>
          <pre style={{ whiteSpace: "pre-wrap" }}>
            {scene.current ? JSON.stringify(scene.current, null, 2) : "(hidden in this view)"}
          </pre>
        </>
      )}
    </main>
  );
}
