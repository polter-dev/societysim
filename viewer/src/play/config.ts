/** Visitor play is on only when the build has a play server URL (repo variable VITE_PLAY_API_URL). */
export const playEnabled = (): boolean => Boolean(((import.meta.env.VITE_PLAY_API_URL as string | undefined) ?? "").trim());
