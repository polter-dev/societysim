import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { JSDOM } from 'jsdom';
import { describe, expect, it } from 'vitest';
import fixture from '../../../fixtures/sample_game.jsonl?raw';
import { parseLog } from '../data/parse';
import { deriveScene, type Mode } from '../scene/deriveScene';
import { IllustratedInspector } from './PlayerProfile';

// The fixture predates the config ids; use the real one so the display name and avatar resolve.
const events = parseLog(fixture.replaceAll('llama-4-maverick', 'llama4-maverick')).events;
const render = (idx: number, mode: Mode, seat: string) =>
  new JSDOM(renderToStaticMarkup(createElement(IllustratedInspector, { scene: deriveScene(events, idx, mode), mode, selected: seat, close: () => {} }))).window.document;

describe('player profile dialog', () => {
  const start = render(0, 'villager', 'p1');

  it('shows the model avatar, the display name and the full id on hover', () => {
    expect(start.querySelector('.profile-head .medallion img.model-avatar-img')?.getAttribute('src')).toContain('avatars/llama4-maverick.webp');
    const model = start.querySelector('.profile-model')!;
    expect(model.textContent).toBe('Llama 4 Maverick');
    expect(model.getAttribute('title')).toBe('llama4-maverick');
  });

  it('gives every section the same plain h3, with no stray "]"', () => {
    const headings = [...start.querySelectorAll('.profile-section > h3')];
    expect(headings.map(h => h.textContent)).toEqual(['Public statement', 'Public activity', 'Decision history']);
    expect(headings.every(h => !h.className && !h.textContent!.includes(']'))).toBe(true);
  });

  it('collapses empty sections to one line, with no empty scroll panes', () => {
    expect(start.querySelectorAll('.profile-pane')).toHaveLength(0);
    expect([...start.querySelectorAll('.profile-empty')].map(p => p.textContent)).toEqual(['Nothing said yet.', '0 statements and 0 votes so far.', 'No decisions yet.']);
  });

  it('keeps the role hidden in villager mode until it is revealed', () => {
    expect(start.querySelector('.profile-role')!.textContent).toBe('◇ role unknown');
    expect(render(0, 'narrator', 'p1').querySelector('.profile-role')!.textContent).toBe('◇ villager');
  });

  it('marks a dead player on the portrait', () => {
    const end = render(events.length - 1, 'villager', 'p0');
    expect(end.querySelector('.medallion')!.getAttribute('data-alive')).toBe('false');
    expect(end.querySelector('.profile-history .profile-pane li')).not.toBeNull();
  });
});
