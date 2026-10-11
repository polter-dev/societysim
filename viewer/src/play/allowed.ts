// The model ids the play server allows visitors (GET /api/limits `allowed_models`, #356).

/** The list from a limits reply, or null when the server didn't send one (then nothing is filtered). */
export function allowedModels(limits: unknown): string[] | null {
  const v = (limits as { allowed_models?: unknown } | null)?.allowed_models;
  return Array.isArray(v) && v.every((x) => typeof x === "string") ? (v as string[]) : null;
}
