// @vitest-environment jsdom
import { act, createElement, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { ThemedSelect, type SelectOption } from './ThemedSelect';

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const options: SelectOption[] = [
  { value: 'apple', label: 'Apple' },
  { value: 'banana', label: 'Banana', disabled: true },
  { value: 'cherry', label: 'Cherry' },
  { value: 'cranberry', label: 'Cranberry', icon: null },
];
let root: Root; let box: HTMLElement; let changed: ReturnType<typeof vi.fn<(v: string) => void>>;
function Harness() {
  const [value, setValue] = useState('apple');
  return createElement(ThemedSelect, { label: 'Fruit', value, options, onChange: (v: string) => { changed(v); setValue(v); } });
}
const trigger = () => box.querySelector('button') as HTMLButtonElement;
const list = () => document.querySelector('[role=listbox]');
const active = () => document.getElementById(trigger().getAttribute('aria-activedescendant') ?? '')?.querySelector('.themed-select__label')?.textContent;
const key = (k: string) => act(() => { trigger().dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true })); });

beforeEach(() => {
  changed = vi.fn<(v: string) => void>();
  box = document.createElement('div'); document.body.append(box); root = createRoot(box);
  act(() => root.render(createElement(Harness)));
  trigger().focus();
});
afterEach(() => { act(() => root.unmount()); box.remove(); });

it('exposes the combobox and listbox aria contract', () => {
  const t = trigger();
  expect(t.getAttribute('role')).toBe('combobox');
  expect(t.getAttribute('aria-haspopup')).toBe('listbox');
  expect(t.getAttribute('aria-expanded')).toBe('false');
  expect(t.textContent).toBe('Apple');
  expect(list()).toBeNull();
  act(() => t.click());
  expect(t.getAttribute('aria-expanded')).toBe('true');
  expect(list()!.id).toBe(t.getAttribute('aria-controls'));
  expect(document.querySelector('[aria-selected=true]')!.textContent).toBe('Apple');
  expect(document.querySelector('[data-value=banana]')!.getAttribute('aria-disabled')).toBe('true');
  expect(document.querySelector('[data-value=cranberry] .themed-select__avatar')!.textContent).toBe('C');
  expect(document.querySelector('[data-value=apple] .themed-select__avatar')).toBeNull();
});

it('opens with ArrowDown, skips disabled options, honors Home/End and selects with Enter', async () => {
  await key('ArrowDown');
  expect(list()).not.toBeNull();
  expect(active()).toBe('Apple');
  await key('ArrowDown');
  expect(active()).toBe('Cherry');
  await key('End');
  expect(active()).toBe('Cranberry');
  await key('ArrowDown');
  expect(active()).toBe('Cranberry');
  await key('Home');
  expect(active()).toBe('Apple');
  await key('ArrowDown');
  await key('Enter');
  expect(changed).toHaveBeenCalledWith('cherry');
  expect(list()).toBeNull();
  expect(trigger().textContent).toBe('Cherry');
  expect(document.activeElement).toBe(trigger());
});

it('type-ahead jumps to matching enabled options', async () => {
  await key(' ');
  await key('c');
  expect(active()).toBe('Cherry');
  await key('c');
  expect(active()).toBe('Cranberry');
  await key('b');
  expect(active()).toBe('Cranberry');
});

it('Escape and outside clicks close without changing the value', async () => {
  await key('Enter');
  await key('ArrowDown');
  await key('Escape');
  expect(list()).toBeNull();
  expect(document.activeElement).toBe(trigger());
  act(() => trigger().click());
  expect(list()).not.toBeNull();
  act(() => { document.body.dispatchEvent(new Event('pointerdown', { bubbles: true })); });
  expect(list()).toBeNull();
  expect(changed).not.toHaveBeenCalled();
});

it('ignores clicks on disabled options and picks enabled ones', () => {
  act(() => trigger().click());
  act(() => (document.querySelector('[data-value=banana]') as HTMLElement).click());
  expect(changed).not.toHaveBeenCalled();
  expect(list()).not.toBeNull();
  act(() => (document.querySelector('[data-value=cranberry]') as HTMLElement).click());
  expect(changed).toHaveBeenCalledWith('cranberry');
  expect(list()).toBeNull();
});

it('does not open when disabled', () => {
  act(() => root.render(createElement(ThemedSelect, { label: 'Fruit', value: 'apple', options, onChange: changed, disabled: true })));
  act(() => trigger().click());
  expect(list()).toBeNull();
});
