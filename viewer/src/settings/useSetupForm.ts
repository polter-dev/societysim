import { useEffect, useMemo, useState } from "react";
import {
  type GameSetupJson,
  type ModelsCatalog,
  type SetupAssets,
  type SetupState,
  defaultState,
  joinAs,
  restrictCatalog,
  schemaErrors,
  setHumanSeat,
  setNPlayers,
  setMode,
  toSetupJson,
  validate,
} from "./setup";

export interface SetupForm {
  state: SetupState;
  errors: string[];
  setup: GameSetupJson;
  json: string;
  valid: boolean;
  update: (patch: Partial<SetupState>) => void;
  setMode: (mode: SetupState["mode"]) => void;
  setSeatModel: (seat: number, model: string) => void;
  setHuman: (seat: number | null) => void;
  /** The catalog the form offers (narrowed to the allowed models). */
  catalog: ModelsCatalog;
  reset: () => void;
}

export interface VisitorOptions {
  /** True: the person plays (one human seat); false: they watch (none). Omit outside visitor play. */
  play?: boolean;
  /** Model ids the server allows (/api/limits). The catalog and defaults are narrowed to them. */
  allowed?: string[] | null;
}

export function useSetupForm(assets: SetupAssets, { play, allowed }: VisitorOptions = {}): SetupForm {
  const { schema } = assets;
  const catalog = useMemo(() => restrictCatalog(assets.catalog, allowed), [assets.catalog, allowed]);
  const [state, setState] = useState(() => {
    const d = defaultState(catalog);
    return play === undefined ? d : joinAs(d, play, catalog);
  });
  useEffect(() => {
    if (play !== undefined) setState((s) => joinAs(s, play, catalog));
  }, [play, catalog]);
  return useMemo(() => {
    const setup = toSetupJson(state);
    const errors = [...validate(state, catalog), ...schemaErrors(setup, schema)];
    return {
      state,
      catalog,
      errors,
      setup,
      json: JSON.stringify(setup, null, 2),
      valid: errors.length === 0,
      update: (patch) =>
        setState((s) => {
          const { nPlayers, ...rest } = patch;
          const next = { ...s, ...rest };
          return nPlayers === undefined ? next : setNPlayers(next, nPlayers, catalog);
        }),
      setMode: (mode) => setState((s) => setMode(s, mode)),
      setSeatModel: (seat, model) =>
        setState((s) => ({ ...s, seats: s.seats.map((m, i) => (i === seat ? model : m)) })),
      setHuman: (seat) => setState((s) => setHumanSeat(s, seat, catalog)),
      reset: () => setState(play === undefined ? defaultState(catalog) : joinAs(defaultState(catalog), play, catalog)),
    };
  }, [state, catalog, schema, play]);
}
