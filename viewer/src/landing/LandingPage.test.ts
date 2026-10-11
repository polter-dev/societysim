import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {afterEach,describe,expect,it,vi} from 'vitest';
import LandingPage,{landingStatus} from './LandingPage';
afterEach(()=>vi.unstubAllEnvs());
describe('landing page',()=>{
 it('shows no badge when play is available, and one notice only when it is not',()=>{
  expect(landingStatus({kind:'ready',limits:{remaining_today:40,play_enabled:true}})).toBeNull();
  expect(landingStatus({kind:'ready',limits:{remaining_today:0,play_enabled:true}})).toBe('Daily limit reached');
  expect(landingStatus({kind:'ready',limits:{remaining_today:3,play_enabled:false}})).toBe('Play paused');
  expect(landingStatus({kind:'unavailable'})).toBe('Live play unavailable right now');
  for(const kind of ['loading','unconfigured'] as const) expect(landingStatus({kind})).toBeNull();
 });
 it('links Leaderboard and Research from the front page itself, outside the dialog',()=>{
  vi.stubEnv('VITE_PLAY_API_URL','');
  const html=renderToStaticMarkup(createElement(LandingPage,{href:extra=>'?'+new URLSearchParams({base:'https://batch.test/',...extra})}));
  const links=[...html.matchAll(/<a [^>]*href="([^"]*)"[^>]*>([^<]*)<\/a>/g)].map(m=>[m[2],m[1].replace(/&amp;/g,'&')]);
  const base='base=https%3A%2F%2Fbatch.test%2F';
  expect(links).toContainEqual(['Leaderboard',`?${base}&page=leaderboard`]);
  expect(links).toContainEqual(['Research',`?${base}&page=research`]);
  expect(html).toContain('page=modes');
 });
 it('links Trial Voices to the narrated replay of the demo game',()=>{
  vi.stubEnv('VITE_PLAY_API_URL','');
  const html=renderToStaticMarkup(createElement(LandingPage,{href:extra=>'?'+new URLSearchParams({base:'https://batch.test/',...extra})}));
  const links=[...html.matchAll(/<a [^>]*href="([^"]*)"[^>]*>([^<]*)<\/a>/g)].map(m=>[m[2],m[1].replace(/&amp;/g,'&')]);
  expect(links).toContainEqual(['Trial Voices','?base=https%3A%2F%2Fbatch.test%2F&game=mixed_v2_10p-g0043&mode=narrator']);
 });
 it('renders an accessible menu and preserves the selected batch on Play',()=>{
  vi.stubEnv('VITE_PLAY_API_URL','');
  const html=renderToStaticMarkup(createElement(LandingPage,{href:extra=>'?'+new URLSearchParams({base:'https://batch.test/',...extra})}));
  expect(html).toContain('aria-label="Main menu"');expect(html).toContain('page=modes');expect(html).toContain('base=https%3A%2F%2Fbatch.test%2F');expect(html).toContain('How to play');expect(html).toContain('About');expect(html).not.toContain('Live play coming soon');expect(html).not.toContain('Play available');expect(html).not.toContain('5 games left today');
 });
});
