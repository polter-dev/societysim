import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {afterEach,describe,expect,it,vi} from 'vitest';
import LandingPage,{landingStatus} from './LandingPage';
afterEach(()=>vi.unstubAllEnvs());
describe('landing page',()=>{
 it('uses the API quota and availability rather than the export labels',()=>{
  expect(landingStatus({kind:'ready',limits:{remaining_today:2,play_enabled:true}})).toEqual({games:'2 games left today',server:'Play available'});
  expect(landingStatus({kind:'ready',limits:{remaining_today:0,play_enabled:false}})).toEqual({games:'0 games left today',server:'Play paused'});
 });
 it('does not claim a fixed allowance or availability when the server is unknown',()=>{
  for(const kind of ['loading','unavailable','unconfigured'] as const){const labels=landingStatus({kind});expect(labels.games).not.toMatch(/5 games/i);expect(labels.server).not.toBe('Play available');}
 });
 it('renders an accessible menu and preserves the selected batch on Play',()=>{
  vi.stubEnv('VITE_PLAY_API_URL','');
  const html=renderToStaticMarkup(createElement(LandingPage,{href:extra=>'?'+new URLSearchParams({base:'https://batch.test/',...extra})}));
  expect(html).toContain('aria-label="Main menu"');expect(html).toContain('page=modes');expect(html).toContain('base=https%3A%2F%2Fbatch.test%2F');expect(html).toContain('How to play');expect(html).toContain('About');expect(html).toContain('Live play coming soon');expect(html).not.toContain('5 games left today');
 });
});
