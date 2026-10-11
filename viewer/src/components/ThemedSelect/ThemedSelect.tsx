// In-page select that matches the illustrated screens (WAI-ARIA select-only combobox).
// Focus stays on the trigger; the open list is portaled to <body> so frames never clip it.
import { useEffect, useId, useLayoutEffect, useRef, useState, type CSSProperties, type KeyboardEvent } from 'react';
import { createPortal } from 'react-dom';
import './ThemedSelect.css';

export interface SelectOption {
  value: string;
  label: string;
  disabled?: boolean;
  /** Tooltip, e.g. why a model is unavailable. */
  title?: string;
  /** Avatar URL; null shows a monogram. Leave undefined for no avatar slot. */
  icon?: string | null;
}

export function ThemedSelect({ label, value, options, onChange, disabled = false, placeholder = '', className = '' }: {
  label: string; value: string; options: SelectOption[]; onChange: (value: string) => void;
  disabled?: boolean; placeholder?: string; className?: string;
}) {
  const id = useId();
  const trigger = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const typed = useRef({ text: '', at: 0 });
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [place, setPlace] = useState<CSSProperties>({});
  const [up, setUp] = useState(false);
  const selected = options.findIndex(o => o.value === value);
  const current = options[selected];
  const enabled = (i: number) => i >= 0 && i < options.length && !options[i].disabled;

  const show = (index = selected) => {
    if (disabled) return;
    setActive(enabled(index) ? index : options.findIndex(o => !o.disabled));
    setOpen(true);
  };
  const close = () => { setOpen(false); trigger.current?.focus(); };
  const pick = (i: number) => {
    if (!enabled(i)) return;
    if (options[i].value !== value) onChange(options[i].value);
    close();
  };
  // Next enabled option from `from` in direction `dir`; stays put at either end.
  const step = (from: number, dir: 1 | -1) => {
    for (let i = from + dir; i >= 0 && i < options.length; i += dir) if (enabled(i)) return i;
    return from;
  };

  useLayoutEffect(() => {
    if (!open) return;
    const placeList = () => {
      const r = trigger.current!.getBoundingClientRect();
      const below = window.innerHeight - r.bottom, above = r.top;
      const flip = below < 220 && above > below;
      setUp(flip);
      setPlace({
        right: Math.max(8, window.innerWidth - r.right), minWidth: r.width, maxWidth: Math.max(r.width, r.right - 8),
        maxHeight: Math.min(320, (flip ? above : below) - 16),
        ...(flip ? { bottom: window.innerHeight - r.top + 6 } : { top: r.bottom + 6 }),
      });
    };
    placeList();
    window.addEventListener('resize', placeList);
    window.addEventListener('scroll', placeList, true);
    return () => { window.removeEventListener('resize', placeList); window.removeEventListener('scroll', placeList, true); };
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const outside = (e: PointerEvent) => {
      const t = e.target as Node;
      if (!trigger.current?.contains(t) && !list.current?.contains(t)) setOpen(false);
    };
    document.addEventListener('pointerdown', outside);
    return () => document.removeEventListener('pointerdown', outside);
  }, [open]);

  useEffect(() => { if (open && active >= 0) list.current?.children[active]?.scrollIntoView?.({ block: 'nearest' }); }, [open, active]);

  const typeAhead = (key: string) => {
    const now = Date.now();
    const typedText = (now - typed.current.at < 600 ? typed.current.text : '') + key.toLowerCase();
    typed.current = { text: typedText, at: now };
    // Repeating one letter cycles through the options that start with it, like a native select.
    const text = typedText.split('').every(c => c === typedText[0]) ? typedText[0] : typedText;
    const start = text.length === 1 ? active + 1 : active;
    for (let k = 0; k < options.length; k++) {
      const i = (Math.max(start, 0) + k) % options.length;
      if (enabled(i) && options[i].label.toLowerCase().startsWith(text)) return open ? setActive(i) : show(i);
    }
  };

  const onKeyDown = (e: KeyboardEvent<HTMLButtonElement>) => {
    const keys: Record<string, () => void> = open ? {
      ArrowDown: () => setActive(step(active, 1)),
      ArrowUp: () => setActive(step(active, -1)),
      Home: () => setActive(step(-1, 1)),
      End: () => setActive(step(options.length, -1)),
      Enter: () => pick(active),
      ' ': () => (typed.current.text && Date.now() - typed.current.at < 600 ? typeAhead(' ') : pick(active)),
      Escape: close,
    } : {
      ArrowDown: () => show(), ArrowUp: () => show(), Enter: () => show(), ' ': () => show(),
      Home: () => show(step(-1, 1)), End: () => show(step(options.length, -1)),
    };
    if (e.key === 'Tab') { setOpen(false); return; }
    const run = keys[e.key];
    if (run) { e.preventDefault(); run(); return; }
    if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && !e.altKey) { e.preventDefault(); typeAhead(e.key); }
  };

  const avatar = (o: SelectOption) => o.icon === undefined ? null
    : <span className="themed-select__avatar" aria-hidden="true">{o.icon ? <img src={o.icon} alt="" /> : o.label.slice(0, 1)}</span>;

  return <span className={`themed-select ${className}`}>
    <button ref={trigger} type="button" role="combobox" className="themed-select__trigger" aria-label={label}
      aria-haspopup="listbox" aria-expanded={open} aria-controls={`${id}-list`} disabled={disabled}
      aria-activedescendant={open && active >= 0 ? `${id}-${active}` : undefined}
      onClick={() => (open ? setOpen(false) : show())} onKeyDown={onKeyDown}>
      <span className="themed-select__value">{current ? current.label : placeholder}</span>
      <span className="themed-select__chevron" aria-hidden="true" />
    </button>
    {open && createPortal(
      <ul ref={list} id={`${id}-list`} role="listbox" aria-label={label} tabIndex={-1}
        className={`themed-select__list ${up ? 'themed-select__list--up' : ''}`} style={place}
        onMouseDown={e => e.preventDefault()}>
        {options.map((o, i) => <li key={o.value} id={`${id}-${i}`} role="option" data-value={o.value}
          aria-selected={i === selected} aria-disabled={o.disabled || undefined} title={o.title}
          className={i === active ? 'themed-select__option is-active' : 'themed-select__option'}
          onMouseMove={() => enabled(i) && i !== active && setActive(i)} onClick={() => pick(i)}>
          <span className="themed-select__check" aria-hidden="true" />{avatar(o)}<span className="themed-select__label">{o.label}</span>
        </li>)}
      </ul>, document.body)}
  </span>;
}
