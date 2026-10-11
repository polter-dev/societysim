import { useEffect, useState } from "react";
import { probePlay, type PlayStatus } from "./api";
import { playEnabled } from "./config";

/** Probes the play server once on mount. With no VITE_PLAY_API_URL it stays "checking" (callers gate on playEnabled first). */
export function usePlayStatus(): PlayStatus {
  const [status, setStatus] = useState<PlayStatus>({ kind: "checking" });
  useEffect(() => {
    if (!playEnabled()) return;
    let live = true;
    probePlay().then((s) => live && setStatus(s));
    return () => {
      live = false;
    };
  }, []);
  return status;
}
