import { useState } from "react";
import { ModelAvatar, useModelLabel } from "../ModelAvatar/ModelAvatar";
import { modelName } from "../../data/modelNames";
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
      <div role="tablist" aria-label="Leaderboard" className="global-leaderboard__tablist">
        {BOARD_TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            className="global-leaderboard__tab"
            aria-selected={t.id === tab}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>
      {error && <p role="alert">Couldn't refresh the leaderboard right now.</p>}
      {!board ? (
        !error && <p className="global-leaderboard__status">Loading the leaderboard…</p>
      ) : board.failed.includes(tab) ? (
        <p role="alert">Couldn't load the {tab} right now.</p>
      ) : tab === "detectors" ? (
        <div className="global-leaderboard__scroll" style={{ overflowX: "auto", maxWidth: "100%" }}>
          <Table rows={board.detectors} columns={DETECTOR_COLUMNS} caption="Best wolf detectors, as villagers" />
        </div>
      ) : (
        <div className="global-leaderboard__scroll" style={{ overflowX: "auto", maxWidth: "100%" }}>
          <Table rows={board.liars} columns={LIAR_COLUMNS} caption="Best liars, as wolves" />
        </div>
      )}
    </section>
  );
}

function Table<T extends { model: string; ranked: boolean }>({ rows, columns, caption }: { rows: T[]; columns: Column<T>[]; caption: string }) {
  const catalogLabel = useModelLabel();
  if (rows.length === 0) return <p className="global-leaderboard__status">No finished games yet.</p>;
  return (
    <table role="tabpanel" className="global-leaderboard__table">
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
                <th key={c.label} scope="row" title={c.label === "Model" ? catalogLabel(r.model) : undefined}>
                  {c.label === "Model" && <ModelAvatar model={r.model} label={modelName(r.model)} size={28} />}{" "}
                  {c.label === "Model" ? modelName(r.model) : c.value(r)}
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
