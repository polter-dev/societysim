export interface StepControlsProps {
  /** 0-based index of the current beat. */
  pos: number;
  count: number;
  live: boolean;
  atLatest: boolean;
  newCount: number;
  onPrev: () => void;
  onNext: () => void;
  onLive: () => void;
}

/** Classes: step-controls, step-prev, step-next, step-label, step-live (styled in controls.css). */
export function StepControls(p: StepControlsProps) {
  return (
    <div className="step-controls" role="group" aria-label="Step through the game">
      <button type="button" className="step-prev" aria-label="Previous beat" disabled={p.pos <= 0} onClick={p.onPrev}>
        ◀
      </button>
      <span className="step-label" aria-live="polite">
        beat {p.count === 0 ? 0 : p.pos + 1} / {p.count}
      </span>
      <button type="button" className="step-next" aria-label="Next beat" disabled={p.atLatest} onClick={p.onNext}>
        ▶
      </button>
      {p.live && !p.atLatest && (
        <button type="button" className="step-live" onClick={p.onLive}>
          Back to live ({p.newCount} new)
        </button>
      )}
    </div>
  );
}
