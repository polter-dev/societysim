import { useCallback, useEffect, useRef, useState, type RefObject } from "react";
import { getTurnstileToken } from "./turnstile";

/**
 * A Turnstile widget in `widget` and its current token. A token is single use: call `reset()` after
 * any rejected start to remove the widget and render a fresh one. `reset` clears the token at once,
 * so Start stays disabled until the new check passes. Set `enabled` false to skip the widget.
 */
export function useTurnstile(
  widget: RefObject<HTMLElement | null>,
  onError: (message: string) => void,
  enabled = true,
) {
  const [token, setToken] = useState<string | null>(null);
  const [round, setRound] = useState(0);
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;

  useEffect(() => {
    if (!enabled || !widget.current) return;
    let active = true;
    const t = getTurnstileToken(widget.current, undefined, (v) => active && setToken(v));
    t.token.then(
      (v) => active && setToken(v),
      (e: Error) => active && e.message !== "cancelled" && onErrorRef.current(e.message),
    );
    return () => {
      active = false;
      t.cancel();
    };
  }, [widget, enabled, round]);

  const reset = useCallback(() => {
    setToken(null);
    setRound((r) => r + 1);
  }, []);
  return { token, reset };
}
