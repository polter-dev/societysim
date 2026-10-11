import { useEffect, useState } from "react";
import { avatarFor } from "../../data/models";
import "./ModelAvatar.css";
import { HUMAN, labelFor, type ModelsCatalog } from "../../settings/setup";

let catalog: Promise<ModelsCatalog | null> | null = null;
const loadCatalog = () =>
  (catalog ??= fetch(new URL("models_catalog.json", new URL(import.meta.env.BASE_URL, location.href)))
    .then((r) => (r.ok ? r.json() : null))
    .then((c) => (Array.isArray(c?.models) ? (c as ModelsCatalog) : null))
    .catch(() => null));

/** Display name for a model id ("Gemma 4 26B · Google") from the site's models_catalog.json; the id until it loads or if unknown. */
export function useModelLabel(): (model: string) => string {
  const [cat, setCat] = useState<ModelsCatalog | null>(null);
  useEffect(() => {
    let live = true;
    loadCatalog().then((c) => live && setCat(c));
    return () => {
      live = false;
    };
  }, []);
  return (model) => (model === HUMAN ? "Human" : cat ? labelFor(cat, model) : model);
}

/** Monogram for a model with no portrait: the model's initial ("You" for the human seat). */
export const monogram = (model: string, label = model) => (model === HUMAN ? "You" : label.charAt(0).toUpperCase());

/** Round portrait for a model or the human seat; falls back to a monogram. Eliminated players are dimmed and grayscale. */
export function ModelAvatar({ model, label, size = 32, alive = true }: { model: string; label?: string; size?: number; alive?: boolean }) {
  const src = avatarFor(model);
  const name = label ?? model;
  const style = { width: size, height: size };
  return src ? (
    <img className="model-avatar-img" data-alive={alive} src={src} alt={name} width={size} height={size} loading="lazy" style={style} />
  ) : (
    <span className="model-avatar-img" data-alive={alive} data-placeholder="true" aria-hidden="true" style={style}>
      {monogram(model, name)}
    </span>
  );
}
