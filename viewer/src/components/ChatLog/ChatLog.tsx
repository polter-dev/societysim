import { useEffect, useRef } from "react";
import type { Event, Phase } from "../../types/events";
import type { Scene } from "../../scene/deriveScene";
import { seatName } from "../../scene/seatName";
import { SpeechText } from "../Speech/Speech";

const PHASE_LABEL: Record<Phase, string> = { setup: "Setup", night: "Night", day: "Day", vote: "Vote, day" };

/** Chat entries grouped by (day, phase), in log order. wolf_chat is only in scene.log in narrator mode. */
export function chatGroups(scene: Scene): { key: string; title: string; entries: Event[] }[] {
  const groups: { key: string; title: string; entries: Event[] }[] = [];
  for (const e of scene.log) {
    if (e.type !== "statement" && e.type !== "wolf_chat") continue;
    const key = `${e.day}-${e.phase}`;
    if (groups.at(-1)?.key !== key) groups.push({ key, title: `${PHASE_LABEL[e.phase]} ${e.day}`, entries: [] });
    groups.at(-1)!.entries.push(e);
  }
  return groups;
}

/** Side chat log: public statements, plus the wolf channel when the scene holds it (narrator view). */
export function ChatLog({ scene }: { scene: Scene }) {
  const ref = useRef<HTMLElement>(null);
  // Entries stop at the current event, so following the scrubber means keeping the newest one in view.
  useEffect(() => {
    if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [scene.seq]);

  const groups = chatGroups(scene);
  const last = groups.at(-1)?.entries.at(-1);
  const byId = new Map(scene.players.map((p) => [p.id, p]));

  return (
    // tabIndex: a scrolling region must take focus so keyboard users can scroll it.
    <section ref={ref} aria-label="Chat log" tabIndex={0} style={{ maxHeight: "70vh", overflowY: "auto", padding: "0 0.5em" }}>
      {groups.length === 0 && <p>No one has spoken yet.</p>}
      {groups.map((g) => (
        <section key={g.key} aria-label={g.title}>
          <h3 style={{ position: "sticky", top: 0, margin: 0, padding: "0.25em 0", background: "Canvas", fontSize: "0.9em", zIndex: 1 }}>
            {g.title}
          </h3>
          <ol style={{ listStyle: "none", margin: 0, padding: 0 }}>
            {g.entries.map((e) => {
              const p = e.actor ? byId.get(e.actor) : undefined;
              const wolves = e.type === "wolf_chat";
              return (
                <li
                  key={e.seq}
                  data-seq={e.seq}
                  data-channel={wolves ? "wolves" : "public"}
                  aria-current={e === last ? "true" : undefined}
                  style={{
                    margin: "0.25em 0",
                    padding: "0.35em 0.5em",
                    borderLeft: `4px solid ${p?.color ?? "#888"}`,
                    borderRadius: 4,
                    background: wolves ? "#3a1518" : undefined,
                    color: wolves ? "#f6dcdc" : undefined,
                    outline: e === last ? "1px solid currentColor" : undefined,
                  }}
                >
                  {wolves && <small style={{ display: "block", fontWeight: 700 }}>Wolf channel</small>}
                  <strong>{p?.name ?? seatName(scene.players, e.actor)}:</strong> <SpeechText e={e} />
                </li>
              );
            })}
          </ol>
        </section>
      ))}
    </section>
  );
}
