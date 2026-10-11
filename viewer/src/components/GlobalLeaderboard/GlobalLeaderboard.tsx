import { useId, useState, type KeyboardEvent } from "react";
import { ModelAvatar, useModelLabel } from "../ModelAvatar/ModelAvatar";
import { modelName } from "../../data/modelNames";
import { BOARD_TABS, DETECTOR_COLUMNS, liarColumns, type BoardTab, type Column, type GlobalBoard } from "../../data/globalBoard";

export interface GlobalLeaderboardProps {
  board: GlobalBoard | null;
  error?: string | null;
  initialTab?: BoardTab;
}

/**
 * Global leaderboard with two tabs. Feed it from `useGlobalBoard()`. Rows below the games
 * threshold carry `data-ranked="false"`. Styled by pages/Leaderboard.css.
 */
export function GlobalLeaderboard({ board, error = null, initialTab = "detectors" }: GlobalLeaderboardProps) {
  const [tab, setTab] = useState<BoardTab>(initialTab);
  const id = useId();
  const tabId = (t: BoardTab) => `${id}-${t}`;
  // WAI-ARIA tabs: one tab stop; the arrow keys, Home and End move and select.
  const onKeyDown = (e: KeyboardEvent) => {
    const i = BOARD_TABS.findIndex((t) => t.id === tab);
    const n = BOARD_TABS.length;
    const next = ({ ArrowRight: (i + 1) % n, ArrowLeft: (i - 1 + n) % n, Home: 0, End: n - 1 } as Record<string, number>)[e.key];
    if (next === undefined) return;
    e.preventDefault();
    setTab(BOARD_TABS[next].id);
    document.getElementById(tabId(BOARD_TABS[next].id))?.focus();
  };
  const body = !board ? (
    !error && <p className="global-leaderboard__status">Loading the leaderboard…</p>
  ) : board.failed.includes(tab) ? (
    <p role="alert">Couldn't load the {tab} right now.</p>
  ) : tab === "detectors" ? (
    <Table rows={board.detectors} columns={DETECTOR_COLUMNS} caption="Best wolf detectors, as villagers" />
  ) : (
    <Table rows={board.liars} columns={liarColumns(board.liars)} caption="Best liars, as wolves" />
  );
  return (
    <section className="global-leaderboard" data-tab={tab}>
      <div role="tablist" aria-label="Leaderboard" className="global-leaderboard__tablist" onKeyDown={onKeyDown}>
        {BOARD_TABS.map((t) => (
          <button
            key={t.id}
            id={tabId(t.id)}
            type="button"
            role="tab"
            className="global-leaderboard__tab"
            aria-selected={t.id === tab}
            aria-controls={`${id}-panel`}
            tabIndex={t.id === tab ? 0 : -1}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>
      {error && <p role="alert">Couldn't refresh the leaderboard right now.</p>}
      {/* The panel is also the table's scroll box, so it takes focus for keyboard scrolling. */}
      {body && (
        <div role="tabpanel" id={`${id}-panel`} aria-labelledby={tabId(tab)} tabIndex={0} className="global-leaderboard__scroll">
          {body}
        </div>
      )}
    </section>
  );
}

function Table<T extends { model: string; ranked: boolean }>({ rows, columns, caption }: { rows: T[]; columns: Column<T>[]; caption: string }) {
  const catalogLabel = useModelLabel();
  if (rows.length === 0) return <p className="global-leaderboard__status">No finished games yet.</p>;
  return (
    <table className="global-leaderboard__table">
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
