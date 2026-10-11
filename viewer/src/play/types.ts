// Types mirror schema/play_api.schema.json (x-contract-version 1).
import type { GameSetupJson } from "../settings/setup";

export type Role = "wolf" | "seer" | "doctor" | "villager";
export type Phase = "setup" | "night" | "day" | "vote";
export type PendingKind = "statement" | "vote" | "wolf_chat" | "lone_wolf" | "seer" | "doctor";

export interface StartGame {
  setup: GameSetupJson;
  turnstile_token: string;
  watch_only: boolean;
}
export interface StartedGame {
  game_id: string;
  seat: string | null;
  seat_token: string | null;
  watch_token?: string | null;
}
export interface PendingAction {
  kind: PendingKind;
  targets: string[];
  others: string[];
  /** Deadline as epoch seconds (the server's time.time()). */
  deadline: number;
}
export interface Player {
  seat: string;
  name: string;
  alive: boolean;
  /** Public model id (or "human"); absent until the play API sends it. */
  model?: string;
}
export interface Me {
  game_id: string;
  status: "running" | "finished" | "forfeit" | "failed";
  winner: "wolves" | "villagers" | "draw" | null;
  seat: string;
  name: string;
  role: Role;
  alive: boolean;
  day: number;
  phase: Phase;
  observation: string | null;
  pending_action: PendingAction | null;
  players: Player[];
}
export interface Limits {
  play_enabled: boolean;
  remaining_today: number;
}

export interface SuspicionEntry {
  player: string;
  p: number;
}
/** Optional for a visitor (HumanReasoning). Players left out of suspicion are logged as not rated. */
export interface PrivateReasoning {
  thoughts?: string;
  suspicion?: SuspicionEntry[];
}
export type ActionBody =
  | {
      private_reasoning?: PrivateReasoning;
      action: {
        text: string;
        accuse: string[];
        claim_role: Role | null;
        seer_report: { target: string; result: "wolf" | "not_wolf"; night: number } | null;
      };
    }
  | { private_reasoning?: PrivateReasoning; action: { vote: string } }
  | { private_reasoning?: PrivateReasoning; action: { message: string; proposed_target: string } }
  | { private_reasoning?: PrivateReasoning; action: { target: string } };
