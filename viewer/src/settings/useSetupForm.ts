import { useMemo, useState } from "react";
import {
  type GameSetupJson,
  type SetupAssets,
  type SetupState,
  defaultState,
  schemaErrors,
  setHumanSeat,
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
  reset: () => void;
}

export function useSetupForm({ catalog, schema }: SetupAssets): SetupForm {
  const [state, setState] = useState(() => defaultState(catalog));
  return useMemo(() => {
    const setup = toSetupJson(state);
    const errors = [...validate(state, catalog), ...schemaErrors(setup, schema)];
    return {
      state,
      errors,
      setup,
      json: JSON.stringify(setup, null, 2),
      valid: errors.length === 0,
      update: (patch) => setState((s) => ({ ...s, ...patch })),
      setMode: (mode) => setState((s) => setMode(s, mode)),
      setSeatModel: (seat, model) =>
        setState((s) => ({ ...s, seats: s.seats.map((m, i) => (i === seat ? model : m)) })),
      setHuman: (seat) => setState((s) => setHumanSeat(s, seat, catalog)),
      reset: () => setState(defaultState(catalog)),
    };
  }, [state, catalog, schema]);
}
