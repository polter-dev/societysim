// Cloudflare Turnstile: load the script once, render an explicit widget, resolve with its token.
const SRC = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";

interface TurnstileApi {
  render: (el: HTMLElement, opts: Record<string, unknown>) => string;
  remove?: (id: string) => void;
}
const api = () => (globalThis as { turnstile?: TurnstileApi }).turnstile;

let loading: Promise<TurnstileApi> | null = null;
function loadScript(): Promise<TurnstileApi> {
  const ready = api();
  if (ready) return Promise.resolve(ready);
  loading ??= new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = SRC;
    s.async = true;
    s.onload = () => {
      const t = api();
      if (t) resolve(t);
      else reject(new Error("Turnstile did not load."));
    };
    s.onerror = () => {
      loading = null;
      reject(new Error("Turnstile did not load."));
    };
    document.head.appendChild(s);
  });
  return loading;
}

/** Render the widget in `el`. Resolves with a token; call `cancel` to remove the widget. */
export function getTurnstileToken(
  el: HTMLElement,
  siteKey: string | undefined = import.meta.env.VITE_TURNSTILE_SITE_KEY as string | undefined,
  onToken?: (token: string | null) => void,
): { token: Promise<string>; cancel: () => void } {
  let id: string | undefined;
  let removed = false;
  const token = (async () => {
    if (!siteKey) throw new Error("VITE_TURNSTILE_SITE_KEY is not set.");
    const t = await loadScript();
    return new Promise<string>((resolve, reject) => {
      if (removed) return reject(new Error("cancelled"));
      id = t.render(el, {
        sitekey: siteKey,
        callback: (value: string) => { if (!removed) { onToken?.(value); resolve(value); } },
        "error-callback": () => { if (!removed) onToken?.(null); reject(new Error("Turnstile check failed.")); },
        "expired-callback": () => { if (!removed) onToken?.(null); reject(new Error("Turnstile check expired.")); },
      });
    });
  })();
  return {
    token,
    cancel: () => {
      removed = true;
      if (id !== undefined) api()?.remove?.(id);
    },
  };
}
