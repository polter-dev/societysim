import { art } from "../../illustrated/assets";
import { ModelAvatar } from "../ModelAvatar/ModelAvatar";
import "./AvatarMedallion.css";

/** Round model portrait inside the gold flower frame. `size` is the frame width in px; dead players are desaturated and darkened. */
export function AvatarMedallion({ model, label, alive = true, size = 96 }: { model: string; label?: string; alive?: boolean; size?: number }) {
  return (
    <span className="medallion" data-alive={alive} style={{ width: size, height: (size * 158) / 144 }}>
      <span className="medallion-portrait">
        <ModelAvatar model={model} label={label} size={size} alive={alive} />
      </span>
      <img src={art("model_frame.png")} alt="" />
    </span>
  );
}
