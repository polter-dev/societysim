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
  expect(eventIcon({...events[0],type:'death_reveal',phase:'night'})).toBe('werewolf');
  expect(eventIcon({...events[0],type:'death_reveal',phase:'day'})).toBe('voting');
  expect(eventIcon({...events[0],type:'death_reveal',phase:'vote'})).toBe('voting');
  expect(eventIcon({...events[0],type:'phase_start',phase:'night'})).toBe('night');
  expect(eventIcon({...events[0],type:'statement',phase:'day'})).toBe('day');
 });
 it('villager view only illustrates public events, with no wolf picture outside the night',()=>{
  const log=deriveScene(events,events.length-1,'villager').log;
  expect(log.length).toBeGreaterThan(0);
  for(const e of log){
   expect(e.visibility).toBe('public');
   expect(['protection']).not.toContain(eventIcon(e));
   if(e.phase!=='night') expect(eventIcon(e)).not.toBe('werewolf');
  }
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

describe('role claim text',()=>{
 const claim=events.find(e=>e.type==='role_claim')!;
 const text=(meta:object,mode:'villager'|'narrator'='narrator')=>{
  const scene=deriveScene(events,events.length-1,mode);
  return eventPresentation({...claim,private_reasoning:'secret',meta:{...claim.meta,...meta}} as typeof claim,scene).text;
 };
 it('names each claimed role',()=>{
  expect(text({role:'seer'})).toBe('Claims to be the Seer');
  expect(text({role:'doctor'})).toBe('Claims to be the Doctor');
  expect(text({role:'villager'})).toBe('Claims to be a Villager');
  expect(text({role:'wolf'})).toBe('Claims to be a Werewolf');
 });
 it('shows the claim, not the true role, in both modes',()=>{
  const start=events.find(e=>e.type==='game_start')!;
  const truth=start.meta.seats![claim.actor!].role;
  const lie=truth==='seer'?'doctor':'seer';
  for(const mode of ['villager','narrator'] as const){
   const t=text({role:lie},mode);
   expect(t).toBe(`Claims to be the ${lie==='seer'?'Seer':'Doctor'}`);
   expect(t).not.toContain('secret');
  }
 });
 it('falls back neutrally on missing or malformed claims',()=>{
  expect(text({role:undefined})).toBe('Claims a role');
  expect(text({role:42})).toBe('Claims a role');
  expect(text({role:'banana'})).toBe('Claims a role');
  expect(text({role:'toString'})).toBe('Claims a role');
 });
});
