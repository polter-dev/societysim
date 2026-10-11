import {createElement} from 'react';
import {renderToStaticMarkup} from 'react-dom/server';
import {describe,expect,it} from 'vitest';
import fixture from '../../../fixtures/sample_game.jsonl?raw';
import {parseLog} from '../data/parse';
import {deriveScene} from '../scene/deriveScene';
import {eventPresentation,playerStatistics,eventIcon} from './presentation';
import {IllustratedShell,IllustratedInspector} from './IllustratedView';
const events=parseLog(fixture).events;
describe('illustrated replay data',()=>{
 it('uses public event types and phase for detail illustrations',()=>{
  expect(eventIcon({...events[0],type:'seer_check'})).toBe('investigation');
  expect(eventIcon({...events[0],type:'doctor_save'})).toBe('protection');
  expect(eventIcon({...events[0],type:'vote'})).toBe('voting');
  expect(eventIcon({...events[0],type:'night_kill'})).toBe('werewolf');
  expect(eventIcon({...events[0],type:'death_reveal'})).toBe('werewolf');
  expect(eventIcon({...events[0],type:'phase_start',phase:'night'})).toBe('night');
  expect(eventIcon({...events[0],type:'statement',phase:'day'})).toBe('day');
 });
 it('renders the logged roster, including a tenth seat without prototype agents',()=>{
  const start=events.find(e=>e.type==='game_start')!;
  const seats={...start.meta.seats!,p7:{name:'Actual Seven',model:'logged-model',color:'#123456',role:'villager' as const},p8:{name:'Actual Eight',model:'logged-model',color:'#123456',role:'villager' as const},p9:{name:'Actual Nine',model:'logged-model',color:'#123456',role:'villager' as const}};
  const scene=deriveScene([{...start,schema_version:3,meta:{...start.meta,seats}}],0,'villager');
  const html=renderToStaticMarkup(createElement(IllustratedShell,{scene,onSelect:()=>{}}));
  expect(html.match(/class="agent-button"/g)).toHaveLength(10);
  expect(html).toContain('Actual Nine');expect(html).not.toContain('Aria');
 });
 it('counts logged actions and freezes survival at death',()=>{
  const scene=deriveScene(events,events.length-1,'narrator');
  const stats=playerStatistics(scene,'p0');
  expect(stats.statements).toBe(scene.log.filter(e=>e.actor==='p0'&&e.type==='statement').length);
  expect(stats.days).toBe(scene.log.find(e=>e.target==='p0'&&e.type==='death_reveal')!.day);
 });
 it('uses exact recorded speech and does not manufacture notes',()=>{
  const scene=deriveScene(events,events.length-1,'narrator');
  const statement=scene.log.find(e=>e.type==='statement')!;
  expect(eventPresentation(statement,scene).text).toBe(statement.public_text);
  expect(eventPresentation(statement,scene).result).toBe(null);
 });
 it('inspector changes from private logged reasoning to public data in villager mode',()=>{
  const i=events.findIndex(e=>!!e.private_reasoning&&e.actor==='p0');
  const reason=events[i].private_reasoning!;
  const render=(mode:'narrator'|'villager')=>renderToStaticMarkup(createElement(IllustratedInspector,{scene:deriveScene(events,i,mode),mode,selected:'p0',close:()=>{}}));
  expect(render('narrator')).toContain(reason.replaceAll('&','&amp;').replaceAll('"','&quot;').replaceAll("'",'&#x27;'));
  expect(render('villager')).not.toContain(reason);
 });
});

