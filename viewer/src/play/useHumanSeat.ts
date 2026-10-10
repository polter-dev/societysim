import { useCallback, useEffect, useRef, useState } from "react";
import { PlayError, getMe, postAction } from "./api";
import { type Draft, buildAction, secondsLeft } from "./action";
import type { Me } from "./types";

export const POLL_MS = 2000;

/** Poll /me for the visitor's seat. Exposes the private view, the countdown and submit(). */
export function useHumanSeat(gameId: string | null, pollMs = POLL_MS) {
  const [me, setMe] = useState<Me | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [errors, setErrors] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const inflight = useRef(false);

  const refresh = useCallback(async () => {
    if (!gameId || inflight.current) return;
    inflight.current = true;
    try {
      setMe(await getMe(gameId));
      setError(null);
    } catch (e) {
      setError(e instanceof PlayError ? e.message : "Something went wrong.");
    } finally {
      inflight.current = false;
    }
  }, [gameId]);

  const over = me !== null && me.status !== "running";
  useEffect(() => {
    if (!gameId || over) return;
    void refresh();
    const t = setInterval(() => void refresh(), pollMs);
    return () => clearInterval(t);
  }, [gameId, over, pollMs, refresh]);

  const pending = me?.pending_action ?? null;
  useEffect(() => {
    if (!pending) return;
    setNow(Date.now());
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [pending]);

  const submit = useCallback(
    async (draft: Draft): Promise<boolean> => {
      if (!gameId || !pending) return false;
      const built = buildAction(pending, draft);
      if (!built.ok) {
        setErrors(built.errors);
        return false;
      }
      setErrors([]);
      setBusy(true);
      try {
        await postAction(gameId, built.body);
        setMe((m) => (m ? { ...m, pending_action: null } : m));
        setError(null);
        return true;
      } catch (e) {
        setError(e instanceof PlayError ? e.message : "Something went wrong.");
        return false;
      } finally {
        setBusy(false);
      }
    },
    [gameId, pending],
  );

  return {
    me,
    pending,
    error,
    errors,
    busy,
    secondsLeft: pending ? secondsLeft(pending.deadline, now) : null,
    submit,
    refresh,
  };
}
