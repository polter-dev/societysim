import { useState } from "react";
import { BOARD_TABS, DETECTOR_COLUMNS, LIAR_COLUMNS, type BoardTab, type Column, type GlobalBoard } from "../../data/globalBoard";

export interface GlobalLeaderboardProps {
  board: GlobalBoard | null;
  error?: string | null;
  initialTab?: BoardTab;
}

/**
 * Unstyled global leaderboard with two tabs. Feed it from `useGlobalBoard()`. Rows below the
 * games threshold carry `data-ranked="false"`; style via the data attributes.
 */
export function GlobalLeaderboard({ board, error = null, initialTab = "detectors" }: GlobalLeaderboardProps) {
  const [tab, setTab] = useState<BoardTab>(initialTab);
  return (
    <section className="global-leaderboard" data-tab={tab}>
      <div role="tablist" aria-label="Leaderboard">
        {BOARD_TABS.map((t) => (
          <button key={t.id} type="button" role="tab" aria-selected={t.id === tab} onClick={() => setTab(t.id)}>
            {t.label}
          </button>
        ))}
      </div>
      {error && <p role="alert">Couldn't refresh the leaderboard: {error}</p>}
      {!board ? (
        !error && <p>Loading the leaderboard…</p>
      ) : tab === "detectors" ? (
        <Table rows={board.detectors} columns={DETECTOR_COLUMNS} caption="Best wolf detectors, as villagers" />
      ) : (
        <Table rows={board.liars} columns={LIAR_COLUMNS} caption="Best liars, as wolves" />
      )}
    </section>
  );
}

function Table<T extends { model: string; ranked: boolean }>({ rows, columns, caption }: { rows: T[]; columns: Column<T>[]; caption: string }) {
  if (rows.length === 0) return <p>No finished games yet.</p>;
  return (
    <table role="tabpanel">
      <caption>{caption}. Models with fewer than 5 games are listed last, unranked.</caption>
      <thead>
        <tr>
          {columns.map((c) => (
            <th key={c.label} scope="col" title={c.title || undefined}>
              {c.label}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.model} data-ranked={r.ranked}>
            {columns.map((c, i) =>
              i === 0 ? (
                <th key={c.label} scope="row">
                  {c.value(r)}
                </th>
              ) : (
                <td key={c.label}>{c.value(r)}</td>
              ),
            )}
          </tr>
        ))}
      </tbody>
    </table>
  );
}
